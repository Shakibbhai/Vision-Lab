from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rtsp import resolve_rtsp_url
from app.db import crud
from app.db.session import AsyncSessionLocal
from app.services.recognition.facial_expression_recognition import FacialExpressionRecognitionService
from app.services.recognition.face_recognition import FaceRecognitionService
from app.services.analytics.monitoring import collect_metrics
from app.services.tracking.person_reid_overlay import get_person_reid_overlay_service
from app.services.analytics.realtime_monitoring import get_realtime_monitoring_service
from app.services.analytics.trajectory_heatmap import get_trajectory_heatmap_service
from app.services.utils.zone_crop import crop_image_to_zone
from app.services.utils.zone_geometry import line_points_from_polygon

_analytics_running = True
_SOURCE_FRAME_TIMEOUT_SECONDS = 12
_FACIAL_EXPRESSION_RETRY_SECONDS = 30.0
_FACE_RECOGNITION_RETRY_SECONDS = 30.0
_FACIAL_REALTIME_DEFAULT_INTERVAL_SECONDS = 0.04
_FACIAL_REALTIME_DEFAULT_CACHE_TTL_SECONDS = 2.0
_RENDERED_FRAME_CACHE_TTL_SECONDS = 8.0
_RENDERED_FRAME_CACHE_MAX_ENTRIES = 48
_facial_expression_service: FacialExpressionRecognitionService | None = None
_facial_expression_retry_after_monotonic = 0.0
_face_recognition_service: FaceRecognitionService | None = None
_face_recognition_retry_after_monotonic = 0.0
_facial_detection_cache: dict[int, "_FacialDetectionCacheEntry"] = {}
_facial_detection_locks: dict[int, asyncio.Lock] = {}
_facial_detection_tasks: dict[int, asyncio.Task[Any]] = {}
_realtime_monitoring_tasks: set[asyncio.Task[Any]] = set()
_rendered_frame_cache: dict[tuple[int, str, str, str], "_RenderedFrameCacheEntry"] = {}
_rendered_frame_cache_lock = threading.Lock()
logger = logging.getLogger(__name__)


@dataclass
class _FacialDetectionCacheEntry:
    frame_token: str
    updated_at_monotonic: float
    frame_width: int
    frame_height: int
    detections: list[dict[str, Any]]


@dataclass
class _RenderedFrameCacheEntry:
    payload: bytes
    updated_at_monotonic: float


async def _refresh_realtime_count_cache(
    service,
    camera_id: int,
    frame_path: str,
    zones: list[Any],
) -> None:
    try:
        await asyncio.to_thread(service.refresh_cached_result, camera_id, frame_path, zones)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Realtime count refresh failed for camera %s: %s", camera_id, exc)


async def _get_realtime_count_snapshot(
    service,
    camera_id: int,
    frame_path: str,
    zones: list[Any],
) -> dict[str, Any]:
    result, should_refresh = service.get_cached_result(camera_id, frame_path, zones)
    if should_refresh:
        task = asyncio.create_task(_refresh_realtime_count_cache(service, camera_id, frame_path, zones))
        _realtime_monitoring_tasks.add(task)

        def _cleanup(done_task: asyncio.Task[Any]) -> None:
            _realtime_monitoring_tasks.discard(done_task)

        task.add_done_callback(_cleanup)
    return result


def _is_entry_exit_mode(analytics_mode: str | None) -> bool:
    return (analytics_mode or "").strip().lower() == "entry_exit_count"


def _is_facial_expression_mode(analytics_mode: str | None) -> bool:
    return (analytics_mode or "").strip().lower() == "facial_expression"


def _is_trajectory_heatmap_mode(analytics_mode: str | None) -> bool:
    return (analytics_mode or "").strip().lower() == "trajectory_heatmap"


def _is_person_reid_mode(analytics_mode: str | None) -> bool:
    return (analytics_mode or "").strip().lower() == "person_reid"


def _is_face_recognition_mode(analytics_mode: str | None) -> bool:
    return (analytics_mode or "").strip().lower() == "face_recognition"


def _get_facial_expression_service() -> FacialExpressionRecognitionService:
    global _facial_expression_service, _facial_expression_retry_after_monotonic

    now = time.monotonic()
    should_initialize = _facial_expression_service is None
    should_retry = (
        _facial_expression_service is not None
        and not _facial_expression_service.is_available()
        and now >= _facial_expression_retry_after_monotonic
    )
    if should_initialize or should_retry:
        _facial_expression_service = FacialExpressionRecognitionService()
        if not _facial_expression_service.is_available():
            _facial_expression_retry_after_monotonic = now + _FACIAL_EXPRESSION_RETRY_SECONDS
            logger.warning(
                "Facial expression service unavailable; will retry initialization in %.0f seconds",
                _FACIAL_EXPRESSION_RETRY_SECONDS,
            )
        else:
            _facial_expression_retry_after_monotonic = 0.0
    return _facial_expression_service


def _get_face_recognition_service() -> FaceRecognitionService:
    global _face_recognition_service, _face_recognition_retry_after_monotonic

    now = time.monotonic()
    should_initialize = _face_recognition_service is None
    should_retry = (
        _face_recognition_service is not None
        and not _face_recognition_service.is_available()
        and now >= _face_recognition_retry_after_monotonic
    )
    if should_initialize or should_retry:
        _face_recognition_service = FaceRecognitionService()
        if not _face_recognition_service.is_available():
            _face_recognition_retry_after_monotonic = now + _FACE_RECOGNITION_RETRY_SECONDS
            logger.warning(
                "Face recognition service unavailable; will retry initialization in %.0f seconds",
                _FACE_RECOGNITION_RETRY_SECONDS,
            )
        else:
            _face_recognition_retry_after_monotonic = 0.0
    return _face_recognition_service


def _draw_facial_expression_overlays(cv2: Any, frame: Any, detections: list[dict[str, Any]]) -> None:
    if not detections:
        return

    frame_height, frame_width = frame.shape[:2]
    box_thickness = max(3, int(round(min(frame_width, frame_height) / 280)))
    font_scale = max(0.6, min(1.0, min(frame_width, frame_height) / 900))
    text_thickness = max(1, int(round(box_thickness * 1.0)))
    emotion_colors: dict[str, tuple[int, int, int]] = {
        "happy": (46, 205, 247),
        "sad": (235, 138, 52),
        "angry": (61, 61, 245),
        "surprise": (244, 231, 79),
        "fear": (150, 94, 244),
        "disgust": (97, 201, 85),
        "neutral": (243, 240, 240),
    }

    for row in detections:
        try:
            x1 = int(row.get("x1", 0))
            y1 = int(row.get("y1", 0))
            x2 = int(row.get("x2", 0))
            y2 = int(row.get("y2", 0))
            emotion = str(row.get("emotion", "")).strip().capitalize() or "Unknown"
            age = row.get("age")
            gender = str(row.get("gender", "")).strip().capitalize() or "Unknown"
        except Exception:  # noqa: BLE001
            continue

        x1 = max(0, min(frame_width - 1, x1))
        y1 = max(0, min(frame_height - 1, y1))
        x2 = max(0, min(frame_width - 1, x2))
        y2 = max(0, min(frame_height - 1, y2))
        if x2 <= x1 or y2 <= y1:
            continue

        color = emotion_colors.get(emotion.lower(), (96, 245, 252))
        # Add black under-stroke to keep face boxes visible on bright backgrounds.
        cv2.rectangle(frame, (x1, y1), (x2, y2), (16, 16, 16), box_thickness + 3)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, box_thickness)

        parts = []
        if emotion and emotion != "Unknown":
            parts.append(emotion)
        if gender and gender != "Unknown":
            parts.append(gender)
        if age is not None:
            parts.append(f"{age}y")

        label = " | ".join(parts) if parts else "Unknown"
        (label_w, label_h), baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            text_thickness,
        )
        top = max(0, y1 - label_h - baseline - 10)
        bottom = min(frame_height - 1, top + label_h + baseline + 10)
        right = min(frame_width - 1, x1 + label_w + 12)
        cv2.rectangle(frame, (x1, top), (right, bottom), (12, 12, 12), -1)
        cv2.rectangle(frame, (x1, top), (right, bottom), color, 2)
        cv2.putText(
            frame,
            label,
            (x1 + 6, bottom - baseline - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            text_thickness,
            cv2.LINE_AA,
        )


def _draw_face_recognition_overlays(cv2: Any, frame: Any, detections: list[dict[str, Any]]) -> None:
    if not detections:
        return

    frame_height, frame_width = frame.shape[:2]
    box_thickness = max(3, int(round(min(frame_width, frame_height) / 280)))
    font_scale = max(0.6, min(1.0, min(frame_width, frame_height) / 900))
    text_thickness = max(1, int(round(box_thickness * 1.0)))
    box_color = (28, 201, 255)
    label_color = (255, 255, 255)

    for row in detections:
        try:
            x1 = int(row.get("x1", 0))
            y1 = int(row.get("y1", 0))
            x2 = int(row.get("x2", 0))
            y2 = int(row.get("y2", 0))
            name = str(row.get("name", "")).strip() or "Unknown"
            distance = row.get("distance")
        except Exception:  # noqa: BLE001
            continue

        x1 = max(0, min(frame_width - 1, x1))
        y1 = max(0, min(frame_height - 1, y1))
        x2 = max(0, min(frame_width - 1, x2))
        y2 = max(0, min(frame_height - 1, y2))
        if x2 <= x1 or y2 <= y1:
            continue

        cv2.rectangle(frame, (x1, y1), (x2, y2), (10, 10, 10), box_thickness + 3)
        cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, box_thickness)

        label = name
        if isinstance(distance, (float, int)):
            label = f"{label} | {float(distance):.2f}"
        (label_w, label_h), baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            text_thickness,
        )
        top = max(0, y1 - label_h - baseline - 10)
        bottom = min(frame_height - 1, top + label_h + baseline + 10)
        right = min(frame_width - 1, x1 + label_w + 12)
        cv2.rectangle(frame, (x1, top), (right, bottom), (8, 8, 8), -1)
        cv2.rectangle(frame, (x1, top), (right, bottom), box_color, 2)
        cv2.putText(
            frame,
            label,
            (x1 + 6, bottom - baseline - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            label_color,
            text_thickness,
            cv2.LINE_AA,
        )


def _extract_zone_polygons(polygon_payload: Any) -> list[list[Any]]:
    if isinstance(polygon_payload, dict):
        raw_polygons = polygon_payload.get("polygons")
        if isinstance(raw_polygons, list):
            return [poly for poly in raw_polygons if isinstance(poly, list)]
        points = polygon_payload.get("points")
        if isinstance(points, list):
            return [points]
        return []

    if isinstance(polygon_payload, list):
        if polygon_payload and isinstance(polygon_payload[0], list):
            return [poly for poly in polygon_payload if isinstance(poly, list)]
        return [polygon_payload]

    return []


def _zone_filter_from_zones(zones: list[Any]) -> Any | None:
    polygons: list[list[Any]] = []
    for zone in zones:
        polygons.extend(_extract_zone_polygons(getattr(zone, "polygon", None)))

    if not polygons:
        return None

    return SimpleNamespace(polygon={"polygons": polygons})


def _normalized_analytics_mode(analytics_mode: str | None) -> str:
    return (analytics_mode or "").strip().lower()


def _zone_scope_cache_key(zone: Any | None) -> str:
    if zone is None:
        return "none"

    zone_id = getattr(zone, "id", None)
    if zone_id is not None:
        return f"zone:{int(zone_id)}"

    polygon = getattr(zone, "polygon", None)
    if polygon is None:
        return "custom:none"
    return f"custom:{abs(hash(repr(polygon)))}"


def _zones_scope_cache_key(zones: list[Any]) -> str:
    if not zones:
        return "zones:none"

    parts: list[str] = []
    for zone in sorted(zones, key=lambda item: int(getattr(item, "id", 0))):
        zone_id = int(getattr(zone, "id", 0))
        polygon = getattr(zone, "polygon", None)
        parts.append(f"{zone_id}:{abs(hash(repr(polygon)))}")
    return "zones:" + "|".join(parts)


def _get_cached_rendered_frame(
    camera_id: int,
    frame_token: str,
    analytics_mode: str | None,
    scope_key: str,
) -> bytes | None:
    now = time.monotonic()
    cache_key = (int(camera_id), frame_token, _normalized_analytics_mode(analytics_mode) or "default", scope_key)
    with _rendered_frame_cache_lock:
        cached = _rendered_frame_cache.get(cache_key)
        if cached is None:
            return None
        if now - cached.updated_at_monotonic > _RENDERED_FRAME_CACHE_TTL_SECONDS:
            _rendered_frame_cache.pop(cache_key, None)
            return None
        cached.updated_at_monotonic = now
        return cached.payload


def _store_cached_rendered_frame(
    camera_id: int,
    frame_token: str,
    analytics_mode: str | None,
    scope_key: str,
    payload: bytes,
) -> None:
    now = time.monotonic()
    cache_key = (int(camera_id), frame_token, _normalized_analytics_mode(analytics_mode) or "default", scope_key)
    with _rendered_frame_cache_lock:
        _rendered_frame_cache[cache_key] = _RenderedFrameCacheEntry(
            payload=payload,
            updated_at_monotonic=now,
        )
        stale_keys = [
            key
            for key, entry in _rendered_frame_cache.items()
            if now - entry.updated_at_monotonic > _RENDERED_FRAME_CACHE_TTL_SECONDS
        ]
        for stale_key in stale_keys:
            _rendered_frame_cache.pop(stale_key, None)
        if len(_rendered_frame_cache) <= _RENDERED_FRAME_CACHE_MAX_ENTRIES:
            return

        extra = len(_rendered_frame_cache) - _RENDERED_FRAME_CACHE_MAX_ENTRIES
        oldest_keys = sorted(
            _rendered_frame_cache,
            key=lambda key: _rendered_frame_cache[key].updated_at_monotonic,
        )[:extra]
        for oldest_key in oldest_keys:
            _rendered_frame_cache.pop(oldest_key, None)


def _get_facial_detection_lock(camera_id: int) -> asyncio.Lock:
    lock = _facial_detection_locks.get(camera_id)
    if lock is None:
        lock = asyncio.Lock()
        _facial_detection_locks[camera_id] = lock
    return lock


def _get_facial_detection_task(camera_id: int) -> asyncio.Task[Any] | None:
    task = _facial_detection_tasks.get(camera_id)
    if task is not None and task.done():
        _facial_detection_tasks.pop(camera_id, None)
        return None
    return task


def _prune_facial_detection_cache(now: float, ttl_seconds: float) -> None:
    done_task_ids = [camera_id for camera_id, task in _facial_detection_tasks.items() if task.done()]
    for camera_id in done_task_ids:
        _facial_detection_tasks.pop(camera_id, None)

    if not _facial_detection_cache:
        return
    stale_ids = [
        camera_id
        for camera_id, entry in _facial_detection_cache.items()
        if now - entry.updated_at_monotonic > ttl_seconds
    ]
    for camera_id in stale_ids:
        task = _get_facial_detection_task(camera_id)
        lock = _facial_detection_locks.get(camera_id)
        if task is not None or (lock is not None and lock.locked()):
            continue
        _facial_detection_cache.pop(camera_id, None)
        _facial_detection_locks.pop(camera_id, None)


def _schedule_facial_detection_refresh(
    camera_id: int,
    frame_token: str,
    frame: Any,
    zone: Any,
    service: FacialExpressionRecognitionService,
) -> None:
    existing_task = _get_facial_detection_task(camera_id)
    if existing_task is not None:
        return

    frame_h, frame_w = frame.shape[:2]
    if frame_w <= 0 or frame_h <= 0:
        return
    frame_copy = frame.copy()

    async def _run() -> None:
        lock = _get_facial_detection_lock(camera_id)
        async with lock:
            cached = _facial_detection_cache.get(camera_id)
            if (
                cached is not None
                and cached.frame_token == frame_token
                and cached.frame_width == frame_w
                and cached.frame_height == frame_h
            ):
                return

            try:
                detections = await asyncio.to_thread(service.detect_faces_with_demographics, frame_copy, zone)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Facial expression async refresh failed for one frame: %s", exc)
                detections = []

            _facial_detection_cache[camera_id] = _FacialDetectionCacheEntry(
                frame_token=frame_token,
                updated_at_monotonic=time.monotonic(),
                frame_width=frame_w,
                frame_height=frame_h,
                detections=detections,
            )

    task = asyncio.create_task(_run())
    _facial_detection_tasks[camera_id] = task

    def _cleanup(done_task: asyncio.Task[Any]) -> None:
        if _facial_detection_tasks.get(camera_id) is done_task:
            _facial_detection_tasks.pop(camera_id, None)

    task.add_done_callback(_cleanup)


async def _get_facial_detections_for_frame(
    camera_id: int,
    frame_token: str,
    frame: Any,
    zone: Any,
    *,
    non_blocking: bool = False,
) -> list[dict[str, Any]]:
    # Facial-expression detection requires a valid zone filter. Both callers already resolve
    # the zone filter from the database before calling this function, so if zone is still None
    # here, there truly are no configured zones and we can skip detection safely.
    if zone is None:
        return []

    service = _get_facial_expression_service()
    if not service.is_available():
        return []

    frame_h, frame_w = frame.shape[:2]
    if frame_w <= 0 or frame_h <= 0:
        return []

    now = time.monotonic()
    target_interval_seconds = max(0.01, float(getattr(settings, "frame_interval_seconds", 0.04)))
    min_interval_seconds = max(
        0.005,
        float(
            getattr(
                settings,
                "facial_expression_realtime_interval_seconds",
                target_interval_seconds,
            )
        ),
    )
    configured_cache_ttl = float(
        getattr(
            settings,
            "facial_expression_realtime_cache_ttl_seconds",
            _FACIAL_REALTIME_DEFAULT_CACHE_TTL_SECONDS,
        )
    )
    # Allow cached detections to persist across several frames so the overlay
    # stays visible while the next DeepFace analysis runs in the background.
    cache_ttl_seconds = max(min_interval_seconds * 3.0, configured_cache_ttl, 2.0)
    _prune_facial_detection_cache(now, cache_ttl_seconds)

    cached = _facial_detection_cache.get(camera_id)
    if cached is not None:
        if (
            cached.frame_token == frame_token
            and cached.frame_width == frame_w
            and cached.frame_height == frame_h
        ):
            return service.filter_detections_by_zone(cached.detections, zone, frame_w, frame_h)
        if (
            now - cached.updated_at_monotonic < min_interval_seconds
            and cached.frame_width == frame_w
            and cached.frame_height == frame_h
        ):
            return service.filter_detections_by_zone(cached.detections, zone, frame_w, frame_h)

    lock = _get_facial_detection_lock(camera_id)
    if non_blocking:
        _schedule_facial_detection_refresh(camera_id, frame_token, frame, zone, service)
        if cached is None:
            return []
        if now - cached.updated_at_monotonic > cache_ttl_seconds:
            return []
        return service.filter_detections_by_zone(cached.detections, zone, frame_w, frame_h)

    if lock.locked():
        # Keep stream FPS stable while another request is running DeepFace.
        if cached is None:
            return []
        if now - cached.updated_at_monotonic > cache_ttl_seconds:
            return []
        return service.filter_detections_by_zone(cached.detections, zone, frame_w, frame_h)

    async with lock:
        now = time.monotonic()
        cached = _facial_detection_cache.get(camera_id)
        if cached is not None:
            if (
                cached.frame_token == frame_token
                and cached.frame_width == frame_w
                and cached.frame_height == frame_h
            ):
                return service.filter_detections_by_zone(cached.detections, zone, frame_w, frame_h)
            if (
                now - cached.updated_at_monotonic < min_interval_seconds
                and cached.frame_width == frame_w
                and cached.frame_height == frame_h
            ):
                return service.filter_detections_by_zone(cached.detections, zone, frame_w, frame_h)

        try:
            detections = await asyncio.to_thread(service.detect_faces_with_demographics, frame, zone)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Facial expression detection failed for one frame: %s", exc)
            detections = []

        _facial_detection_cache[camera_id] = _FacialDetectionCacheEntry(
            frame_token=frame_token,
            updated_at_monotonic=time.monotonic(),
            frame_width=frame_w,
            frame_height=frame_h,
            detections=detections,
        )
        return service.filter_detections_by_zone(detections, zone, frame_w, frame_h)

def _draw_zone_polygons(cv2: Any, frame: Any, zone: Any) -> None:
    """Draw zone polygon boundary/boundaries onto the frame."""
    if zone is None:
        return
    try:
        from app.services.utils.zone_geometry import region_points_from_polygon

        frame_h, frame_w = frame.shape[:2]
        if frame_w <= 0 or frame_h <= 0:
            return

        polygons = region_points_from_polygon(
            getattr(zone, "polygon", None),
            frame_w,
            frame_h,
            use_default_on_invalid=False,
        )
        if not polygons:
            return

        import numpy as np

        thickness = max(2, int(round(min(frame_w, frame_h) / 540)))
        for poly in polygons:
            pts = np.array(poly, dtype=np.int32).reshape((-1, 1, 2))
            # Keep zone fill subtle so facial overlays remain readable.
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pts], (16, 160, 214))
            cv2.addWeighted(overlay, 0.08, frame, 0.92, 0, frame)
            cv2.polylines(frame, [pts], isClosed=True, color=(214, 248, 255), thickness=thickness, lineType=cv2.LINE_AA)

    except Exception as exc:  # noqa: BLE001
        logger.debug("Zone polygon draw failed: %s", exc)


def _draw_entry_exit_lines(cv2: Any, frame: Any, zones: list[Any]) -> None:
    if not zones:
        return

    frame_height, frame_width = frame.shape[:2]
    if frame_width <= 0 or frame_height <= 0:
        return

    line_thickness = max(2, int(round(min(frame_width, frame_height) / 540)))
    for zone in zones:
        polygon = getattr(zone, "polygon", None)
        entry_line = line_points_from_polygon(polygon, frame_width, frame_height, line_key="entry_line")
        if entry_line is None:
            entry_line = line_points_from_polygon(polygon, frame_width, frame_height, line_key="entry_exit_line")
        exit_line = line_points_from_polygon(polygon, frame_width, frame_height, line_key="exit_line")

        if entry_line is not None:
            cv2.line(
                frame,
                (int(round(entry_line[0][0])), int(round(entry_line[0][1]))),
                (int(round(entry_line[1][0])), int(round(entry_line[1][1]))),
                (90, 200, 120),
                line_thickness,
                cv2.LINE_AA,
            )
        if exit_line is not None:
            cv2.line(
                frame,
                (int(round(exit_line[0][0])), int(round(exit_line[0][1]))),
                (int(round(exit_line[1][0])), int(round(exit_line[1][1]))),
                (70, 90, 230),
                line_thickness,
                cv2.LINE_AA,
            )


def _render_entry_exit_lines(payload: bytes, zones: list[Any]) -> bytes:
    if not payload or not zones:
        return payload
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(payload, np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            return payload
        _draw_entry_exit_lines(cv2, image, zones)
        success, encoded = _encode_jpeg(cv2, image)
        if success:
            return encoded.tobytes()
    except Exception:
        return payload
    return payload


def _encode_jpeg(cv2: Any, image: Any):
    quality = int(getattr(settings, "mjpeg_jpeg_quality", 80))
    quality = max(40, min(95, quality))
    return cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])


async def _render_frame_payload(
    session: AsyncSession,
    camera_id: int,
    frame: Any,
    path: Path,
    zone: Any | None,
    analytics_mode: str | None,
    *,
    payload: bytes | None = None,
    camera_zones: list[Any] | None = None,
    frame_token: str | None = None,
) -> bytes | None:
    entry_exit_mode = _is_entry_exit_mode(analytics_mode)
    facial_expression_mode = _is_facial_expression_mode(analytics_mode)
    trajectory_heatmap_mode = _is_trajectory_heatmap_mode(analytics_mode)
    person_reid_mode = _is_person_reid_mode(analytics_mode)
    face_recognition_mode = _is_face_recognition_mode(analytics_mode)

    if not (
        zone is not None
        or entry_exit_mode
        or facial_expression_mode
        or trajectory_heatmap_mode
        or person_reid_mode
        or face_recognition_mode
    ):
        return None

    resolved_camera_zones = list(camera_zones or [])
    facial_expression_zone_filter = zone
    trajectory_zone_filter = zone

    if entry_exit_mode and zone is None and not resolved_camera_zones:
        resolved_camera_zones = await crud.list_zones(session, camera_id=camera_id)
    if facial_expression_mode and not resolved_camera_zones:
        resolved_camera_zones = await crud.list_zones(session, camera_id=camera_id)
    if facial_expression_mode and facial_expression_zone_filter is None:
        facial_expression_zone_filter = _zone_filter_from_zones(resolved_camera_zones)

    scope_key = _zone_scope_cache_key(zone)
    if entry_exit_mode and zone is None:
        scope_key = _zones_scope_cache_key(resolved_camera_zones)
    elif facial_expression_mode:
        scope_key = _zone_scope_cache_key(facial_expression_zone_filter)
    elif trajectory_heatmap_mode:
        scope_key = _zone_scope_cache_key(trajectory_zone_filter)

    resolved_frame_token = frame_token or f"{frame.id}:{path.stat().st_mtime_ns}"
    cached_payload = _get_cached_rendered_frame(camera_id, resolved_frame_token, analytics_mode, scope_key)
    if cached_payload is not None:
        return cached_payload

    try:
        import cv2

        image = None
        if payload is not None:
            import numpy as np

            arr = np.frombuffer(payload, np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            image = cv2.imread(str(path))

        if image is not None:
            if trajectory_heatmap_mode:
                heatmap_service = get_trajectory_heatmap_service()
                if heatmap_service.is_available():
                    overlay = await asyncio.to_thread(
                        heatmap_service.render_overlay,
                        camera_id,
                        resolved_frame_token,
                        image,
                        trajectory_zone_filter,
                    )
                    image = overlay.get("frame", image)
            elif person_reid_mode:
                reid_overlay_service = get_person_reid_overlay_service()
                if reid_overlay_service.is_available():
                    overlay = await asyncio.to_thread(
                        reid_overlay_service.render_overlay,
                        camera_id,
                        resolved_frame_token,
                        image,
                        zone,
                    )
                    image = overlay.get("frame", image)
            elif face_recognition_mode:
                face_references = await crud.list_face_references(session)
                face_recognition_service = _get_face_recognition_service()
                detections = await asyncio.to_thread(
                    face_recognition_service.detect_faces_with_matches,
                    image,
                    zone,
                    face_references,
                )
                if zone is not None:
                    _draw_zone_polygons(cv2, image, zone)
                _draw_face_recognition_overlays(cv2, image, detections)
            elif facial_expression_mode:
                detections = await _get_facial_detections_for_frame(
                    camera_id,
                    resolved_frame_token,
                    image,
                    facial_expression_zone_filter,
                    non_blocking=True,
                )
                _draw_zone_polygons(cv2, image, facial_expression_zone_filter)
                _draw_facial_expression_overlays(cv2, image, detections)
            elif entry_exit_mode:
                _draw_entry_exit_lines(cv2, image, [zone] if zone is not None else resolved_camera_zones)
            elif zone is not None:
                image = crop_image_to_zone(image, zone)

            success, buffer = _encode_jpeg(cv2, image)
            if success:
                rendered_payload = buffer.tobytes()
                _store_cached_rendered_frame(
                    camera_id,
                    resolved_frame_token,
                    analytics_mode,
                    scope_key,
                    rendered_payload,
                )
                return rendered_payload
    except Exception:
        pass

    if entry_exit_mode:
        rendered_payload = _render_entry_exit_lines(
            payload if payload is not None else path.read_bytes(),
            [zone] if zone is not None else resolved_camera_zones,
        )
        _store_cached_rendered_frame(
            camera_id,
            resolved_frame_token,
            analytics_mode,
            scope_key,
            rendered_payload,
        )
        return rendered_payload

    return None


async def _resolve_existing_frame(
    session: AsyncSession,
    camera_id: int,
    *,
    prefer_first: bool,
):
    preferred = await (crud.get_first_frame(session, camera_id) if prefer_first else crud.get_latest_frame(session, camera_id))
    if preferred and Path(preferred.path).exists():
        return preferred

    # Fall back to scanning all frame rows when stale DB rows reference deleted files.
    # This avoids false "no frame" responses when early rows are stale but newer files exist.
    frames = await crud.list_frames_for_camera(
        session,
        camera_id,
        limit=None,
        descending=not prefer_first,
    )
    for frame in frames:
        if Path(frame.path).exists():
            return frame
    return None


async def metrics(session: AsyncSession):
    return await collect_metrics(session)


def analytics_state() -> dict[str, bool]:
    return {"running": _analytics_running}


def start_analytics() -> dict[str, bool]:
    global _analytics_running
    _analytics_running = True
    return {"running": _analytics_running}


def stop_analytics() -> dict[str, bool]:
    global _analytics_running
    _analytics_running = False
    return {"running": _analytics_running}


async def realtime(session: AsyncSession, camera_ids: list[int] | None = None):
    cameras = await crud.list_cameras(session, camera_ids=camera_ids)

    service = get_realtime_monitoring_service()
    analytics_running = _analytics_running
    heartbeat_timeout = timedelta(seconds=max(1.0, float(settings.stream_heartbeat_timeout_seconds)))
    camera_id_list = [camera.id for camera in cameras]
    streams_by_camera = await crud.list_streams_for_cameras(session, camera_id_list)
    zones_by_camera = await crud.list_zones_for_cameras(session, camera_id_list)
    latest_frames_by_camera = await crud.get_latest_frames_for_cameras(session, camera_id_list)

    def _is_frame_fresh(frame: Any | None) -> bool:
        if frame is None or frame.timestamp is None:
            return False
        frame_timestamp = frame.timestamp
        if frame_timestamp.tzinfo is not None:
            now = datetime.now(timezone.utc)
            frame_timestamp = frame_timestamp.astimezone(timezone.utc)
        else:
            now = datetime.utcnow()
        return now - frame_timestamp <= heartbeat_timeout

    output = []
    for camera in cameras:
        stream = streams_by_camera.get(camera.id)
        zones = zones_by_camera.get(camera.id, [])
        latest_frame = latest_frames_by_camera.get(camera.id)

        has_frame = latest_frame is not None and Path(latest_frame.path).exists()
        frame_fresh = _is_frame_fresh(latest_frame) if has_frame else False
        frame_timestamp = latest_frame.timestamp.isoformat() if latest_frame else None
        stream_status = stream.status.value if stream else "stopped"
        heartbeat_fresh = False
        if stream and stream.last_heartbeat:
            heartbeat_fresh = datetime.utcnow() - stream.last_heartbeat <= heartbeat_timeout
        stream_running = stream is not None and stream_status == "running" and (heartbeat_fresh or frame_fresh)
        if stream is None and frame_fresh:
            stream_status = "running"
        elif stream and stream_status == "running" and not stream_running:
            stream_status = "stopped"
        has_live_frame = has_frame and (stream_running or (stream is None and frame_fresh))

        if analytics_running and has_live_frame and latest_frame:
            result = await _get_realtime_count_snapshot(service, camera.id, latest_frame.path, zones)
            zone_counts = result["zone_counts"]
            detection_boxes = result.get("detection_boxes", [])
            total_people_in_zones = int(result.get("total_in_zones", 0))
            total_detected_people = int(result.get("total_detected", total_people_in_zones))
            if not zones:
                total_people_in_zones = total_detected_people
        else:
            zone_counts = {zone.id: 0 for zone in zones}
            detection_boxes = []
            total_detected_people = 0
            total_people_in_zones = 0

        zone_rows = [
            {
                "zone_id": zone.id,
                "zone_name": zone.name,
                "person_count": int(zone_counts.get(zone.id, 0)),
            }
            for zone in zones
        ]

        output.append(
            {
                "camera_id": camera.id,
                "camera_name": camera.name,
                "stream_status": stream_status,
                "frame_timestamp": frame_timestamp,
                "has_live_frame": has_live_frame,
                "total_person_count": total_people_in_zones,
                "total_detected_person_count": total_detected_people,
                "detection_boxes": detection_boxes,
                "zones": zone_rows,
            }
        )

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "detector_available": service.is_available(),
        "analytics_running": analytics_running,
        "cameras": output,
    }


async def latest_frame(
    session: AsyncSession,
    camera_id: int,
    zone_id: int | None = None,
    analytics_mode: str | None = None,
):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    frame = await _resolve_existing_frame(session, camera_id, prefer_first=False)
    if not frame:
        raise HTTPException(status_code=404, detail="No captured frames found for this camera yet.")

    path = Path(frame.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Captured frame file is missing on disk. Re-capture this camera.")

    zone = await crud.get_zone(session, zone_id) if zone_id is not None else None
    if zone is not None and int(getattr(zone, "camera_id", 0)) != int(camera_id):
        zone = None
    rendered_payload = await _render_frame_payload(session, camera_id, frame, path, zone, analytics_mode)
    if rendered_payload is not None:
        return Response(content=rendered_payload, media_type="image/jpeg")

    return FileResponse(str(path), media_type="image/jpeg")


async def first_frame(
    session: AsyncSession,
    camera_id: int,
    zone_id: int | None = None,
    analytics_mode: str | None = None,
):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    frame = await _resolve_existing_frame(session, camera_id, prefer_first=True)
    if not frame:
        raise HTTPException(status_code=404, detail="No captured frames found for this camera yet.")

    path = Path(frame.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="First captured frame file is missing on disk. Re-capture this camera.")

    zone = await crud.get_zone(session, zone_id) if zone_id is not None else None
    if zone is not None and int(getattr(zone, "camera_id", 0)) != int(camera_id):
        zone = None
    rendered_payload = await _render_frame_payload(session, camera_id, frame, path, zone, analytics_mode)
    if rendered_payload is not None:
        return Response(content=rendered_payload, media_type="image/jpeg")

    return FileResponse(str(path), media_type="image/jpeg")


async def facial_expression_realtime(
    session: AsyncSession,
    camera_id: int,
    zone_id: int | None = None,
) -> dict[str, Any]:
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    service = _get_facial_expression_service()
    detector_available = service.is_available()

    zones_for_camera = await crud.list_zones(session, camera_id=camera_id)
    zone = await crud.get_zone(session, zone_id) if zone_id is not None else None
    if zone is not None and int(getattr(zone, "camera_id", 0)) != int(camera_id):
        raise HTTPException(status_code=404, detail="Zone not found for this camera")

    zone_filter = zone if zone is not None else _zone_filter_from_zones(zones_for_camera)
    zone_name = zone.name if zone is not None else ("All Zones" if zone_filter is not None else None)

    frame = await _resolve_existing_frame(session, camera_id, prefer_first=False)
    if not frame:
        base_summary = service.summarize_detections([])
        return {
            "camera_id": camera_id,
            "zone_id": zone_id,
            "zone_name": zone_name,
            "frame_id": None,
            "frame_timestamp": None,
            "detector_available": detector_available,
            "zone_filter_available": zone_filter is not None,
            **base_summary,
        }

    path = Path(frame.path)
    if not path.exists():
        base_summary = service.summarize_detections([])
        return {
            "camera_id": camera_id,
            "zone_id": zone_id,
            "zone_name": zone_name,
            "frame_id": frame.id,
            "frame_timestamp": frame.timestamp.isoformat() if frame.timestamp else None,
            "detector_available": detector_available,
            "zone_filter_available": zone_filter is not None,
            **base_summary,
        }

    detections: list[dict[str, Any]] = []
    if zone_filter is not None:
        try:
            import cv2

            image = cv2.imread(str(path))
            if image is not None:
                frame_token = f"{frame.id}:{path.stat().st_mtime_ns}"
                detections = await _get_facial_detections_for_frame(
                    camera_id,
                    frame_token,
                    image,
                    zone_filter,
                    non_blocking=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Facial realtime snapshot failed: %s", exc)

    summary = service.summarize_detections(detections)
    return {
        "camera_id": camera_id,
        "zone_id": zone_id,
        "zone_name": zone_name,
        "frame_id": frame.id,
        "frame_timestamp": frame.timestamp.isoformat() if frame.timestamp else None,
        "detector_available": detector_available,
        "zone_filter_available": zone_filter is not None,
        **summary,
    }


def _capture_rtsp_source_frame(rtsp_url: str) -> bytes:
    command = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        settings.ffmpeg_log_level,
        "-rtsp_transport",
        settings.ffmpeg_rtsp_transport,
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=_SOURCE_FRAME_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg binary not found: {settings.ffmpeg_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail="Timed out while capturing a frame from the RTSP source.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to capture RTSP frame: {exc}") from exc

    if result.returncode != 0 or not result.stdout:
        detail = (result.stderr or "").strip().splitlines()[-1] if result.stderr else "ffmpeg returned non-zero status"
        raise HTTPException(status_code=502, detail=f"Failed to capture RTSP frame: {detail}")
    return result.stdout


def _capture_webcam_source_frame(webcam_id: str) -> bytes:
    import sys
    
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
    ]
    
    if sys.platform == "darwin":
        cmd.extend(["-f", "avfoundation", "-framerate", "30", "-i", webcam_id])
    elif sys.platform.startswith("linux"):
        cmd.extend(["-f", "v4l2", "-framerate", "30", "-i", f"/dev/video{webcam_id}"])
    elif sys.platform == "win32":
        cmd.extend(["-f", "dshow", "-i", f"video={webcam_id}"])
    else:
        cmd.extend(["-i", webcam_id])
        
    cmd.extend([
        "-frames:v", "1",
        "-q:v", "2",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "pipe:1"
    ])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=5.0,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg binary not found: {settings.ffmpeg_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Timed out while capturing a frame from webcam.") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to capture Webcam frame: {exc}") from exc

    if result.returncode != 0 or not result.stdout:
        print(f"DEBUG ffmpeg capture failed! returncode={result.returncode}, stderr={result.stderr}")
        detail = (result.stderr or b"").decode(errors="ignore").strip().splitlines()[-1] if result.stderr else "ffmpeg returned non-zero status"
        raise HTTPException(status_code=502, detail=f"Failed to capture Webcam frame: {detail}")
    return result.stdout


async def source_frame(session: AsyncSession, camera_id: int):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    source_url = (camera.rtsp_url or "").strip()
    if not source_url:
        raise HTTPException(status_code=400, detail="Camera source URL required")

    scheme = urlparse(source_url).scheme.lower()
    print(f"DEBUG: source_frame called for camera {camera_id}")
    print(f"DEBUG: source_url: {source_url}, scheme: {scheme}")
    
    if scheme == "webcam":
        parsed = urlparse(source_url)
        webcam_id = parsed.netloc or parsed.path or "0"
        print(f"DEBUG: Selected webcam id: {webcam_id}")
        payload = await asyncio.to_thread(_capture_webcam_source_frame, webcam_id)
        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
        
    if scheme in {"rtsp", "rtsps"}:
        payload = await asyncio.to_thread(_capture_rtsp_source_frame, resolve_rtsp_url(source_url))
        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    if scheme == "file":
        return await first_frame(session, camera_id)

    raise HTTPException(
        status_code=400,
        detail="Unsupported camera source URL. Use rtsp://, rtsps://, file://, or webcam://",
    )


async def ingest_browser_frame(
    session: AsyncSession,
    camera_id: int,
    payload: bytes,
    *,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    source_url = (camera.rtsp_url or "").strip()
    scheme = urlparse(source_url).scheme.lower()
    if scheme != "webcam":
        raise HTTPException(
            status_code=400,
            detail="Browser frame ingest is only supported for webcam sources.",
        )

    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded frame is empty.")

    stream = await crud.get_stream_by_camera(session, camera_id)
    if not stream:
        stream = await crud.create_stream(session, camera_id, None)

    frame_timestamp = datetime.utcnow()
    frame_width = int(width) if width and width > 0 else None
    frame_height = int(height) if height and height > 0 else None
    base_dir = Path(settings.frames_dir) / f"camera_{camera_id}" / f"stream_{stream.id}"
    base_dir.mkdir(parents=True, exist_ok=True)

    frame_name = f"browser_{frame_timestamp.strftime('%Y%m%d%H%M%S%f')}.jpg"
    frame_path = base_dir / frame_name
    await asyncio.to_thread(frame_path.write_bytes, payload)

    frame = await crud.create_frame(
        session,
        camera_id=camera_id,
        stream_id=stream.id,
        path=str(frame_path),
        timestamp=frame_timestamp,
        width=frame_width,
        height=frame_height,
        size_bytes=len(payload),
    )
    await crud.update_stream_heartbeat(session, stream, frame_timestamp)

    return {
        "accepted": True,
        "camera_id": camera_id,
        "stream_id": stream.id,
        "frame_id": frame.id,
        "frame_timestamp": frame.timestamp.isoformat() if frame.timestamp else frame_timestamp.isoformat(),
    }


async def mjpeg_stream(
    camera_id: int,
    request: Request,
    zone_id: int | None = None,
    analytics_mode: str | None = None,
):
    async with AsyncSessionLocal() as session:
        camera = await crud.get_camera(session, camera_id)
        zone = await crud.get_zone(session, zone_id) if zone_id else None
        if zone is not None and int(getattr(zone, "camera_id", 0)) != int(camera_id):
            zone = None
        draw_entry_exit_lines = _is_entry_exit_mode(analytics_mode) and zone is None
        draw_facial_expression = _is_facial_expression_mode(analytics_mode)
        draw_trajectory_heatmap = _is_trajectory_heatmap_mode(analytics_mode)
        draw_person_reid = _is_person_reid_mode(analytics_mode)
        draw_face_recognition = _is_face_recognition_mode(analytics_mode)
        needs_camera_zones = (
            draw_entry_exit_lines
            or (draw_facial_expression and zone is None)
        )
        camera_zones = await crud.list_zones(session, camera_id=camera_id) if needs_camera_zones else []

    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    boundary = "frame"
    frame_interval_seconds = max(0.01, float(getattr(settings, "frame_interval_seconds", 0.04)))
    idle_wait_seconds = min(0.2, max(0.01, frame_interval_seconds))
    same_token_wait_seconds = min(0.04, max(0.005, frame_interval_seconds / 4.0))
    post_yield_wait_seconds = min(0.01, max(0.001, frame_interval_seconds * 0.25))

    async def _stream():
        last_token = ""
        while True:
            if await request.is_disconnected():
                break

            async with AsyncSessionLocal() as live_session:
                frame = await crud.get_latest_frame(live_session, camera_id)

            if not frame:
                await asyncio.sleep(idle_wait_seconds)
                continue

            path = Path(frame.path)
            if not path.exists():
                await asyncio.sleep(idle_wait_seconds)
                continue

            token = f"{frame.id}:{path.stat().st_mtime_ns}"
            if token == last_token:
                await asyncio.sleep(same_token_wait_seconds)
                continue

            payload = path.read_bytes()

            if (
                zone is not None
                or draw_entry_exit_lines
                or draw_facial_expression
                or draw_trajectory_heatmap
                or draw_person_reid
                or draw_face_recognition
            ):
                async with AsyncSessionLocal() as render_session:
                    rendered_payload = await _render_frame_payload(
                        render_session,
                        camera_id,
                        frame,
                        path,
                        zone,
                        analytics_mode,
                        payload=payload,
                        camera_zones=camera_zones if (draw_entry_exit_lines or draw_facial_expression) else None,
                        frame_token=token,
                    )
                if rendered_payload is not None:
                    payload = rendered_payload

            headers = (
                f"--{boundary}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n"
            ).encode("utf-8")
            yield headers + payload + b"\r\n"
            last_token = token
            await asyncio.sleep(post_yield_wait_seconds)

    return StreamingResponse(
        _stream(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
