from __future__ import annotations

import importlib
import inspect
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.db import crud
from app.db.models import AnalysisJobStatus, PersonDetection, PersonTrack
from app.services.tracking.boxmot_utils import (
    create_strongsort_tracker,
    ensure_strongsort_outputs,
    resolve_strongsort_config_path,
)
from app.services.detection.yolo_threadsafe import build_locked_callable
from app.services.detection.yolo_person_classes import resolve_person_class_ids
from app.services.utils.zone_geometry import region_points_from_polygon

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PersonViTEncoder:
    def __init__(self, checkpoint_path: Path, device: str, trust_checkpoint: bool) -> None:
        import torch

        try:
            import timm  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("timm is required for PersonViT re-identification") from exc

        self._torch = torch
        # Normalize bare integer device IDs like "0", "1" to "cuda:0", "cuda:1"
        if device.isdigit():
            device = f"cuda:{device}"
        if device != "cpu" and not torch.cuda.is_available():
            device = "cpu"
        self._device = torch.device(device)
        self._checkpoint_path = checkpoint_path
        state = self._load_checkpoint(checkpoint_path, trust_checkpoint)

        self._model = timm.create_model(
            "vit_base_patch16_224",
            pretrained=False,
            num_classes=0,
            global_pool="token",
            img_size=(256, 128),
        )
        model_state = self._model.state_dict()
        filtered: dict[str, Any] = {}
        for key, value in state.items():
            normalized_key = str(key).replace("module.", "")
            if normalized_key.startswith("backbone."):
                normalized_key = normalized_key[len("backbone."):]
            if normalized_key.startswith("head") or normalized_key.startswith("classifier"):
                continue
            if normalized_key in model_state and getattr(value, "shape", None) == model_state[normalized_key].shape:
                filtered[normalized_key] = value

        missing, unexpected = self._model.load_state_dict(filtered, strict=False)
        logger.info(
            "PersonViT loaded from %s (%s keys, %s missing, %s unexpected)",
            checkpoint_path,
            len(filtered),
            len(missing),
            len(unexpected),
        )
        self._model.to(self._device).eval()
        self._mean = torch.tensor([0.485, 0.456, 0.406], device=self._device).view(1, 3, 1, 1)
        self._std = torch.tensor([0.229, 0.224, 0.225], device=self._device).view(1, 3, 1, 1)

    def _load_checkpoint(self, path: Path, trust_checkpoint: bool) -> dict[str, Any]:
        torch = self._torch
        checkpoint = self._safe_torch_load(path, trust_checkpoint)
        if isinstance(checkpoint, dict) and "state_dict" in checkpoint and isinstance(checkpoint["state_dict"], dict):
            return checkpoint["state_dict"]
        if isinstance(checkpoint, dict) and "student" in checkpoint and isinstance(checkpoint["student"], dict):
            return checkpoint["student"]
        if isinstance(checkpoint, dict) and "model" in checkpoint and isinstance(checkpoint["model"], dict):
            return checkpoint["model"]
        if isinstance(checkpoint, dict):
            return checkpoint
        raise RuntimeError(f"Unsupported PersonViT checkpoint format: {path}")

    def _safe_torch_load(self, path: Path, trust_checkpoint: bool) -> Any:
        torch = self._torch
        signature = inspect.signature(torch.load)
        supports_weights_only = "weights_only" in signature.parameters
        if not supports_weights_only:
            return torch.load(str(path), map_location=self._device)

        allow_globals: list[Any] = []
        for module_name in ("numpy.core.multiarray", "numpy._core.multiarray"):
            try:
                module = importlib.import_module(module_name)
                scalar = getattr(module, "scalar", None)
                if scalar is not None:
                    allow_globals.append(scalar)
            except Exception:
                continue
        for module_name in ("numpy", "numpy.core", "numpy._core"):
            try:
                module = importlib.import_module(module_name)
                dtype = getattr(module, "dtype", None)
                if dtype is not None:
                    allow_globals.append(dtype)
            except Exception:
                continue

        try:
            with torch.serialization.safe_globals(allow_globals):
                return torch.load(str(path), map_location=self._device, weights_only=True)
        except Exception:
            if trust_checkpoint:
                return torch.load(str(path), map_location=self._device, weights_only=False)
            raise

    def encode_crops_bgr(self, crops_bgr: list[Any]) -> Any:
        torch = self._torch
        if not crops_bgr:
            return None

        batch = []
        for crop in crops_bgr:
            if crop is None or getattr(crop, "size", 0) == 0:
                continue
            image = self._cv2.cvtColor(crop, self._cv2.COLOR_BGR2RGB)
            image = self._cv2.resize(image, (128, 256), interpolation=self._cv2.INTER_LINEAR)
            batch.append(image)

        if not batch:
            return None

        import numpy as np

        # Ship uint8 to the device once and normalize there; per-crop float conversion on CPU dominated encode time
        stacked = torch.from_numpy(np.stack(batch)).to(self._device, non_blocking=True)
        stacked = stacked.permute(0, 3, 1, 2).float().div_(255.0)
        stacked = (stacked - self._mean) / self._std

        use_fp16 = self._device.type == "cuda"
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16, enabled=use_fp16):
            embeddings = self._model(stacked)
        embeddings = torch.nn.functional.normalize(embeddings.float(), dim=1)
        return embeddings.detach().cpu().numpy().astype("float32")

    @property
    def _cv2(self):
        import cv2  # type: ignore

        return cv2


class PersonTrackingService:
    def __init__(self) -> None:
        self._available = False
        self._init_error: str | None = None
        self._yolo = None
        self._predict = None
        self._cv2 = None
        self._np = None
        self._create_tracker = None
        self._strongsort_config_path: Path | None = None
        self._person_class_ids: list[int] = [0]
        self._reid_weights = self._resolve_path(settings.boxmot_reid_weights)
        self._personvit_encoder: PersonViTEncoder | None = None
        self._personvit_threshold = max(0.0, min(1.0, float(settings.personvit_similarity_threshold)))

        if not settings.analytics_use_yolo:
            self._init_error = "YOLO analytics is disabled"
            return

        try:
            from ultralytics import YOLO  # type: ignore
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            from boxmot import create_tracker  # type: ignore
            import boxmot  # type: ignore

            self._yolo = YOLO(settings.yolo_model_path)
            self._yolo.to(settings.boxmot_device)
            self._predict = build_locked_callable(self._yolo.predict)
            self._cv2 = cv2
            self._np = np
            self._create_tracker = create_tracker
            self._strongsort_config_path = resolve_strongsort_config_path(boxmot)
            self._person_class_ids = resolve_person_class_ids(self._yolo)
            self._init_personvit_if_enabled()
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._init_error = str(exc)
            logger.warning("Failed to initialize person tracking service: %s", exc)

    def _resolve_path(self, raw: str) -> str:
        candidate = Path(str(raw or "")).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        return str(candidate)

    def _init_personvit_if_enabled(self) -> None:
        if not settings.personvit_use_reid:
            return
        configured_path = Path(self._resolve_path(settings.personvit_reid_weights))
        candidates = [
            configured_path,
            Path.cwd() / "checkpoint0260.pth",
            Path.cwd() / "vit_base_checkpoint0260.pth",
            Path(r"e:\OneDrive\source code java all\OneDrive\Desktop\8th semister project\Percepta-ReID\vit_base_checkpoint0260.pth"),
            Path(r"e:\OneDrive\source code java all\OneDrive\Desktop\8th semister project\Percepta-ReID\checkpoint0260.pth"),
            Path("/workspace/Percepta-ReID/vit_base_checkpoint0260.pth"),
            Path("/workspace/Percepta-ReID/checkpoint0260.pth"),
        ]
        checkpoint = None
        for cand in candidates:
            if cand and cand.exists():
                checkpoint = cand
                break
        if checkpoint is None:
            logger.warning("PersonViT checkpoint not found in candidate paths (reid fallback: StrongSORT ids)")
            return
        try:
            self._personvit_encoder = PersonViTEncoder(
                checkpoint_path=checkpoint,
                device=settings.boxmot_device,
                trust_checkpoint=settings.personvit_trust_checkpoint,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("PersonViT initialization failed: %s", exc)
            self._personvit_encoder = None

    def is_available(self) -> bool:
        return (
            self._available
            and self._yolo is not None
            and self._predict is not None
            and self._cv2 is not None
            and self._np is not None
            and self._create_tracker is not None
            and self._strongsort_config_path is not None
        )

    def error_message(self) -> str:
        return self._init_error or "Person tracking/ReID is unavailable"

    async def run_analysis(
        self,
        session: "AsyncSession",
        job_id: int,
        camera_id: int,
        frames: list[Any],
        zones: list[Any],
    ) -> dict[str, Any]:
        job = await crud.get_analysis_job(session, job_id)
        if job is None:
            return {"error": "Analysis job not found"}

        total_frames = len(frames)
        await crud.update_analysis_job(
            session,
            job,
            status=AnalysisJobStatus.running,
            total_frames=total_frames,
            processed_frames=0,
            unique_persons=0,
            error=None,
        )

        if not self.is_available():
            error_text = self.error_message()
            await crud.update_analysis_job(
                session,
                job,
                status=AnalysisJobStatus.failed,
                total_frames=total_frames,
                processed_frames=0,
                unique_persons=0,
                error=error_text,
            )
            return {"error": error_text}

        stride = max(1, int(settings.analytics_frame_stride))
        max_frames = max(1, int(settings.analytics_max_frames))
        selected_frames = [frame for index, frame in enumerate(frames) if index % stride == 0][:max_frames]
        if not selected_frames:
            await crud.update_analysis_job(
                session,
                job,
                status=AnalysisJobStatus.failed,
                total_frames=total_frames,
                processed_frames=0,
                unique_persons=0,
                error="No frames available after stride/max-frame filtering",
            )
            return {"error": "No frames available after filtering"}

        try:
            tracker = create_strongsort_tracker(
                create_tracker_fn=self._create_tracker,
                config_path=self._strongsort_config_path,
                reid_weights=self._reid_weights,
                device=settings.boxmot_device,
                half=False,
                per_class=False,
                min_hits=max(1, int(settings.strongsort_min_hits)),
                n_init=max(1, int(settings.strongsort_n_init)),
                min_conf=max(0.0, float(settings.strongsort_min_conf)),
            )
        except Exception as exc:  # noqa: BLE001
            error_text = f"StrongSORT initialization failed: {exc}"
            logger.exception(error_text)
            await crud.update_analysis_job(
                session,
                job,
                status=AnalysisJobStatus.failed,
                total_frames=total_frames,
                processed_frames=0,
                unique_persons=0,
                error=error_text,
            )
            return {"error": error_text}

        processed_frames = 0
        track_rows: dict[int, PersonTrack] = {}
        confidence_sums: defaultdict[int, float] = defaultdict(float)
        appearance_counts: defaultdict[int, int] = defaultdict(int)
        zone_counts: defaultdict[int, dict[int | None, int]] = defaultdict(dict)
        global_embeddings: dict[int, Any] = {}
        local_to_global: dict[int, int] = {}
        next_global_id = 1
        polygon_cache: dict[tuple[int, int], dict[int, list[list[tuple[int, int]]]]] = {}

        try:
            for frame in selected_frames:
                frame_path = Path(str(getattr(frame, "path", "")))
                if not frame_path.exists():
                    continue

                image = self._cv2.imread(str(frame_path))
                if image is None:
                    continue

                frame_h, frame_w = image.shape[:2]
                polygons = polygon_cache.get((frame_w, frame_h))
                if polygons is None:
                    polygons = {
                        int(zone.id): region_points_from_polygon(
                            zone.polygon,
                            frame_w,
                            frame_h,
                            use_default_on_invalid=False,
                        )
                        for zone in zones
                    }
                    polygon_cache[(frame_w, frame_h)] = polygons

                detections = self._detect_people(image)
                tracked_raw = tracker.update(detections, image)
                tracked = ensure_strongsort_outputs(tracker, tracked_raw, self._np)
                if tracked is None or tracked.size == 0:
                    processed_frames += 1
                    continue

                frame_detections = self._parse_tracked_rows(tracked)
                crops, crop_indices = self._collect_crops(image, frame_detections)
                embeddings = self._encode_embeddings(crops)

                timestamp = getattr(frame, "timestamp", None) or datetime.utcnow()

                for row_index, det in enumerate(frame_detections):
                    local_track_id = int(det["track_id"])
                    embedding = None
                    if embeddings is not None and row_index in crop_indices:
                        embedding = embeddings[crop_indices[row_index]]

                    if local_track_id not in local_to_global:
                        global_track_id = self._assign_global_track_id(
                            local_track_id,
                            embedding,
                            global_embeddings,
                            next_global_id,
                        )
                        local_to_global[local_track_id] = global_track_id
                        if global_track_id == next_global_id:
                            next_global_id += 1
                    global_track_id = local_to_global[local_track_id]

                    if embedding is not None:
                        current = global_embeddings.get(global_track_id)
                        if current is None:
                            global_embeddings[global_track_id] = embedding
                        else:
                            mixed = 0.7 * current + 0.3 * embedding
                            norm = float(self._np.linalg.norm(mixed))
                            if norm > 1e-8:
                                mixed = mixed / norm
                            global_embeddings[global_track_id] = mixed.astype(self._np.float32, copy=False)

                    zone_id = _resolve_zone_for_bbox(
                        det["x1"],
                        det["y1"],
                        det["x2"],
                        det["y2"],
                        polygons,
                        zones,
                    )
                    confidence = float(det["confidence"])
                    confidence_sums[global_track_id] += confidence
                    appearance_counts[global_track_id] += 1
                    zone_counts[global_track_id][zone_id] = zone_counts[global_track_id].get(zone_id, 0) + 1

                    track_row = track_rows.get(global_track_id)
                    if track_row is None:
                        track_row = PersonTrack(
                            job_id=job_id,
                            track_id=global_track_id,
                            camera_id=camera_id,
                            zone_id=zone_id,
                            first_seen=timestamp,
                            last_seen=timestamp,
                            appearances=appearance_counts[global_track_id],
                            avg_confidence=confidence,
                            dwell_seconds=0.0,
                        )
                        session.add(track_row)
                        track_rows[global_track_id] = track_row
                    else:
                        track_row.last_seen = timestamp
                        track_row.appearances = appearance_counts[global_track_id]
                        track_row.avg_confidence = confidence_sums[global_track_id] / max(
                            1, appearance_counts[global_track_id]
                        )
                        track_row.zone_id = _dominant_zone(zone_counts[global_track_id])
                        track_row.dwell_seconds = max(
                            0.0,
                            float((track_row.last_seen - track_row.first_seen).total_seconds()),
                        )

                    session.add(
                        PersonDetection(
                            track=track_row,
                            frame_id=int(getattr(frame, "id", 0)),
                            bbox_x1=float(det["x1"]),
                            bbox_y1=float(det["y1"]),
                            bbox_x2=float(det["x2"]),
                            bbox_y2=float(det["y2"]),
                            confidence=confidence,
                            in_zone_id=zone_id,
                            timestamp=timestamp,
                        )
                    )

                processed_frames += 1
                if processed_frames % 20 == 0:
                    await session.flush()
                    await crud.update_analysis_job(
                        session,
                        job,
                        status=AnalysisJobStatus.running,
                        total_frames=total_frames,
                        processed_frames=processed_frames,
                        unique_persons=len(track_rows),
                    )
                    job = await crud.get_analysis_job(session, job_id) or job

            for track_id, row in track_rows.items():
                row.zone_id = _dominant_zone(zone_counts[track_id])
                row.avg_confidence = confidence_sums[track_id] / max(1, appearance_counts[track_id])
                row.dwell_seconds = max(0.0, float((row.last_seen - row.first_seen).total_seconds()))

            await session.flush()
            await crud.update_analysis_job(
                session,
                job,
                status=AnalysisJobStatus.completed,
                total_frames=total_frames,
                processed_frames=processed_frames,
                unique_persons=len(track_rows),
                error=None,
            )
            return {
                "job_id": job_id,
                "processed_frames": processed_frames,
                "unique_persons": len(track_rows),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Person tracking analysis failed for job %s: %s", job_id, exc)
            await session.rollback()
            job = await crud.get_analysis_job(session, job_id)
            if job is not None:
                await crud.update_analysis_job(
                    session,
                    job,
                    status=AnalysisJobStatus.failed,
                    total_frames=total_frames,
                    processed_frames=processed_frames,
                    unique_persons=len(track_rows),
                    error=str(exc),
                )
            return {"error": str(exc)}

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
            logger.warning("Person tracking detection failed: %s", exc)
            return self._np.empty((0, 6), dtype=self._np.float32)

    def _parse_tracked_rows(self, tracked: Any) -> list[dict[str, float]]:
        output: list[dict[str, float]] = []
        for row in tracked:
            if len(row) < 5:
                continue
            try:
                x1 = float(row[0])
                y1 = float(row[1])
                x2 = float(row[2])
                y2 = float(row[3])
                track_id = int(row[4])
            except (TypeError, ValueError):
                continue
            if track_id < 0 or x2 <= x1 or y2 <= y1:
                continue
            confidence = float(row[5]) if len(row) > 5 else 0.0
            output.append(
                {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "track_id": float(track_id),
                    "confidence": confidence,
                }
            )
        return output

    def _collect_crops(self, image: Any, detections: list[dict[str, float]]) -> tuple[list[Any], dict[int, int]]:
        crops: list[Any] = []
        index_map: dict[int, int] = {}
        frame_h, frame_w = image.shape[:2]
        for row_index, det in enumerate(detections):
            x1 = max(0, min(frame_w - 1, int(round(det["x1"]))))
            y1 = max(0, min(frame_h - 1, int(round(det["y1"]))))
            x2 = max(0, min(frame_w, int(round(det["x2"]))))
            y2 = max(0, min(frame_h, int(round(det["y2"]))))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = image[y1:y2, x1:x2]
            if crop is None or crop.size == 0:
                continue
            index_map[row_index] = len(crops)
            crops.append(crop)
        return crops, index_map

    def _encode_embeddings(self, crops: list[Any]) -> Any:
        if self._personvit_encoder is None or not crops:
            return None
        try:
            return self._personvit_encoder.encode_crops_bgr(crops)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PersonViT embedding extraction failed: %s", exc)
            return None

    def _assign_global_track_id(
        self,
        local_track_id: int,
        embedding: Any,
        global_embeddings: dict[int, Any],
        next_global_id: int,
    ) -> int:
        """Assign or re-identify global track ID using multi-template exemplar matching."""
        if embedding is None or not global_embeddings:
            return local_track_id if self._personvit_encoder is None else next_global_id

        best_id = None
        best_score = -1.0
        emb_norm = float(self._np.linalg.norm(embedding))
        if emb_norm <= 1e-8:
            return next_global_id
        normalized = embedding / emb_norm

        for global_id, templates in global_embeddings.items():
            template_list = templates if isinstance(templates, list) else [templates]
            for candidate in template_list:
                candidate_norm = float(self._np.linalg.norm(candidate))
                if candidate_norm <= 1e-8:
                    continue
                score = float(self._np.dot(normalized, candidate / candidate_norm))
                if score > best_score:
                    best_score = score
                    best_id = global_id

        threshold = getattr(self, "_personvit_threshold", 0.44)
        if best_id is not None and best_score >= threshold:
            return int(best_id)
        return int(next_global_id)


def _dominant_zone(counts: dict[int | None, int]) -> int | None:
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _resolve_zone_for_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    zone_points: dict[int, list[list[tuple[int, int]]]],
    zones: list[Any],
) -> int | None:
    foot_x = (x1 + x2) / 2.0
    foot_y = y2
    return _resolve_zone_for_point(foot_x, foot_y, zone_points, zones)


def _resolve_zone_for_point(
    x: float,
    y: float,
    zone_points: dict[int, list[list[tuple[int, int]]]],
    zones: list[Any],
) -> int | None:
    for zone in zones:
        zone_id = int(getattr(zone, "id", 0))
        polygons = zone_points.get(zone_id) or []
        for polygon in polygons:
            if len(polygon) < 3:
                continue
            if _point_in_polygon(x, y, polygon):
                return zone_id
    return None


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
