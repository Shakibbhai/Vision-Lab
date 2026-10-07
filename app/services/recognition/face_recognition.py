from __future__ import annotations

import logging
from collections import Counter
from typing import Any
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models import FaceReference
from app.db.session import AsyncSessionLocal
from app.services.utils.zone_geometry import region_points_from_polygon
from app.core.config import settings

logger = logging.getLogger(__name__)

def cosine_distance(source_representation, test_representation):
    a = source_representation
    b = test_representation

    if type(a) == list:
        a = sum([x*y for x,y in zip(a,a)])**0.5
        b = sum([x*y for x,y in zip(b,b)])**0.5
        if a == 0 or b == 0:
            return 1.0
        dot = sum([x*y for x,y in zip(source_representation, test_representation)])
        return 1.0 - (dot / (a * b))
    import numpy as np
    a = np.matmul(np.transpose(source_representation), source_representation)
    b = np.matmul(np.transpose(test_representation), test_representation)

    if (isinstance(a, np.ndarray) and a.item(0) == 0) or (isinstance(b, np.ndarray) and b.item(0) == 0):
        return 1.0
    if (not isinstance(a, np.ndarray) and a == 0) or (not isinstance(b, np.ndarray) and b == 0):
        return 1.0

    return 1.0 - (np.matmul(np.transpose(source_representation), test_representation) / (np.sqrt(a) * np.sqrt(b)))

class FaceRecognitionService:
    """Face recognition service using ArcFace and DeepFace."""

    def __init__(self) -> None:
        self._available = False
        self._init_error: str | None = None
        self._cv2 = None
        self._deepface = None
        self._face_cascade = None
        self._threshold = 0.68  # ArcFace standard threshold

        try:
            import cv2  # type: ignore
            from deepface import DeepFace  # type: ignore

            self._cv2 = cv2
            self._deepface = DeepFace
            cascade_dir = getattr(getattr(cv2, "data", None), "haarcascades", "")
            if cascade_dir:
                cascade = cv2.CascadeClassifier(f"{cascade_dir}haarcascade_frontalface_default.xml")
                if cascade is not None and not cascade.empty():
                    self._face_cascade = cascade
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._init_error = str(exc)
            logger.warning("Failed to initialize Face Recognition service: %s", exc)

    def is_available(self) -> bool:
        return self._available and self._cv2 is not None and self._deepface is not None

    def error_message(self) -> str:
        return self._init_error or "Face Recognition service is unavailable"

    def _resize_for_detection(
        self,
        image: Any,
        *,
        max_side: int = 960,
    ) -> tuple[Any, float]:
        frame_h, frame_w = image.shape[:2]
        longest_side = max(frame_w, frame_h)
        if longest_side <= max_side:
            return image, 1.0

        scale = max_side / float(longest_side)
        resized = self._cv2.resize(
            image,
            (max(1, int(round(frame_w * scale))), max(1, int(round(frame_h * scale)))),
            interpolation=self._cv2.INTER_AREA,
        )
        return resized, scale

    def _expand_box(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        frame_width: int,
        frame_height: int,
        *,
        margin_ratio: float = 0.18,
    ) -> tuple[int, int, int, int]:
        margin_x = int(round((x2 - x1) * margin_ratio))
        margin_y = int(round((y2 - y1) * margin_ratio))
        return (
            max(0, x1 - margin_x),
            max(0, y1 - margin_y),
            min(frame_width, x2 + margin_x),
            min(frame_height, y2 + margin_y),
        )

    def _detect_candidate_face_boxes(self, image: Any) -> list[tuple[int, int, int, int]]:
        if self._face_cascade is None or image is None:
            return []

        frame_h, frame_w = image.shape[:2]
        if frame_h <= 0 or frame_w <= 0:
            return []

        resized, scale = self._resize_for_detection(image)
        gray = self._cv2.cvtColor(resized, self._cv2.COLOR_BGR2GRAY)
        gray = self._cv2.equalizeHist(gray)

        min_face_size = max(12, int(getattr(settings, "facial_expression_min_face_size", 15)))
        scaled_min_face_size = max(8, int(round(min_face_size * scale)))
        scale_factor = max(1.01, float(getattr(settings, "facial_expression_face_scale_factor", 1.1)))
        min_neighbors = max(1, int(getattr(settings, "facial_expression_face_min_neighbors", 3)))

        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=scale_factor,
            minNeighbors=min_neighbors,
            minSize=(scaled_min_face_size, scaled_min_face_size),
        )

        if len(faces) == 0:
            return []

        inv_scale = 1.0 / scale
        boxes: list[tuple[int, int, int, int]] = []
        for x, y, w, h in faces:
            x1 = int(round((x * inv_scale)))
            y1 = int(round((y * inv_scale)))
            x2 = int(round(((x + w) * inv_scale)))
            y2 = int(round(((y + h) * inv_scale)))
            boxes.append((x1, y1, x2, y2))
        return boxes

    def _zone_polygons(self, zone: Any | None, frame_width: int, frame_height: int) -> list[Any]:
        if zone is None:
            return []

        polygons: list[Any] = []
        polygon_data = getattr(zone, "polygon", None)
        import numpy as np

        raw_polygons = region_points_from_polygon(
            polygon_data,
            frame_width,
            frame_height,
            use_default_on_invalid=False,
        )
        for poly in raw_polygons:
            polygons.append(np.array(poly, dtype=np.float32).reshape((-1, 1, 2)))
        return polygons

    def detect_faces_with_matches(
        self,
        image: Any,
        zone: Any | None = None,
        db_faces: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.is_available() or image is None:
            return []

        if db_faces is None:
            db_faces = []

        frame_h, frame_w = image.shape[:2]
        if frame_h <= 0 or frame_w <= 0:
            return []

        zone_polygons = self._zone_polygons(zone, frame_w, frame_h)
        import cv2

        detections: list[dict[str, Any]] = []
        for cx1, cy1, cx2, cy2 in self._detect_candidate_face_boxes(image):
            cx, cy = (cx1 + cx2) // 2, (cy1 + cy2) // 2
            in_zone = True
            if zone_polygons:
                in_zone = False
                for poly in zone_polygons:
                    if cv2.pointPolygonTest(poly, (cx, cy), False) >= 0:
                        in_zone = True
                        break
            if not in_zone:
                continue

            x1, y1, x2, y2 = self._expand_box(cx1, cy1, cx2, cy2, frame_w, frame_h)
            face_crop = image[y1:y2, x1:x2]
            if face_crop.size == 0:
                continue

            try:
                rep_results = self._deepface.represent(
                    img_path=face_crop,
                    model_name="ArcFace",
                    detector_backend="opencv",
                    enforce_detection=False,
                )
                if not rep_results:
                    continue

                test_embedding = rep_results[0]["embedding"]
                best_match = None
                best_distance = 999.0
                for db_f in db_faces:
                    embedding = getattr(db_f, "embedding", None)
                    if embedding is None:
                        continue
                    dist = cosine_distance(embedding, test_embedding)
                    try:
                        dist_value = float(dist)
                    except (TypeError, ValueError):
                        continue
                    if dist_value < best_distance and dist_value <= self._threshold:
                        best_distance = dist_value
                        best_match = getattr(db_f, "name", None)

                detections.append(
                    {
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "name": best_match or "Unknown",
                        "distance": float(best_distance) if best_match else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Face recognition overlay detection failed: %s", exc)

        return detections

    async def _get_database_faces(self):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(FaceReference))
            return list(result.scalars())

    def analyze_frames(self, frames: list[Any], zone: Any | None = None) -> dict[str, Any]:
        if not self.is_available() or not frames:
            return {}

        # Load known faces from DB synchronously using a bounded loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        db_faces = loop.run_until_complete(self._get_database_faces())
        loop.close()

        processed_frames = 0
        total_analyzed_faces = 0
        overall_recognized_counts = Counter()
        series_results = []

        target_total_samples = int(getattr(settings, "facial_expression_target_total_samples", 12))
        analyze_interval = max(3, len(frames) // max(1, target_total_samples))

        for i, frame_record in enumerate(frames):
            if i % analyze_interval != 0 and i != len(frames) - 1:
                continue

            frame_path = getattr(frame_record, "path", None)
            if not frame_path:
                continue

            image = self._cv2.imread(frame_path)
            if image is None:
                continue

            processed_frames += 1

            detections = self.detect_faces_with_matches(image, zone, db_faces)
            frame_analyzed_faces = len(detections)
            total_analyzed_faces += frame_analyzed_faces
            frame_matches = []
            for detection in detections:
                name = str(detection.get("name") or "").strip()
                distance = detection.get("distance")
                if name and name != "Unknown" and distance is not None:
                    overall_recognized_counts[name] += 1
                    frame_matches.append(
                        {
                            "name": name,
                            "distance": float(distance),
                        }
                    )

            timestamp = getattr(frame_record, "timestamp", None)
            series_results.append({
                "frame_id": getattr(frame_record, "id", None),
                "timestamp": timestamp.isoformat() if timestamp else None,
                "analyzed_faces": frame_analyzed_faces,
                "matched_faces": frame_matches
            })

        return {
            "processed_frames": processed_frames,
            "analyzed_faces": total_analyzed_faces,
            "recognized_persons_counts": dict(overall_recognized_counts),
            "series": series_results
        }
