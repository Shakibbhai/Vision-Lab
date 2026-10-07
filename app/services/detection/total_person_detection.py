from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.detection.yolo_threadsafe import build_locked_callable
from app.services.detection.yolo_person_classes import resolve_person_class_ids

logger = logging.getLogger(__name__)


class TotalPersonDetectionService:
    """Run plain per-frame person detection and aggregate total counts."""

    def __init__(self) -> None:
        self._use_yolo = settings.analytics_use_yolo
        self._yolo = None
        self._predict = None
        self._cv2 = None
        self._np = None
        self._person_class_ids: list[int] = [0]

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
            logger.warning("Failed to initialize total person detector: %s", exc)
            self._use_yolo = False

    def is_available(self) -> bool:
        return (
            self._use_yolo
            and self._yolo is not None
            and self._predict is not None
            and self._cv2 is not None
            and self._np is not None
        )

    def analyze_frames(self, frames: list[Any]) -> dict[str, Any]:
        if not self.is_available():
            return {
                "available": False,
                "processed_frames": 0,
                "total_person_detections": 0,
                "max_person_count": 0,
                "avg_person_count": 0.0,
                "series": [],
            }

        processed_frames = 0
        total_person_detections = 0
        max_person_count = 0
        series: list[dict[str, Any]] = []

        for frame in frames:
            path_value = getattr(frame, "path", "")
            if not path_value:
                continue

            frame_path = Path(path_value)
            if not frame_path.exists():
                continue

            image = self._cv2.imread(str(frame_path))
            if image is None:
                continue

            person_count = self._count_people(image)
            timestamp = getattr(frame, "timestamp", None)

            processed_frames += 1
            total_person_detections += person_count
            max_person_count = max(max_person_count, person_count)

            series.append(
                {
                    "frame_id": int(getattr(frame, "id", 0)),
                    "timestamp": timestamp.isoformat() if timestamp else None,
                    "person_count": person_count,
                }
            )

        avg_person_count = round(total_person_detections / processed_frames, 2) if processed_frames else 0.0
        return {
            "available": True,
            "processed_frames": processed_frames,
            "total_person_detections": total_person_detections,
            "max_person_count": max_person_count,
            "avg_person_count": avg_person_count,
            "series": series,
        }

    def _count_people(self, image: Any) -> int:
        try:
            predict_kwargs: dict[str, Any] = {
                "conf": settings.yolo_confidence,
                "iou": settings.yolo_iou,
                "verbose": settings.yolo_verbose,
            }
            if self._person_class_ids:
                predict_kwargs["classes"] = self._person_class_ids
            results = self._predict(image, **predict_kwargs)
            if not results or results[0].boxes is None:
                return 0

            boxes = results[0].boxes
            xyxy = boxes.xyxy.cpu().numpy().astype(self._np.float32)
            if len(xyxy) == 0:
                return 0
            if self._person_class_ids:
                cls = boxes.cls.cpu().numpy().astype(self._np.int32)
                mask = self._np.isin(cls, self._person_class_ids)
                xyxy = xyxy[mask]
            return int(len(xyxy))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Total person detection inference failed: %s", exc)
            return 0
