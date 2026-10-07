from __future__ import annotations

import logging
import re
from typing import Any, Iterable

import numpy as np

from app.core.config import settings
from app.services.detection.yolo_threadsafe import build_locked_callable

logger = logging.getLogger(__name__)


_PERSON_TERMS = {
    "person",
    "people",
    "pedestrian",
    "pedestrians",
    "human",
    "humans",
}


def resolve_person_class_ids(model: Any) -> list[int]:
    """
    Attempts to find YOLO class IDs that represent people by scanning model names.
    Falls back to class id 0 if no match is found.
    """
    names = _extract_model_names(model)
    if not names:
        return [0]

    person_ids = _match_person_ids(names)
    if person_ids:
        return person_ids

    if len(names) == 1:
        only_id = next(iter(names))
        return [only_id]

    if 0 in names:
        return [0]

    first_id = sorted(names.keys())[0]
    return [first_id]


def _extract_model_names(model: Any) -> dict[int, str]:
    raw_sources = (
        getattr(model, "names", None),
        getattr(getattr(model, "model", None), "names", None),
    )
    for raw in raw_sources:
        names = _normalize_names(raw)
        if names:
            return names
    return {}


def _normalize_names(raw: Any) -> dict[int, str]:
    if raw is None:
        return {}

    names: dict[int, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                class_id = int(key)
            except (TypeError, ValueError):
                continue
            names[class_id] = str(value)
        return names

    if isinstance(raw, (list, tuple)):
        for class_id, value in enumerate(raw):
            names[class_id] = str(value)
        return names

    return {}


def _match_person_ids(names: dict[int, str]) -> list[int]:
    ids: list[int] = []
    for class_id, label in names.items():
        normalized = _normalize_label(label)
        if not normalized:
            continue
        words = set(normalized.split())
        if words.intersection(_PERSON_TERMS):
            ids.append(class_id)
            continue
        if "person" in normalized or "people" in normalized or "pedestrian" in normalized:
            ids.append(class_id)
    return sorted(set(ids))


def _normalize_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def detections_to_numpy(results: Any, person_class_ids: list[int] | None = None) -> np.ndarray:
    """
    Converts ultralytics YOLO results to a Nx6 numpy array: [x1, y1, x2, y2, conf, cls].
    Filters by person_class_ids if provided.
    """
    if not results or results[0].boxes is None:
        return np.empty((0, 6), dtype=np.float32)

    boxes = results[0].boxes
    xyxy = boxes.xyxy.cpu().numpy().astype(np.float32)
    conf = boxes.conf.cpu().numpy().astype(np.float32)
    cls = boxes.cls.cpu().numpy().astype(np.float32)

    if len(xyxy) == 0:
        return np.empty((0, 6), dtype=np.float32)

    if person_class_ids:
        mask = np.isin(cls.astype(np.int32), person_class_ids)
        xyxy = xyxy[mask]
        conf = conf[mask]
        cls = cls[mask]
        if len(xyxy) == 0:
            return np.empty((0, 6), dtype=np.float32)

    return np.column_stack((xyxy, conf, cls)).astype(np.float32)


def get_bbox_footpoint(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
    """Calculates the bottom-center point of a bounding box."""
    return ((x1 + x2) / 2.0, y2)


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]] | list[tuple[int, int]]) -> bool:
    """Ray-casting algorithm to check if a point is inside a polygon."""
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


def resolve_zone_for_point(
    x: float,
    y: float,
    zones: list[Any],
    zone_points: dict[int, list[list[tuple[float, float]]]] | dict[int, list[list[tuple[int, int]]]],
) -> int | None:
    """Finds the first zone ID that contains the given point."""
    for zone in zones:
        zone_id = int(getattr(zone, "id", 0))
        polygons = zone_points.get(zone_id) or []
        for polygon in polygons:
            if not polygon or len(polygon) < 3:
                continue
            if point_in_polygon(x, y, polygon):
                return zone_id
    return None


def resolve_zone_for_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    zones: list[Any],
    zone_points: dict[int, list[list[tuple[float, float]]]] | dict[int, list[list[tuple[int, int]]]],
    prefer_footpoint: bool = True,
    zone_masks: dict[int, np.ndarray] | None = None,
) -> int | None:
    """Finds the zone ID for a bounding box, checking footpoint first, then center."""
    if zone_masks:
        if prefer_footpoint:
            fx, fy = get_bbox_footpoint(x1, y1, x2, y2)
            zone_id = resolve_zone_masked(fx, fy, zone_masks)
            if zone_id is not None:
                return zone_id
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        return resolve_zone_masked(cx, cy, zone_masks)

    if prefer_footpoint:
        fx, fy = get_bbox_footpoint(x1, y1, x2, y2)
        zone_id = resolve_zone_for_point(fx, fy, zones, zone_points)
        if zone_id is not None:
            return zone_id

    # Fallback/alternative: check center
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    return resolve_zone_for_point(cx, cy, zones, zone_points)


def create_zone_mask(polygons: list[list[tuple[int, int]]], width: int, height: int) -> np.ndarray:
    """Creates a boolean mask for one or more polygons."""
    import cv2  # type: ignore

    mask = np.zeros((height, width), dtype=np.uint8)
    for poly in polygons:
        if not poly or len(poly) < 3:
            continue
        pts = np.array(poly, dtype=np.int32)
        cv2.fillPoly(mask, [pts], 1)
    return mask.astype(bool)


def resolve_zone_masked(
    x: float,
    y: float,
    zone_masks: dict[int, np.ndarray],
) -> int | None:
    """Finds the zone ID using pre-rendered masks."""
    ix, iy = int(round(x)), int(round(y))
    # We assume zone_masks contains masks of the same shape (height, width)
    # Check bounds once
    first_mask = next(iter(zone_masks.values()), None)
    if first_mask is None:
        return None
    
    h, w = first_mask.shape
    if not (0 <= ix < w and 0 <= iy < h):
        return None

    for zone_id, mask in zone_masks.items():
        if mask[iy, ix]:
            return zone_id
    return None


def get_polygon_bounds(
    polygons: list[list[tuple[float, float]]] | list[list[tuple[int, int]]],
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    """Calculates the bounding box that encompasses all given polygons."""
    xs: list[float] = []
    ys: list[float] = []
    for polygon in polygons:
        for point in polygon:
            xs.append(float(point[0]))
            ys.append(float(point[1]))

    if not xs or not ys:
        return None

    x1 = max(0, int(min(xs)))
    y1 = max(0, int(min(ys)))
    x2 = min(width, int(max(xs)) + 1)
    y2 = min(height, int(max(ys)) + 1)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def is_bbox_in_zone_masked(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    zone_masks: list[np.ndarray],
) -> bool:
    """Checks if a bounding box (various points) is inside any of the given masks."""
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    # Check multiple points for better coverage in face detection
    points = [(cx, cy), (cx, y2), (x1, y1), (x2, y1), (x1, y2), (x2, y2)]
    
    for ux, uy in points:
        ix, iy = int(round(ux)), int(round(uy))
        for mask in zone_masks:
            h, w = mask.shape
            if 0 <= ix < w and 0 <= iy < h and mask[iy, ix]:
                return True
    return False


class VisionBase:
    """
    Unified inference engine for YOLO models with support for CUDA and dynamic batching.
    """

    def __init__(self, model_path: str, device: str = "cpu"):
        from ultralytics import YOLO
        import torch

        # Normalize bare integer device IDs like "0", "1" to "cuda:0", "cuda:1"
        if device.isdigit():
            device = f"cuda:{device}"

        self.device = device
        if self.device != "cpu" and not torch.cuda.is_available():
            logger.warning("CUDA requested but not available. Falling back to CPU.")
            self.device = "cpu"

        logger.info("Initializing YOLO model from %s on %s", model_path, self.device)
        self.model = YOLO(model_path)
        self.model.to(self.device)

        # Warm up the model
        if self.device.startswith("cuda"):
            dummy = np.zeros((640, 640, 3), dtype=np.uint8)
            self.model.predict(dummy, verbose=False)

        self._predict = build_locked_callable(self.model.predict)

    def predict_batch(
        self,
        frames: list[np.ndarray],
        batch_size: int | None = None,
        **kwargs,
    ) -> list[np.ndarray]:
        """
        Runs inference on a list of frames in batches.
        Returns a list of Nx6 numpy arrays (one for each frame).
        """
        if not frames:
            return []

        if batch_size is None:
            batch_size = getattr(settings, "inference_batch_size", 4)

        all_results = []
        person_class_ids = kwargs.get("classes")

        # Process in batches
        for i in range(0, len(frames), batch_size):
            batch = frames[i : i + batch_size]
            # Ultralytics predict handles list of images as a batch
            results = self._predict(batch, **kwargs)
            for res in results:
                all_results.append(detections_to_numpy([res], person_class_ids))

        return all_results

    def predict_single(self, frame: np.ndarray, **kwargs) -> np.ndarray:
        """Runs inference on a single frame."""
        person_class_ids = kwargs.get("classes")
        results = self._predict(frame, **kwargs)
        return detections_to_numpy(results, person_class_ids)


_shared_yolo: VisionBase | None = None


def get_shared_yolo() -> VisionBase:
    """Returns a singleton VisionBase instance for the main YOLO model."""
    global _shared_yolo
    if _shared_yolo is None:
        _shared_yolo = VisionBase(
            model_path=settings.yolo_model_path,
            device=settings.boxmot_device,
        )
    return _shared_yolo
