from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import crud
from app.services.tracking.boxmot_utils import (
    create_bytetrack_tracker,
    ensure_bytetrack_outputs,
    resolve_tracker_config_path,
)
from app.services.detection.yolo_threadsafe import build_locked_callable
from app.services.detection.yolo_person_classes import resolve_person_class_ids
from app.services.utils.zone_geometry import line_points_from_polygon, region_points_from_polygon

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CameraTrackerState:
    tracker: Any
    zone_signature: tuple[tuple[int, str], ...]
    last_zone_by_track: dict[int, int | None] = field(default_factory=dict)
    missing_frames_by_track: dict[int, int] = field(default_factory=dict)
    last_footpoint_by_track: dict[int, tuple[float, float]] = field(default_factory=dict)
    entered_zones_by_track: dict[int, set[int]] = field(default_factory=dict)
    exited_zones_by_track: dict[int, set[int]] = field(default_factory=dict)


@dataclass(slots=True)
class ZoneTransitions:
    entries: dict[int, int]
    exits: dict[int, int]


@dataclass(slots=True)
class ZoneLineDefinition:
    start: tuple[float, float]
    end: tuple[float, float]
    entry_direction: str


class AnalyticsEngine:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._running = False
        self._use_yolo = settings.analytics_use_yolo
        self._model_available = False
        self._model_error: str | None = None
        self._yolo = None
        self._predict = None
        self._cv2 = None
        self._np = None
        self._person_class_ids: list[int] = [0]
        self._create_tracker = None
        self._tracker_config_path: Path | None = None
        self._camera_states: dict[int, CameraTrackerState] = {}
        self._skip_counters: dict[int, float] = {}

        if self._use_yolo:
            try:
                from ultralytics import YOLO  # type: ignore
                from boxmot import create_tracker  # type: ignore
                import boxmot  # type: ignore

                self._yolo = YOLO(settings.yolo_model_path)
                self._yolo.to(settings.boxmot_device)
                self._predict = build_locked_callable(self._yolo.predict)
                self._create_tracker = create_tracker
                self._tracker_config_path = resolve_tracker_config_path(boxmot, "bytetrack")

                import cv2  # type: ignore
                import numpy as np  # type: ignore

                self._cv2 = cv2
                self._np = np
                self._person_class_ids = resolve_person_class_ids(self._yolo)
                self._model_available = True
            except Exception as exc:  # noqa: BLE001
                self._model_error = str(exc)
                logger.warning(
                    "Analytics model initialization failed for '%s' (ByteTrack): %s. "
                    "Footfall analytics will be skipped until model path is valid.",
                    settings.yolo_model_path,
                    exc,
                )

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                async with self._session_factory() as session:
                    await self._process_segments(session)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Analytics processing failed: %s", exc)
            await asyncio.sleep(settings.analytics_poll_seconds)

    async def _process_segments(self, session: AsyncSession) -> None:
        from app.services.core import runtime_config
        config = await runtime_config.get_runtime_config(session)
        ingestion_interval = float(config.get("frame_interval_seconds", settings.frame_interval_seconds))
        processing_fps = float(config.get("analytics_processing_fps", settings.analytics_processing_fps))

        # Ingestion FPS = 1 / interval
        # stride = Ingestion FPS / Processing FPS
        # e.g. 25 / 5 = 5. (Analyze 1, skip 4)
        ingestion_fps = 1.0 / max(ingestion_interval, 1e-6)
        stride = max(1.0, ingestion_fps / max(processing_fps, 0.1))

        frames = await crud.get_unprocessed_frames(session)
        for frame in frames:
            camera_id = frame.camera_id
            # Use a float counter to handle non-integer strides more gracefully (e.g. 30 FPS -> 7 FPS)
            current_count = self._skip_counters.get(camera_id, 0.0) + 1.0
            
            if current_count >= stride:
                self._skip_counters[camera_id] = current_count - stride
                
                # Full AI analysis
                zones = await crud.list_zones(session, camera_id=camera_id)
                if not zones:
                    await crud.mark_frame_processed(session, frame)
                    continue

                transitions = self._analyze_frame(frame.path, camera_id, zones)
                for zone_id, count in transitions.entries.items():
                    if count <= 0:
                        continue
                    await crud.create_footfall_event(
                        session=session,
                        camera_id=camera_id,
                        stream_id=frame.stream_id,
                        zone_id=zone_id,
                        timestamp=frame.timestamp,
                        count=count,
                        event_type="entry",
                    )

                for zone_id, count in transitions.exits.items():
                    if count <= 0:
                        continue
                    await crud.create_footfall_event(
                        session=session,
                        camera_id=camera_id,
                        stream_id=frame.stream_id,
                        zone_id=zone_id,
                        timestamp=frame.timestamp,
                        count=count,
                        event_type="exit",
                    )
            else:
                self._skip_counters[camera_id] = current_count

            # Always mark the frame as processed so it isn't picked up again.
            await crud.mark_frame_processed(session, frame)

    def _analyze_frame(self, path: str, camera_id: int, zones) -> ZoneTransitions:
        empty = _empty_zone_transitions(zones)
        if not self._model_available:
            return empty

        if not (
            self._use_yolo
            and self._yolo is not None
            and self._predict is not None
            and self._cv2 is not None
            and self._np is not None
            and self._create_tracker is not None
            and self._tracker_config_path is not None
        ):
            return empty
        return self._tracked_zone_transitions(path, camera_id, zones)

    def _tracked_zone_transitions(self, path: str, camera_id: int, zones) -> ZoneTransitions:
        if not zones:
            return ZoneTransitions(entries={}, exits={})

        if not Path(path).is_file():
            return _empty_zone_transitions(zones)

        frame = self._cv2.imread(path)
        if frame is None:
            logger.debug("Failed to read frame %s", path)
            return _empty_zone_transitions(zones)

        height, width = frame.shape[:2]
        zone_points = {zone.id: _normalize_polygon(zone.polygon, width, height) for zone in zones}
        zone_entry_lines = {zone.id: _normalize_entry_line(zone.polygon, width, height) for zone in zones}
        zone_exit_lines = {zone.id: _normalize_exit_line(zone.polygon, width, height) for zone in zones}
        state = self._get_or_create_camera_state(camera_id, zones)

        detections = self._detect_people(frame)
        tracked = self._run_tracker(state.tracker, detections, frame)
        return self._count_zone_transitions(tracked, zones, zone_points, zone_entry_lines, zone_exit_lines, state)

    def _detect_people(self, frame) -> Any:
        predict_kwargs = {
            "conf": settings.yolo_confidence,
            "iou": settings.yolo_iou,
            "verbose": settings.yolo_verbose,
        }
        if self._person_class_ids:
            predict_kwargs["classes"] = self._person_class_ids

        results = self._predict(frame, **predict_kwargs)
        if not results or results[0].boxes is None:
            return self._np.empty((0, 6), dtype=self._np.float32)

        boxes = results[0].boxes
        xyxy = boxes.xyxy.cpu().numpy().astype(self._np.float32)
        cls = boxes.cls.cpu().numpy().astype(self._np.float32)
        conf = boxes.conf.cpu().numpy().astype(self._np.float32)

        if len(xyxy) == 0:
            return self._np.empty((0, 6), dtype=self._np.float32)
        if self._person_class_ids:
            mask = self._np.isin(cls.astype(self._np.int32), self._person_class_ids)
            xyxy = xyxy[mask]
            cls = cls[mask]
            conf = conf[mask]
            if len(xyxy) == 0:
                return self._np.empty((0, 6), dtype=self._np.float32)

        # BoxMOT expects detections shaped Nx6: [x1, y1, x2, y2, conf, cls]
        return self._np.column_stack((xyxy, conf, cls)).astype(self._np.float32)

    def _run_tracker(self, tracker: Any, detections: Any, frame) -> Any:
        tracked = tracker.update(detections, frame)
        return ensure_bytetrack_outputs(tracked, self._np)

    def _count_zone_transitions(
        self,
        tracked: Any,
        zones,
        zone_points: dict[int, list[list[tuple[float, float]]]],
        zone_entry_lines: dict[int, ZoneLineDefinition | None],
        zone_exit_lines: dict[int, ZoneLineDefinition | None],
        state: CameraTrackerState,
    ) -> ZoneTransitions:
        frame_entries = {zone.id: 0 for zone in zones}
        frame_exits = {zone.id: 0 for zone in zones}
        seen_track_ids: set[int] = set()

        if tracked.size == 0:
            self._advance_missing_tracks(state, frame_exits)
            return ZoneTransitions(entries=frame_entries, exits=frame_exits)

        for row in tracked:
            try:
                row_length = len(row)
            except TypeError:
                continue
            if row_length < 5:
                continue

            x1, y1, x2, y2 = float(row[0]), float(row[1]), float(row[2]), float(row[3])
            try:
                track_id = int(row[4])
            except (TypeError, ValueError):
                continue
            if track_id < 0:
                continue

            seen_track_ids.add(track_id)
            zone_id = _resolve_zone_for_bbox(x1, y1, x2, y2, zone_points, zones)
            previous_zone_id = state.last_zone_by_track.get(track_id)
            footpoint = _bbox_footpoint(x1, y1, x2, y2)
            previous_footpoint = state.last_footpoint_by_track.get(track_id)

            state.last_zone_by_track[track_id] = zone_id
            state.missing_frames_by_track[track_id] = 0
            state.last_footpoint_by_track[track_id] = footpoint

            if previous_footpoint is not None:
                for line_zone_id, line in zone_entry_lines.items():
                    if line is None:
                        continue
                    if not _segment_intersects(previous_footpoint, footpoint, line.start, line.end):
                        continue

                    if line_zone_id not in state.entered_zones_by_track.get(track_id, set()):
                        frame_entries[line_zone_id] = int(frame_entries.get(line_zone_id, 0)) + 1
                        state.entered_zones_by_track.setdefault(track_id, set()).add(line_zone_id)

                for line_zone_id, line in zone_exit_lines.items():
                    if line is None:
                        continue
                    if not _segment_intersects(previous_footpoint, footpoint, line.start, line.end):
                        continue

                    if line_zone_id not in state.exited_zones_by_track.get(track_id, set()):
                        frame_exits[line_zone_id] = int(frame_exits.get(line_zone_id, 0)) + 1
                        state.exited_zones_by_track.setdefault(track_id, set()).add(line_zone_id)

        self._advance_missing_tracks(
            state,
            frame_exits,
            seen_track_ids=seen_track_ids,
        )
        return ZoneTransitions(entries=frame_entries, exits=frame_exits)

    def _advance_missing_tracks(
        self,
        state: CameraTrackerState,
        frame_exits: dict[int, int],
        seen_track_ids: set[int] | None = None,
    ) -> None:
        seen_track_ids = seen_track_ids or set()
        grace_frames = max(1, int(settings.analytics_track_grace_frames))

        for track_id in list(state.last_zone_by_track.keys()):
            if track_id in seen_track_ids:
                continue

            missing = int(state.missing_frames_by_track.get(track_id, 0)) + 1
            if missing < grace_frames:
                state.missing_frames_by_track[track_id] = missing
                continue

            state.last_zone_by_track.pop(track_id, None)
            state.missing_frames_by_track.pop(track_id, None)
            state.last_footpoint_by_track.pop(track_id, None)
            state.entered_zones_by_track.pop(track_id, None)
            state.exited_zones_by_track.pop(track_id, None)

    def _get_or_create_camera_state(self, camera_id: int, zones) -> CameraTrackerState:
        zone_signature = _zone_signature(zones)
        state = self._camera_states.get(camera_id)
        if state and state.zone_signature == zone_signature:
            return state

        if self._create_tracker is None or self._tracker_config_path is None:
            raise RuntimeError("ByteTrack tracker factory is not initialized")

        tracker = create_bytetrack_tracker(
            create_tracker_fn=self._create_tracker,
            config_path=self._tracker_config_path,
            device=settings.boxmot_device,
            half=False,
            per_class=False,
            track_thresh=settings.bytetrack_track_thresh,
            match_thresh=settings.bytetrack_match_thresh,
            track_buffer=settings.bytetrack_track_buffer,
        )
        state = CameraTrackerState(
            tracker=tracker,
            zone_signature=zone_signature,
        )
        self._camera_states[camera_id] = state
        return state


def _zone_signature(zones) -> tuple[tuple[int, str], ...]:
    signature = []
    for zone in zones:
        polygon_signature = json.dumps(zone.polygon, sort_keys=True, separators=(",", ":"))
        signature.append((zone.id, polygon_signature))
    signature.sort(key=lambda item: item[0])
    return tuple(signature)


def _empty_zone_transitions(zones) -> ZoneTransitions:
    return ZoneTransitions(
        entries={zone.id: 0 for zone in zones},
        exits={zone.id: 0 for zone in zones},
    )


def _normalize_polygon(polygon: Any, width: int, height: int) -> list[list[tuple[float, float]]]:
    polygons = region_points_from_polygon(
        polygon,
        width,
        height,
        use_default_on_invalid=False,
    )
    return [[(float(x), float(y)) for x, y in poly] for poly in polygons]


def _normalize_entry_line(polygon: Any, width: int, height: int) -> ZoneLineDefinition | None:
    points = line_points_from_polygon(polygon, width, height, line_key="entry_line")
    if points is None:
        points = line_points_from_polygon(polygon, width, height, line_key="entry_exit_line")
        if points is None:
            return None
            
    return ZoneLineDefinition(
        start=(float(points[0][0]), float(points[0][1])),
        end=(float(points[1][0]), float(points[1][1])),
        entry_direction="any",
    )


def _normalize_exit_line(polygon: Any, width: int, height: int) -> ZoneLineDefinition | None:
    points = line_points_from_polygon(polygon, width, height, line_key="exit_line")
    if points is None:
        return None
    return ZoneLineDefinition(
        start=(float(points[0][0]), float(points[0][1])),
        end=(float(points[1][0]), float(points[1][1])),
        entry_direction="any",
    )


def _bbox_footpoint(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
    return ((x1 + x2) / 2.0, y2)


def _line_side(
    point: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> float:
    return (
        (line_end[0] - line_start[0]) * (point[1] - line_start[1])
        - (line_end[1] - line_start[1]) * (point[0] - line_start[0])
    )


def _is_entry_crossing(
    previous_point: tuple[float, float],
    current_point: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
    entry_direction: str,
) -> bool:
    previous_side = _line_side(previous_point, line_start, line_end)
    current_side = _line_side(current_point, line_start, line_end)

    # Fallback when the track lands directly on the line.
    if abs(previous_side) <= 1e-6 or abs(current_side) <= 1e-6:
        entry_direction = (entry_direction or "a_to_b").strip().lower()
        if entry_direction == "b_to_a":
            return previous_side < current_side
        return previous_side > current_side

    entry_direction = (entry_direction or "a_to_b").strip().lower()
    if entry_direction == "b_to_a":
        return previous_side < 0.0 and current_side > 0.0
    return previous_side > 0.0 and current_side < 0.0


def _segment_intersects(
    p1: tuple[float, float],
    p2: tuple[float, float],
    q1: tuple[float, float],
    q2: tuple[float, float],
    eps: float = 1e-6,
) -> bool:
    o1 = _orientation(p1, p2, q1)
    o2 = _orientation(p1, p2, q2)
    o3 = _orientation(q1, q2, p1)
    o4 = _orientation(q1, q2, p2)

    if (o1 > eps and o2 < -eps or o1 < -eps and o2 > eps) and (
        o3 > eps and o4 < -eps or o3 < -eps and o4 > eps
    ):
        return True

    if abs(o1) <= eps and _point_on_segment(q1, p1, p2, eps):
        return True
    if abs(o2) <= eps and _point_on_segment(q2, p1, p2, eps):
        return True
    if abs(o3) <= eps and _point_on_segment(p1, q1, q2, eps):
        return True
    if abs(o4) <= eps and _point_on_segment(p2, q1, q2, eps):
        return True
    return False


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_on_segment(
    point: tuple[float, float],
    segment_start: tuple[float, float],
    segment_end: tuple[float, float],
    eps: float = 1e-6,
) -> bool:
    if abs(_orientation(segment_start, segment_end, point)) > eps:
        return False
    min_x = min(segment_start[0], segment_end[0]) - eps
    max_x = max(segment_start[0], segment_end[0]) + eps
    min_y = min(segment_start[1], segment_end[1]) - eps
    max_y = max(segment_start[1], segment_end[1]) + eps
    return min_x <= point[0] <= max_x and min_y <= point[1] <= max_y


def _resolve_zone_for_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    zone_points: dict[int, list[list[tuple[float, float]]]],
    zones,
) -> int | None:
    # Prefer foot-point for entry/footfall logic.
    foot_x = (x1 + x2) / 2.0
    foot_y = y2
    zone_id = _resolve_zone_for_point(foot_x, foot_y, zone_points, zones)
    if zone_id is not None:
        return zone_id

    # Fallback to center for clipped/noisy boxes.
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    return _resolve_zone_for_point(center_x, center_y, zone_points, zones)


def _resolve_zone_for_point(
    x: float,
    y: float,
    zone_points: dict[int, list[list[tuple[float, float]]]],
    zones,
) -> int | None:
    for zone in zones:
        polygons = zone_points.get(zone.id) or []
        for polygon in polygons:
            if not polygon:
                continue
            if _point_in_polygon(x, y, polygon):
                return zone.id
    return None


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside
