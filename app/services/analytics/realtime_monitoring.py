from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.detection.yolo_threadsafe import build_locked_callable
from app.services.detection.yolo_person_classes import resolve_person_class_ids
from app.services.utils.zone_geometry import region_points_from_polygon

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _CacheEntry:
    camera_id: int
    frame_path: str
    frame_mtime_ns: int
    zone_signature: tuple[tuple[int, str], ...]
    zone_counts: dict[int, int]
    detection_boxes: list[dict[str, Any]]
    total_detected: int
    total_in_zones: int
    updated_at_monotonic: float


class RealtimeMonitoringService:
    def __init__(self) -> None:
        self._use_yolo = settings.analytics_use_yolo
        self._yolo = None
        self._predict = None
        self._cv2 = None
        self._np = None
        self._person_class_ids: list[int] = [0]
        self._cache: dict[tuple[int, tuple[tuple[int, str], ...]], _CacheEntry] = {}
        self._inflight: set[tuple[int, tuple[tuple[int, str], ...]]] = set()
        self._lock = threading.Lock()

        if not self._use_yolo:
            return

        try:
            from ultralytics import YOLO  # type: ignore
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            self._yolo = YOLO(settings.yolo_model_path)
            self._yolo.to(settings.boxmot_device)
            self._predict = build_locked_callable(self._yolo.predict)
            self._cv2 = cv2
            self._np = np
            self._person_class_ids = resolve_person_class_ids(self._yolo)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to initialize realtime YOLO detector: %s", exc)
            self._use_yolo = False

    def is_available(self) -> bool:
        return (
            self._use_yolo
            and self._yolo is not None
            and self._predict is not None
            and self._cv2 is not None
            and self._np is not None
        )

    def default_result(self, zones: list[Any], *, available: bool | None = None) -> dict[str, Any]:
        default_counts = {int(getattr(zone, "id", 0)): 0 for zone in zones}
        return {
            "available": self.is_available() if available is None else available,
            "total_detected": 0,
            "total_in_zones": 0,
            "zone_counts": default_counts,
            "detection_boxes": [],
        }

    def get_cached_result(
        self,
        camera_id: int,
        frame_path: str,
        zones: list[Any],
    ) -> tuple[dict[str, Any], bool]:
        if not frame_path:
            return self.default_result(zones), False
        if not self.is_available():
            return self.default_result(zones, available=False), False

        path = Path(frame_path)
        if not path.exists():
            return self.default_result(zones), False

        stat = path.stat()
        zone_signature = _zone_signature(zones)
        cache_key = (camera_id, zone_signature)
        with self._lock:
            cached = self._cache.get(cache_key)
            if (
                cached
                and cached.frame_path == str(path)
                and cached.frame_mtime_ns == stat.st_mtime_ns
            ):
                return _result_from_cache(cached), False

            fallback = _result_from_cache(cached) if cached else self.default_result(zones)
            if cache_key in self._inflight:
                return fallback, False

            self._inflight.add(cache_key)
        return fallback, True

    def refresh_cached_result(
        self,
        camera_id: int,
        frame_path: str,
        zones: list[Any],
    ) -> dict[str, Any]:
        zone_signature = _zone_signature(zones)
        cache_key = (camera_id, zone_signature)
        try:
            result = self.count_people(frame_path, zones)
            path = Path(frame_path)
            if path.exists() and result.get("available"):
                stat = path.stat()
                with self._lock:
                    self._cache[cache_key] = _CacheEntry(
                        camera_id=camera_id,
                        frame_path=str(path),
                        frame_mtime_ns=stat.st_mtime_ns,
                        zone_signature=zone_signature,
                        zone_counts=dict(result["zone_counts"]),
                        detection_boxes=[dict(box) for box in result["detection_boxes"]],
                        total_detected=int(result["total_detected"]),
                        total_in_zones=int(result["total_in_zones"]),
                        updated_at_monotonic=time.monotonic(),
                    )
                    self._prune_cache_locked(max_entries=32)
            return result
        finally:
            with self._lock:
                self._inflight.discard(cache_key)

    def count_people(self, frame_path: str, zones: list[Any]) -> dict[str, Any]:
        if not frame_path:
            return self.default_result(zones)
        if not self.is_available():
            return self.default_result(zones, available=False)

        path = Path(frame_path)
        if not path.exists():
            return self.default_result(zones)

        image = self._cv2.imread(str(path))
        if image is None:
            return self.default_result(zones)

        frame_h, frame_w = image.shape[:2]
        polygons = {
            int(zone.id): region_points_from_polygon(
                zone.polygon,
                frame_w,
                frame_h,
                use_default_on_invalid=False,
            )
            for zone in zones
        }

        zone_counts = {int(zone.id): 0 for zone in zones}
        try:
            predict_kwargs: dict[str, Any] = {
                "conf": settings.yolo_confidence,
                "iou": settings.yolo_iou,
                "verbose": False,
            }
            if self._person_class_ids:
                predict_kwargs["classes"] = self._person_class_ids
            results = self._predict(image, **predict_kwargs)
            boxes = results[0].boxes if results and results[0].boxes is not None else None
            if boxes is None:
                xyxy = self._np.empty((0, 4), dtype=self._np.float32)
            else:
                xyxy = boxes.xyxy.cpu().numpy().astype(self._np.float32)
                if len(xyxy) > 0 and self._person_class_ids:
                    cls = boxes.cls.cpu().numpy().astype(self._np.int32)
                    mask = self._np.isin(cls, self._person_class_ids)
                    xyxy = xyxy[mask]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Realtime inference failed: %s", exc)
            xyxy = self._np.empty((0, 4), dtype=self._np.float32)

        detection_boxes: list[dict[str, Any]] = []
        total_detected = 0
        for row in xyxy:
            if len(row) < 4:
                continue
            x1, y1, x2, y2 = float(row[0]), float(row[1]), float(row[2]), float(row[3])
            total_detected += 1
            matched_zone_ids = _resolve_zones_for_bbox(x1, y1, x2, y2, polygons, zones)
            
            if not matched_zone_ids:
                detection_box = _normalize_detection_box(x1, y1, x2, y2, frame_w, frame_h, None)
                if detection_box is not None:
                    detection_boxes.append(detection_box)
            else:
                for z_id in matched_zone_ids:
                    detection_box = _normalize_detection_box(x1, y1, x2, y2, frame_w, frame_h, z_id)
                    if detection_box is not None:
                        detection_boxes.append(detection_box)
                    zone_counts[z_id] = int(zone_counts.get(z_id, 0)) + 1

        total_in_zones = int(sum(zone_counts.values()))
        return {
            "available": True,
            "total_detected": total_detected,
            "total_in_zones": total_in_zones,
            "zone_counts": zone_counts,
            "detection_boxes": detection_boxes,
        }

    def _prune_cache_locked(self, *, max_entries: int) -> None:
        if len(self._cache) <= max_entries:
            return
        stale_keys = sorted(
            self._cache,
            key=lambda key: self._cache[key].updated_at_monotonic,
        )[: len(self._cache) - max_entries]
        for key in stale_keys:
            self._cache.pop(key, None)


_service: RealtimeMonitoringService | None = None


def get_realtime_monitoring_service() -> RealtimeMonitoringService:
    global _service
    if _service is None:
        _service = RealtimeMonitoringService()
    return _service


def _zone_signature(zones: list[Any]) -> tuple[tuple[int, str], ...]:
    signature: list[tuple[int, str]] = []
    for zone in zones:
        polygon_signature = json.dumps(getattr(zone, "polygon", {}), sort_keys=True, separators=(",", ":"))
        signature.append((int(getattr(zone, "id", 0)), polygon_signature))
    signature.sort(key=lambda item: item[0])
    return tuple(signature)


def _result_from_cache(cached: _CacheEntry) -> dict[str, Any]:
    return {
        "available": True,
        "total_detected": cached.total_detected,
        "total_in_zones": cached.total_in_zones,
        "zone_counts": dict(cached.zone_counts),
        "detection_boxes": [dict(box) for box in cached.detection_boxes],
    }


def _resolve_zones_for_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    zone_points: dict[int, list[list[tuple[int, int]]]],
    zones: list[Any],
) -> list[int]:
    # Draw/count only when the person's footpoint is inside a configured zone.
    foot_x = (x1 + x2) / 2.0
    foot_y = y2
    return _resolve_zones_for_point(foot_x, foot_y, zone_points, zones)


def _resolve_zones_for_point(
    x: float,
    y: float,
    zone_points: dict[int, list[list[tuple[int, int]]]],
    zones: list[Any],
) -> list[int]:
    matched_zones = []
    for zone in zones:
        zone_id = int(getattr(zone, "id", 0))
        polygons = zone_points.get(zone_id) or []
        for polygon in polygons:
            if len(polygon) < 3:
                continue
            if _point_in_polygon(x, y, polygon):
                matched_zones.append(zone_id)
                break
    return matched_zones


def _point_in_polygon(x: float, y: float, polygon: list[tuple[int, int]]) -> bool:
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _normalize_detection_box(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    frame_w: int,
    frame_h: int,
    zone_id: int | None,
) -> dict[str, Any] | None:
    if frame_w <= 0 or frame_h <= 0:
        return None

    nx1 = max(0.0, min(1.0, x1 / frame_w))
    ny1 = max(0.0, min(1.0, y1 / frame_h))
    nx2 = max(0.0, min(1.0, x2 / frame_w))
    ny2 = max(0.0, min(1.0, y2 / frame_h))
    if nx2 <= nx1 or ny2 <= ny1:
        return None

    return {
        "x1": nx1,
        "y1": ny1,
        "x2": nx2,
        "y2": ny2,
        "zone_id": zone_id,
        "in_zone": zone_id is not None,
    }
