"""Queue management service for in-zone queue occupancy counting with YOLO."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections import deque
import time

from app.core.config import settings
from app.db import crud
from app.db.models import QueueJobStatus
from app.services.detection.yolo_threadsafe import build_locked_callable
from app.services.detection.yolo_person_classes import resolve_person_class_ids
from app.services.utils.zone_geometry import region_points_from_polygon

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

class WaitTimeEstimator:
    """
    Hybrid wait time estimator combining:
      1. Capacity-overflow model (from main_heatmap_multi.py):
           wait = avg_dwell_time × (number_over_capacity / capacity)
      2. Throughput-based Little's Law fallback:
           wait = queue_len / throughput
    """

    def __init__(self, window_seconds: float = 30.0, min_exits: int = 1, zone_capacity: int | None = None):
        self.window_seconds = window_seconds
        self.min_exits = min_exits
        self.zone_capacity = zone_capacity  # from Zone.capacity DB field

        self._inside_ids = set()
        self._exit_timestamps = deque()  # timestamps when an ID exits ROI

        # Dwell-time tracking
        self._enter_time = {}  # track_id -> enter timestamp
        self._dwell_samples = deque(maxlen=200)  # recent dwell times

    def update(self, track_ids_in_roi, now=None):
        now = now or time.time()
        track_ids_in_roi = set(track_ids_in_roi)

        # IDs that just entered / exited
        entered = track_ids_in_roi - self._inside_ids
        exited = self._inside_ids - track_ids_in_roi

        # Record enter times
        for tid in entered:
            self._enter_time[tid] = now

        # Record exits + dwell samples
        for tid in exited:
            self._exit_timestamps.append(now)
            if tid in self._enter_time:
                self._dwell_samples.append(now - self._enter_time.pop(tid, now))

        self._inside_ids = track_ids_in_roi

        # Drop old exits outside the rolling window
        cutoff = now - self.window_seconds
        while self._exit_timestamps and self._exit_timestamps[0] < cutoff:
            self._exit_timestamps.popleft()

        queue_len = len(self._inside_ids)

        # Throughput (exits/sec)
        exits = len(self._exit_timestamps)
        throughput = exits / self.window_seconds if self.window_seconds > 0 else 0.0

        # Average dwell time (mean of recent samples)
        avg_dwell = None
        dwell_median = None
        if len(self._dwell_samples) >= 1:
            s = sorted(self._dwell_samples)
            dwell_median = s[len(s) // 2]
            avg_dwell = sum(self._dwell_samples) / len(self._dwell_samples)

        # --- Capacity-overflow model (from main_heatmap_multi.py) ---
        # queue_wait_time = avg_person_time * (number_waiting / MAX_CAPACITY)
        capacity_wait = None
        number_over_capacity = 0
        if self.zone_capacity is not None and self.zone_capacity > 0:
            number_over_capacity = max(0, queue_len - self.zone_capacity)
            if number_over_capacity > 0 and avg_dwell is not None:
                capacity_wait = avg_dwell * (number_over_capacity / self.zone_capacity)
            elif number_over_capacity == 0:
                capacity_wait = None

        # --- Throughput-based fallback (Little's Law) ---
        throughput_wait = None
        if exits >= self.min_exits and throughput > 1e-6:
            throughput_wait = queue_len / throughput

        # --- Final wait time: prefer capacity model, fallback to throughput ---
        wait_time_sec = None
        if queue_len == 0:
            wait_time_sec = 0.0
        elif capacity_wait is not None:
            # Capacity model is primary when zone has capacity configured
            if throughput_wait is not None:
                wait_time_sec = (capacity_wait * 0.7) + (throughput_wait * 0.3)
            else:
                wait_time_sec = capacity_wait
        elif throughput_wait is not None:
            if dwell_median is not None:
                wait_time_sec = (throughput_wait * 0.6) + (dwell_median * 0.4)
            else:
                wait_time_sec = throughput_wait
        elif dwell_median is not None:
            wait_time_sec = dwell_median
        else:
            # Fallback: use oldest person's current dwell time
            if self._enter_time:
                oldest_tid = min(self._enter_time, key=self._enter_time.get)
                wait_time_sec = now - self._enter_time[oldest_tid]

        return {
            "queue_len": queue_len,
            "exits_in_window": exits,
            "throughput_exits_per_sec": throughput,
            "wait_time_sec": wait_time_sec,
            "dwell_median_sec": dwell_median,
            "avg_dwell_sec": avg_dwell,
            "number_over_capacity": number_over_capacity,
        }


class QueueManagementService:
    """Service for running queue analysis inside configured zone regions."""

    def __init__(self) -> None:
        self._use_yolo = settings.analytics_use_yolo
        self._cv2 = None
        self._np = None
        self._yolo = None
        self._track = None
        self._person_class_ids: list[int] = [0]

        if not self._use_yolo:
            return

        model_path = Path(settings.yolo_model_path).expanduser()
        if not model_path.is_absolute():
            model_path = (Path.cwd() / model_path).resolve()
        if not model_path.exists():
            logger.warning("Queue model file does not exist: %s", model_path)
            self._use_yolo = False
            return

        try:
            from ultralytics import YOLO  # type: ignore
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            self._yolo = YOLO(str(model_path))
            self._yolo.to(settings.boxmot_device)
            self._track = build_locked_callable(self._yolo.track)
            self._cv2 = cv2
            self._np = np
            self._person_class_ids = resolve_person_class_ids(self._yolo)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to initialize queue detector: %s", exc)
            self._use_yolo = False

    def is_available(self) -> bool:
        """Check if queue detector dependencies are available."""
        return (
            self._use_yolo
            and self._yolo is not None
            and self._track is not None
            and self._cv2 is not None
            and self._np is not None
        )

    async def run_queue_analysis(
        self,
        session: "AsyncSession",
        job_id: int,
        camera_id: int,
        zone_id: int,
        zone_polygon: Any,
        frames: list,
        zone_capacity: int | None = None,
    ) -> dict:
        """Run queue analysis and persist per-frame queue counts."""
        job = await crud.get_queue_analysis_job(session, job_id)
        if not job:
            return {"error": "Job not found"}

        await crud.update_queue_analysis_job(
            session,
            job,
            status=QueueJobStatus.running,
            total_frames=len(frames),
        )

        if not self.is_available():
            error = "Queue detector dependencies are unavailable"
            await crud.update_queue_analysis_job(
                session,
                job,
                status=QueueJobStatus.failed,
                error=error,
            )
            return {"error": error}

        try:
            result = await self._queue_analysis(session, job_id, zone_id, zone_polygon, frames, zone_capacity=zone_capacity)
            await crud.update_queue_analysis_job(
                session,
                job,
                status=QueueJobStatus.completed,
                processed_frames=result["processed_frames"],
                max_queue_count=result["max_queue_count"],
                avg_queue_count=result["avg_queue_count"],
                avg_wait_time_sec=result.get("avg_wait_time_sec"),
                avg_throughput_exits_per_sec=result.get("avg_throughput_exits_per_sec"),
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("Queue analysis failed: %s", exc)
            await crud.update_queue_analysis_job(
                session,
                job,
                status=QueueJobStatus.failed,
                error=str(exc),
            )
            return {"error": str(exc)}

    async def _queue_analysis(
        self,
        session: "AsyncSession",
        job_id: int,
        zone_id: int,
        zone_polygon: Any,
        frames: list,
        zone_capacity: int | None = None,
    ) -> dict:
        """Run per-frame queue occupancy counting over each frame."""
        width, height = self._detect_frame_size(frames)
        polygons = region_points_from_polygon(
            zone_polygon,
            width,
            height,
            use_default_on_invalid=False,
        )
        
        region_points = []
        if polygons:
            # We want a flat list of (x,y) tuples for OpenCV.
            # Depending on mapping it might be [[(x,y), ...]] or just [(x,y), ...]
            first_poly = polygons[0]
            if first_poly and isinstance(first_poly, list) and isinstance(first_poly[0], tuple):
                region_points = first_poly
            elif first_poly and isinstance(first_poly, tuple):
                region_points = polygons
            else:
                 # Flatten safely if highly nested
                 for block in polygons:
                     if isinstance(block, list):
                         region_points.extend(block)

        if len(region_points) < 3:
            raise RuntimeError("Zone polygon is invalid for queue analysis")

        frame_counts: list[dict[str, Any]] = []
        processed_frames = 0
        estimator = WaitTimeEstimator(window_seconds=30.0, min_exits=1, zone_capacity=zone_capacity)

        for frame in frames:
            if not Path(frame.path).exists():
                continue

            image = self._cv2.imread(frame.path)
            if image is None:
                continue

            ids_in_region = self._extract_track_ids_in_region(image, region_points)
            metrics = estimator.update(ids_in_region, now=frame.timestamp.timestamp())
            queue_count = metrics["queue_len"]
            wait_time = metrics["wait_time_sec"]
            throughput = metrics["throughput_exits_per_sec"]

            await crud.create_queue_frame_result(
                session=session,
                job_id=job_id,
                frame_id=frame.id,
                timestamp=frame.timestamp,
                queue_count=queue_count,
                wait_time_sec=wait_time,
                throughput_exits_per_sec=throughput,
            )

            frame_counts.append({
                "timestamp": frame.timestamp.isoformat(), 
                "queue_count": queue_count,
                "wait_time_sec": wait_time,
                "throughput": throughput
            })
            processed_frames += 1

        counts = [entry["queue_count"] for entry in frame_counts]
        waits = [entry["wait_time_sec"] for entry in frame_counts if entry["wait_time_sec"] is not None]
        throughputs = [entry["throughput"] for entry in frame_counts if entry["throughput"] is not None]

        max_count = max(counts) if counts else 0
        avg_count = round(sum(counts) / len(counts), 2) if counts else 0.0
        avg_wait = round(sum(waits) / len(waits), 2) if waits else None
        avg_throughput = round(sum(throughputs) / len(throughputs), 3) if throughputs else None

        return {
            "zone_id": zone_id,
            "max_queue_count": max_count,
            "avg_queue_count": avg_count,
            "avg_wait_time_sec": avg_wait,
            "avg_throughput_exits_per_sec": avg_throughput,
            "processed_frames": processed_frames,
            "frame_counts": frame_counts,
        }

    def _extract_track_ids_in_region(self, image: Any, region_points: list[tuple[int, int]]) -> list[int]:
        try:
            predict_kwargs: dict[str, Any] = {
                "conf": 0.15,  # Decrease threshold to detect more human contours in queue
                "iou": settings.yolo_iou,
                "verbose": settings.yolo_verbose,
            }
            if self._person_class_ids:
                predict_kwargs["classes"] = self._person_class_ids
                
            # Use ByteTrack for object IDs
            predict_kwargs["tracker"] = "bytetrack.yaml"
            results = self._track(image, persist=True, **predict_kwargs)
            boxes = results[0].boxes if results and results[0].boxes is not None else None
            
            if boxes is None or boxes.id is None:
                return []

            xyxy = boxes.xyxy.cpu().numpy().astype(self._np.float32)
            track_ids = boxes.id.cpu().numpy().astype(self._np.int32)
            
            if len(xyxy) == 0:
                return []

            cls_data = getattr(boxes, "cls", None)
            if self._person_class_ids and cls_data is not None:
                cls = cls_data.cpu().numpy().astype(self._np.int32)
                mask = self._np.isin(cls, self._person_class_ids)
                xyxy = xyxy[mask]
                track_ids = track_ids[mask]

            in_region_ids = []
            for i, row in enumerate(xyxy):
                if len(row) < 4:
                    continue
                x1, y1, x2, y2 = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                if x2 <= x1 or y2 <= y1:
                    continue
                
                # Bottom-center point for region crossing
                foot_x = (x1 + x2) / 2.0
                foot_y = y2
                
                if self._point_in_polygon(foot_x, foot_y, region_points):
                    in_region_ids.append(int(track_ids[i]))
                    
            return in_region_ids
        except Exception as exc:  # noqa: BLE001
            logger.warning("Queue analysis tracking failed: %s", exc)
            return []

    def _point_in_polygon(self, x: float, y: float, polygon: list[tuple[int, int]]) -> bool:
        if len(polygon) < 3:
            return False

        inside = False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            intersects = ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
            )
            if intersects:
                inside = not inside
            j = i
        return inside

    def _detect_frame_size(self, frames: list) -> tuple[int, int]:
        for frame in frames:
            if not Path(frame.path).exists():
                continue
            image = self._cv2.imread(frame.path)
            if image is None:
                continue
            height, width = image.shape[:2]
            return width, height
        return 1920, 1080
