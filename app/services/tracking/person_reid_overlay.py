from __future__ import annotations

import logging
import threading
import time
from collections import deque
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

_STATE_TTL_SECONDS = 180.0


@dataclass(slots=True)
class _CameraTrackState:
    tracker: Any
    frame_width: int
    frame_height: int
    tracked_rows: list[dict[str, Any]] = field(default_factory=list)
    last_frame_token: str = ""
    updated_at_monotonic: float = 0.0
    # Re-ID State Memory
    local_to_global: dict[int, int] = field(default_factory=dict)
    global_exemplars: dict[int, list[Any]] = field(default_factory=dict)
    track_age: dict[int, int] = field(default_factory=dict)
    next_global_id: int = 1


class PersonReidOverlayService:
    """Realtime person tracking and Re-ID overlay that renders consistent global IDs."""

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
        self._personvit_encoder: Any = None
        self._reid_threshold: float = 0.44

        self._camera_states: dict[tuple[int, str], _CameraTrackState] = {}
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
            self._reid_threshold = float(getattr(settings, "personvit_similarity_threshold", 0.44))

            # Initialize PersonViT Re-ID Encoder if enabled
            self._init_personvit_encoder()
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._init_error = str(exc)
            logger.warning("Failed to initialize person reid overlay service: %s", exc)

    def _init_personvit_encoder(self) -> None:
        """Load PersonViT Re-ID feature extractor with candidate paths."""
        try:
            from app.services.tracking.person_tracking import PersonViTEncoder
            configured_path = Path(settings.personvit_reid_weights)
            candidates = [
                configured_path,
                Path.cwd() / "checkpoint0260.pth",
                Path.cwd() / "vit_base_checkpoint0260.pth",
                Path(r"e:\OneDrive\source code java all\OneDrive\Desktop\8th semister project\Percepta-ReID\vit_base_checkpoint0260.pth"),
                Path(r"e:\OneDrive\source code java all\OneDrive\Desktop\8th semister project\Percepta-ReID\checkpoint0260.pth"),
                Path("/workspace/Percepta-ReID/vit_base_checkpoint0260.pth"),
                Path("/workspace/Percepta-ReID/checkpoint0260.pth"),
            ]
            found_ckpt = None
            for cand in candidates:
                if cand and cand.exists():
                    found_ckpt = cand
                    break

            if found_ckpt is not None:
                self._personvit_encoder = PersonViTEncoder(
                    checkpoint_path=found_ckpt,
                    device=settings.boxmot_device,
                    trust_checkpoint=True,
                )
                logger.info("PersonReidOverlayService: PersonViT encoder active from %s", found_ckpt)
            else:
                logger.info("PersonReidOverlayService: Re-ID weights not found locally (will use spatial tracking until GPU/weights connected)")
                self._personvit_encoder = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("PersonReidOverlayService: PersonViT encoder init notice: %s", exc)
            self._personvit_encoder = None

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
        return self._init_error or "Person reid overlay service is unavailable"

    def render_overlay(
        self,
        camera_id: int,
        frame_token: str,
        image: Any,
        zone: Any | None = None,
    ) -> dict[str, Any]:
        if not self.is_available() or image is None:
            return {"frame": image, "active_tracks": 0}

        frame_h, frame_w = image.shape[:2]
        if frame_w <= 0 or frame_h <= 0:
            return {"frame": image, "active_tracks": 0}

        zone_scope_key = self._zone_scope_key(zone)
        state_key = (int(camera_id), zone_scope_key)
        zone_polygons = self._resolve_zone_polygons(zone, frame_w, frame_h)
        now = time.monotonic()
        self._prune_stale_states(now)

        lock = self._get_camera_lock(state_key)
        with lock:
            state = self._get_or_create_state(state_key, frame_w, frame_h, now)
            if state.last_frame_token != frame_token:
                state.tracked_rows = self._track_people(state, image, zone_polygons)
                state.last_frame_token = frame_token
                state.updated_at_monotonic = now

            rendered = self._draw_tracked_rows(image, state.tracked_rows, zone_polygons)
            return {"frame": rendered, "active_tracks": len(state.tracked_rows)}

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
    ) -> _CameraTrackState:
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
        state = _CameraTrackState(
            tracker=tracker,
            frame_width=frame_w,
            frame_height=frame_h,
            updated_at_monotonic=now,
        )
        self._camera_states[state_key] = state
        return state

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
            logger.warning("Person reid overlay detection failed: %s", exc)
            return self._np.empty((0, 6), dtype=self._np.float32)

    def _track_people(
        self,
        state: _CameraTrackState,
        image: Any,
        zone_polygons: list[list[tuple[int, int]]],
    ) -> list[dict[str, Any]]:
        detections = self._detect_people(image)
        tracked_raw = state.tracker.update(detections, image)
        tracked = ensure_bytetrack_outputs(tracked_raw, self._np)
        rows: list[dict[str, Any]] = []

        crops_to_encode = []
        valid_track_info = []

        for row in tracked:
            try:
                if len(row) < 5:
                    continue
                x1 = int(round(float(row[0])))
                y1 = int(round(float(row[1])))
                x2 = int(round(float(row[2])))
                y2 = int(round(float(row[3])))
                track_id = int(row[4])
            except Exception:  # noqa: BLE001
                continue

            x1 = max(0, min(state.frame_width - 1, x1))
            y1 = max(0, min(state.frame_height - 1, y1))
            x2 = max(0, min(state.frame_width - 1, x2))
            y2 = max(0, min(state.frame_height - 1, y2))
            if track_id < 0 or x2 <= x1 or y2 <= y1:
                continue

            foot_x = (x1 + x2) / 2.0
            foot_y = float(y2)
            if zone_polygons and not _point_in_any_polygon(foot_x, foot_y, zone_polygons):
                continue

            crop = image[y1:y2, x1:x2]
            crops_to_encode.append(crop)
            valid_track_info.append((x1, y1, x2, y2, track_id))

        # Extract embeddings if PersonViT encoder is active
        embeddings = None
        if self._personvit_encoder is not None and crops_to_encode:
            try:
                embeddings = self._personvit_encoder.encode_crops_bgr(crops_to_encode)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Encoder notice: %s", exc)

        # Match or Assign Consistent Global Re-ID Identity
        for idx, (x1, y1, x2, y2, local_track_id) in enumerate(valid_track_info):
            state.track_age[local_track_id] = state.track_age.get(local_track_id, 0) + 1
            embedding = embeddings[idx] if embeddings is not None and idx < len(embeddings) else None

            global_id = state.local_to_global.get(local_track_id)

            if global_id is None:
                if embedding is not None and state.global_exemplars:
                    best_match_id = None
                    best_sim = -1.0
                    for gid, exemplars in state.global_exemplars.items():
                        for ex in exemplars:
                            sim = float(self._np.dot(embedding, ex))
                            if sim > best_sim:
                                best_sim = sim
                                best_match_id = gid

                    if best_match_id is not None and best_sim >= self._reid_threshold:
                        global_id = best_match_id
                    else:
                        global_id = state.next_global_id
                        state.next_global_id += 1
                else:
                    global_id = state.next_global_id
                    state.next_global_id += 1

                state.local_to_global[local_track_id] = global_id
            else:
                # Deferred Re-Association for recently spawned IDs (first 15 frames)
                if state.track_age[local_track_id] <= 15 and embedding is not None:
                    for earlier_gid, exemplars in state.global_exemplars.items():
                        if earlier_gid >= global_id:
                            continue
                        for ex in exemplars:
                            sim = float(self._np.dot(embedding, ex))
                            if sim >= self._reid_threshold:
                                state.local_to_global[local_track_id] = earlier_gid
                                global_id = earlier_gid
                                break

            # Store up to 8 diverse angle/pose templates per person
            if embedding is not None:
                if global_id not in state.global_exemplars:
                    state.global_exemplars[global_id] = [embedding]
                else:
                    exemplars = state.global_exemplars[global_id]
                    if len(exemplars) < 8:
                        if all(float(self._np.dot(embedding, ex)) < 0.90 for ex in exemplars):
                            exemplars.append(embedding)

            rows.append({
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "track_id": global_id,
                "local_id": local_track_id,
            })

        return rows

    def _draw_tracked_rows(
        self,
        frame: Any,
        tracked_rows: list[dict[str, Any]],
        zone_polygons: list[list[tuple[int, int]]],
    ) -> Any:
        output = frame.copy()
        frame_h, frame_w = output.shape[:2]

        for item in tracked_rows:
            track_id = int(item["track_id"])
            x1 = int(item["x1"])
            y1 = int(item["y1"])
            x2 = int(item["x2"])
            y2 = int(item["y2"])

            color = _track_color(track_id)
            self._cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)

            label = f"PERSON_{track_id:04d}"
            (label_w, label_h), baseline = self._cv2.getTextSize(label, self._cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            label_top = max(0, y1 - label_h - baseline - 6)
            label_bottom = min(frame_h - 1, label_top + label_h + baseline + 6)
            label_right = min(frame_w - 1, x1 + label_w + 8)
            self._cv2.rectangle(output, (x1, label_top), (label_right, label_bottom), color, -1)
            self._cv2.putText(
                output,
                label,
                (x1 + 4, label_bottom - baseline - 3),
                self._cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
                self._cv2.LINE_AA,
            )

        for polygon in zone_polygons:
            if len(polygon) < 3:
                continue
            pts = self._np.array(polygon, dtype=self._np.int32).reshape((-1, 1, 2))
            self._cv2.polylines(
                output,
                [pts],
                isClosed=True,
                color=(235, 235, 235),
                thickness=2,
                lineType=self._cv2.LINE_AA,
            )

        return output


_person_reid_overlay_service: PersonReidOverlayService | None = None


def get_person_reid_overlay_service() -> PersonReidOverlayService:
    global _person_reid_overlay_service
    if _person_reid_overlay_service is None:
        _person_reid_overlay_service = PersonReidOverlayService()
    return _person_reid_overlay_service


def _point_in_any_polygon(
    x: float,
    y: float,
    polygons: list[list[tuple[int, int]]],
) -> bool:
    import cv2
    import numpy as np

    pt = (float(x), float(y))
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        poly_arr = np.array(polygon, dtype=np.int32)
        if cv2.pointPolygonTest(poly_arr, pt, False) >= 0:
            return True
    return False


def _track_color(track_id: int) -> tuple[int, int, int]:
    if track_id < 0:
        return (120, 120, 120)
    return (
        int((37 * track_id + 17) % 255),
        int((17 * track_id + 113) % 255),
        int((29 * track_id + 53) % 255),
    )
