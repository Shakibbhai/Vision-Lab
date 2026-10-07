from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.tracking.boxmot_utils import (
    create_bytetrack_tracker,
    ensure_bytetrack_outputs,
    resolve_tracker_config_path,
)
from app.services.detection.yolo_person_classes import resolve_person_class_ids
from app.services.detection.yolo_threadsafe import build_locked_callable
from app.services.utils.zone_geometry import region_points_from_polygon

logger = logging.getLogger(__name__)

_STATE_TTL_SECONDS = 120.0


@dataclass(slots=True)
class _CameraHeatmapState:
    tracker: Any
    frame_width: int
    frame_height: int
    heatmap: Any
    prev_point_by_track: dict[int, tuple[float, float, int]] = field(default_factory=dict)
    last_frame_token: str = ""
    updated_at_monotonic: float = 0.0
    total_segments: int = 0
    active_tracks: int = 0


class TrajectoryHeatmapService:
    """Build object trajectory-density heatmap overlays using YOLO + BoxMOT."""

    def __init__(self) -> None:
        self._available = False
        self._init_error: str | None = None
        self._yolo = None
        self._predict = None
        self._cv2 = None
        self._np = None
        self._create_tracker = None
        self._tracker_config_path: Path | None = None
        self._person_class_ids: list[int] = [0]

        self._camera_states: dict[tuple[int, str], _CameraHeatmapState] = {}
        self._camera_locks: dict[tuple[int, str], threading.Lock] = {}
        self._state_guard = threading.Lock()

        if not settings.analytics_use_yolo:
            self._init_error = "YOLO analytics is disabled"
            return

        try:
            from ultralytics import YOLO  # type: ignore
            from boxmot import create_tracker  # type: ignore
            import boxmot  # type: ignore
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            self._yolo = YOLO(settings.yolo_model_path)
            self._yolo.to(settings.boxmot_device)
            self._predict = build_locked_callable(self._yolo.predict)
            self._create_tracker = create_tracker
            self._tracker_config_path = resolve_tracker_config_path(boxmot, "bytetrack")
            self._cv2 = cv2
            self._np = np
            self._person_class_ids = resolve_person_class_ids(self._yolo)
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._init_error = str(exc)
            logger.warning("Failed to initialize trajectory heatmap service: %s", exc)

    def is_available(self) -> bool:
        return (
            self._available
            and self._yolo is not None
            and self._predict is not None
            and self._cv2 is not None
            and self._np is not None
            and self._create_tracker is not None
            and self._tracker_config_path is not None
        )

    def error_message(self) -> str:
        return self._init_error or "Trajectory heatmap service is unavailable"

    def render_overlay(
        self,
        camera_id: int,
        frame_token: str,
        image: Any,
        zone: Any | None = None,
    ) -> dict[str, Any]:
        if not self.is_available() or image is None:
            return {
                "frame": image,
                "active_tracks": 0,
                "total_segments": 0,
                "heatmap_peak": 0.0,
                "nonzero_ratio": 0.0,
            }

        frame_h, frame_w = image.shape[:2]
        if frame_w <= 0 or frame_h <= 0:
            return {
                "frame": image,
                "active_tracks": 0,
                "total_segments": 0,
                "heatmap_peak": 0.0,
                "nonzero_ratio": 0.0,
            }

        zone_polygons = self._resolve_zone_polygons(zone, frame_w, frame_h)
        zone_boxes = self._resolve_zone_boxes(zone_polygons)
        zone_scope_key = self._zone_scope_key(zone)
        state_key = (int(camera_id), zone_scope_key)
        now = time.monotonic()
        self._prune_stale_states(now)

        lock = self._get_camera_lock(state_key)
        with lock:
            state = self._get_or_create_state(state_key, frame_w, frame_h, now)
            if state.last_frame_token != frame_token:
                self._update_state_with_frame(state, image, zone_boxes, frame_token, now)

            rendered = self._compose_heatmap_overlay(image, state.heatmap, zone_boxes, state.active_tracks)
            peak = float(self._np.max(state.heatmap)) if state.heatmap.size > 0 else 0.0
            nonzero_ratio = (
                float(self._np.count_nonzero(state.heatmap)) / float(state.heatmap.size)
                if state.heatmap.size > 0
                else 0.0
            )
            return {
                "frame": rendered,
                "active_tracks": int(state.active_tracks),
                "total_segments": int(state.total_segments),
                "heatmap_peak": peak,
                "nonzero_ratio": nonzero_ratio,
            }

    def _resolve_zone_polygons(
        self,
        zone: Any | None,
        frame_w: int,
        frame_h: int,
    ) -> list[list[tuple[int, int]]]:
        if zone is None:
            return []
        return region_points_from_polygon(
            getattr(zone, "polygon", None),
            frame_w,
            frame_h,
            use_default_on_invalid=False,
        )

    def _resolve_zone_boxes(
        self,
        zone_polygons: list[list[tuple[int, int]]],
    ) -> list[tuple[int, int, int, int]]:
        boxes: list[tuple[int, int, int, int]] = []
        for polygon in zone_polygons:
            if len(polygon) < 3:
                continue
            xs = [int(point[0]) for point in polygon]
            ys = [int(point[1]) for point in polygon]
            if not xs or not ys:
                continue
            x1 = min(xs)
            y1 = min(ys)
            x2 = max(xs)
            y2 = max(ys)
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append((x1, y1, x2, y2))
        return boxes

    def _prune_stale_states(self, now: float) -> None:
        with self._state_guard:
            stale_keys = [
                state_key
                for state_key, state in self._camera_states.items()
                if now - state.updated_at_monotonic > _STATE_TTL_SECONDS
            ]
            for state_key in stale_keys:
                self._camera_states.pop(state_key, None)
                self._camera_locks.pop(state_key, None)

    def _get_camera_lock(self, state_key: tuple[int, str]) -> threading.Lock:
        with self._state_guard:
            lock = self._camera_locks.get(state_key)
            if lock is None:
                lock = threading.Lock()
                self._camera_locks[state_key] = lock
            return lock

    def _get_or_create_state(
        self,
        state_key: tuple[int, str],
        frame_w: int,
        frame_h: int,
        now: float,
    ) -> _CameraHeatmapState:
        state = self._camera_states.get(state_key)
        if (
            state is not None
            and state.frame_width == frame_w
            and state.frame_height == frame_h
        ):
            state.updated_at_monotonic = now
            return state

        tracker = create_bytetrack_tracker(
            create_tracker_fn=self._create_tracker,
            config_path=self._tracker_config_path,
            device=settings.boxmot_device,
            half=False,
            per_class=False,
            track_thresh=max(0.0, float(settings.bytetrack_track_thresh)),
            match_thresh=max(0.0, float(settings.bytetrack_match_thresh)),
            track_buffer=max(1, int(settings.bytetrack_track_buffer)),
        )
        state = _CameraHeatmapState(
            tracker=tracker,
            frame_width=frame_w,
            frame_height=frame_h,
            heatmap=self._np.zeros((frame_h, frame_w), dtype=self._np.float32),
            updated_at_monotonic=now,
        )
        self._camera_states[state_key] = state
        return state

    def _zone_scope_key(self, zone: Any | None) -> str:
        if zone is None:
            return "all"

        zone_id = getattr(zone, "id", None)
        if zone_id is not None:
            return f"zone:{int(zone_id)}"

        polygon = getattr(zone, "polygon", None)
        if polygon is None:
            return "custom:none"
        return f"custom:{abs(hash(str(polygon)))}"

    def _update_state_with_frame(
        self,
        state: _CameraHeatmapState,
        image: Any,
        zone_boxes: list[tuple[int, int, int, int]],
        frame_token: str,
        now: float,
    ) -> None:
        detections = self._detect_people(image)
        tracked_raw = state.tracker.update(detections, image)
        tracked = ensure_bytetrack_outputs(tracked_raw, self._np)
        current_points: dict[int, tuple[float, float, int]] = {}
        segments_added = 0
        active_tracks = 0

        for row in tracked:
            try:
                if len(row) < 5:
                    continue
                x1 = float(row[0])
                y1 = float(row[1])
                x2 = float(row[2])
                y2 = float(row[3])
                track_id = int(row[4])
            except Exception:  # noqa: BLE001
                continue

            if track_id < 0 or x2 <= x1 or y2 <= y1:
                continue

            px = max(0.0, min(float(state.frame_width - 1), (x1 + x2) / 2.0))
            py = max(0.0, min(float(state.frame_height - 1), y2))
            if zone_boxes:
                box_index = _point_in_any_box(px, py, zone_boxes)
                if box_index < 0:
                    continue
            else:
                box_index = 0

            active_tracks += 1
            current_point = (px, py, box_index)
            prev_point = state.prev_point_by_track.get(track_id)
            if prev_point is not None and prev_point[2] == box_index:
                self._cv2.line(
                    state.heatmap,
                    (int(round(prev_point[0])), int(round(prev_point[1]))),
                    (int(round(current_point[0])), int(round(current_point[1]))),
                    1.0,
                    2,
                    lineType=self._cv2.LINE_AA,
                )
                segments_added += 1

            current_points[track_id] = current_point

        state.prev_point_by_track = current_points
        state.active_tracks = active_tracks
        state.total_segments += segments_added
        state.last_frame_token = frame_token
        state.updated_at_monotonic = now

    def _detect_people(self, image: Any) -> Any:
        predict_kwargs: dict[str, Any] = {
            "conf": settings.yolo_confidence,
            "iou": settings.yolo_iou,
            "verbose": settings.yolo_verbose,
        }
        if self._person_class_ids:
            predict_kwargs["classes"] = self._person_class_ids

        try:
            results = self._predict(image, **predict_kwargs)
            boxes = results[0].boxes if results and results[0].boxes is not None else None
            if boxes is None:
                return self._np.empty((0, 6), dtype=self._np.float32)

            xyxy = boxes.xyxy.cpu().numpy().astype(self._np.float32)
            conf = boxes.conf.cpu().numpy().astype(self._np.float32)
            cls = boxes.cls.cpu().numpy().astype(self._np.float32)
            if len(xyxy) == 0:
                return self._np.empty((0, 6), dtype=self._np.float32)

            if self._person_class_ids:
                mask = self._np.isin(cls.astype(self._np.int32), self._person_class_ids)
                xyxy = xyxy[mask]
                conf = conf[mask]
                cls = cls[mask]
                if len(xyxy) == 0:
                    return self._np.empty((0, 6), dtype=self._np.float32)

            return self._np.column_stack((xyxy, conf, cls)).astype(self._np.float32)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Trajectory heatmap detection failed: %s", exc)
            return self._np.empty((0, 6), dtype=self._np.float32)

    def _compose_heatmap_overlay(
        self,
        frame: Any,
        heatmap: Any,
        zone_boxes: list[tuple[int, int, int, int]],
        active_tracks: int,
    ) -> Any:
        output = frame.copy()
        if heatmap is not None and heatmap.size > 0 and float(self._np.max(heatmap)) > 0.0:
            blurred = self._cv2.GaussianBlur(heatmap, (0, 0), sigmaX=7, sigmaY=7)
            normalized = self._cv2.normalize(blurred, None, 0, 255, self._cv2.NORM_MINMAX)
            heat_u8 = normalized.astype(self._np.uint8, copy=False)
            colored = self._cv2.applyColorMap(heat_u8, self._cv2.COLORMAP_JET)
            blended = self._cv2.addWeighted(output, 0.62, colored, 0.38, 0.0)
            mask = heat_u8 > 0
            output = self._np.where(mask[..., None], blended, output)

        if zone_boxes:
            for x1, y1, x2, y2 in zone_boxes:
                self._cv2.rectangle(
                    output,
                    (int(x1), int(y1)),
                    (int(x2), int(y2)),
                    (255, 255, 255),
                    2,
                    lineType=self._cv2.LINE_AA,
                )

        text = f"Trajectory Heatmap | Active Tracks: {active_tracks}"
        self._cv2.putText(
            output,
            text,
            (10, 22),
            self._cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (245, 245, 245),
            2,
            self._cv2.LINE_AA,
        )
        return output


_trajectory_heatmap_service: TrajectoryHeatmapService | None = None


def get_trajectory_heatmap_service() -> TrajectoryHeatmapService:
    global _trajectory_heatmap_service
    if _trajectory_heatmap_service is None:
        _trajectory_heatmap_service = TrajectoryHeatmapService()
    return _trajectory_heatmap_service


def _point_in_any_box(
    x: float,
    y: float,
    boxes: list[tuple[int, int, int, int]],
) -> int:
    for index, (x1, y1, x2, y2) in enumerate(boxes):
        if x1 <= x <= x2 and y1 <= y <= y2:
            return index
    return -1
