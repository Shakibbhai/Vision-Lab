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

_STATE_TTL_SECONDS = 180.0
# A new track keeps a provisional ID until it has this many embeddings, then is compared
# (as an average, single crops are too noisy) against identities not visible on this camera.
_MIN_EMBEDDINGS_FOR_MATCH = 5
_REMATCH_MAX_AGE = 40
_EXEMPLAR_WINDOW = 8
_MAX_EXEMPLARS = 10
_EXEMPLAR_DIVERSITY = 0.9
# Keep drawing a track's last box this long after the detector misses it
_COAST_SECONDS = 1.5
_COAST_MAX_IOU = 0.5
_GALLERY_TTL_SECONDS = 1800.0
# Single-model fallbacks when only one encoder loads (fused threshold comes from settings)
_VIT_ONLY_THRESHOLD = 0.70
_OSNET_ONLY_THRESHOLD = 0.84


@dataclass(slots=True)
class _Identity:
    """Appearance memory for one global person ID, shared across cameras."""

    embedding_sum: Any
    count: int
    exemplars: list[Any] = field(default_factory=list)
    window: list[Any] = field(default_factory=list)
    last_seen_monotonic: float = 0.0


@dataclass(slots=True)
class _TrackMemory:
    embedding_sum: Any = None
    count: int = 0
    age: int = 0
    settled: bool = False
    last_row: dict[str, Any] | None = None
    last_seen_monotonic: float = 0.0


@dataclass(slots=True)
class _CameraTrackState:
    tracker: Any
    frame_width: int
    frame_height: int
    tracked_rows: list[dict[str, Any]] = field(default_factory=list)
    last_frame_token: str = ""
    updated_at_monotonic: float = 0.0
    local_to_global: dict[int, int] = field(default_factory=dict)
    tracks: dict[int, _TrackMemory] = field(default_factory=dict)


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
        self._osnet_encoder: Any = None
        self._reid_threshold: float = 0.75

        self._camera_states: dict[tuple[int, str], _CameraTrackState] = {}
        self._camera_locks: dict[tuple[int, str], threading.Lock] = {}
        self._state_guard = threading.Lock()

        # Identity gallery shared by every camera/zone so a person keeps one ID across views
        self._gallery: dict[int, _Identity] = {}
        self._gallery_lock = threading.Lock()
        self._next_global_id = 1

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

            self._init_personvit_encoder()
            self._init_osnet_encoder()
            if self._personvit_encoder is not None and self._osnet_encoder is not None:
                self._reid_threshold = float(settings.reid_match_threshold)
            elif self._personvit_encoder is not None:
                self._reid_threshold = _VIT_ONLY_THRESHOLD
            else:
                self._reid_threshold = _OSNET_ONLY_THRESHOLD
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

    def _init_osnet_encoder(self) -> None:
        """Load the supervised OSNet Re-ID model; fused with PersonViT it separates look-alikes far better."""
        try:
            from boxmot.reid.core.auto_backend import ReidAutoBackend  # type: ignore

            weights = Path(settings.boxmot_reid_weights)
            if not weights.exists():
                logger.info("PersonReidOverlayService: OSNet weights not found at %s", weights)
                return
            self._osnet_encoder = ReidAutoBackend(weights=weights, device=settings.boxmot_device, half=False).model
            logger.info("PersonReidOverlayService: OSNet encoder active from %s", weights)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PersonReidOverlayService: OSNet encoder init notice: %s", exc)
            self._osnet_encoder = None

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
        with self._gallery_lock:
            expired = [
                global_id
                for global_id, identity in self._gallery.items()
                if now - identity.last_seen_monotonic > _GALLERY_TTL_SECONDS
            ]
            for global_id in expired:
                self._gallery.pop(global_id, None)

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
        now = time.monotonic()
        detections = self._detect_people(image)
        tracked_raw = state.tracker.update(detections, image)
        tracked = ensure_bytetrack_outputs(tracked_raw, self._np)

        visible: list[tuple[int, dict[str, Any]]] = []
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
            visible.append((track_id, {"x1": x1, "y1": y1, "x2": x2, "y2": y2}))

        embeddings = self._embed(image, [box for _, box in visible])

        with self._gallery_lock:
            for idx, (local_id, _box) in enumerate(visible):
                memory = state.tracks.setdefault(local_id, _TrackMemory())
                memory.age += 1
                memory.last_seen_monotonic = now
                embedding = embeddings[idx] if embeddings is not None else None
                if embedding is not None:
                    memory.embedding_sum = embedding.copy() if memory.embedding_sum is None else memory.embedding_sum + embedding
                    memory.count += 1
                if local_id not in state.local_to_global:
                    state.local_to_global[local_id] = self._new_identity(now)

            self._settle_young_tracks(state, [local_id for local_id, _ in visible])

            rows: list[dict[str, Any]] = []
            for idx, (local_id, box) in enumerate(visible):
                global_id = state.local_to_global[local_id]
                embedding = embeddings[idx] if embeddings is not None else None
                if embedding is not None:
                    self._remember(global_id, embedding, now)
                row = {**box, "track_id": global_id, "local_id": local_id}
                state.tracks[local_id].last_row = row
                rows.append(row)

        rows.extend(self._coasting_rows(state, rows, now))
        return rows

    def _embed(self, image: Any, boxes: list[dict[str, Any]]) -> Any:
        """Fused, L2-normalized PersonViT+OSNet embeddings (dot product = mean of the two cosines)."""
        if not boxes or (self._personvit_encoder is None and self._osnet_encoder is None):
            return None
        np = self._np
        parts = []
        try:
            if self._personvit_encoder is not None:
                crops = [image[b["y1"]:b["y2"], b["x1"]:b["x2"]] for b in boxes]
                parts.append(_l2_normalize(np, np.asarray(self._personvit_encoder.encode_crops_bgr(crops))))
            if self._osnet_encoder is not None:
                xyxy = np.array([[b["x1"], b["y1"], b["x2"], b["y2"]] for b in boxes], dtype=np.float32)
                parts.append(_l2_normalize(np, np.asarray(self._osnet_encoder.get_features(xyxy, image))))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Re-ID embedding failed: %s", exc)
            return None
        if any(len(part) != len(boxes) for part in parts):
            return None
        return _l2_normalize(np, np.concatenate(parts, axis=1)).astype(np.float32)

    def _new_identity(self, now: float) -> int:
        global_id = self._next_global_id
        self._next_global_id += 1
        self._gallery[global_id] = _Identity(embedding_sum=None, count=0, last_seen_monotonic=now)
        return global_id

    def _settle_young_tracks(self, state: _CameraTrackState, visible_local_ids: list[int]) -> None:
        """Re-associate young tracks with a known identity once their averaged embedding is reliable.

        A global ID already shown on another track of this camera is never a candidate, so two people
        in the same frame can never share an ID.
        """
        np = self._np
        active_ids = {state.local_to_global[local_id] for local_id in visible_local_ids}
        proposals: list[tuple[float, int, int]] = []
        for local_id in visible_local_ids:
            memory = state.tracks[local_id]
            if memory.settled:
                continue
            if memory.age > _REMATCH_MAX_AGE:
                memory.settled = True
                continue
            if memory.count < _MIN_EMBEDDINGS_FOR_MATCH:
                continue
            own_id = state.local_to_global[local_id]
            query = _l2_normalize(np, memory.embedding_sum)
            for global_id, identity in self._gallery.items():
                if global_id == own_id or global_id in active_ids or identity.count == 0:
                    continue
                score = self._identity_similarity(query, identity)
                if score >= self._reid_threshold:
                    proposals.append((score, local_id, global_id))

        claimed_tracks: set[int] = set()
        claimed_ids: set[int] = set()
        for score, local_id, global_id in sorted(proposals, reverse=True):
            if local_id in claimed_tracks or global_id in claimed_ids:
                continue
            claimed_tracks.add(local_id)
            claimed_ids.add(global_id)
            provisional_id = state.local_to_global[local_id]
            self._merge_identity(provisional_id, into=global_id)
            state.local_to_global[local_id] = global_id
            state.tracks[local_id].settled = True
            logger.debug("Re-ID: track %s re-identified as %s (score %.3f)", local_id, global_id, score)

    def _identity_similarity(self, query: Any, identity: _Identity) -> float:
        np = self._np
        best = float(np.dot(query, _l2_normalize(np, identity.embedding_sum)))
        for exemplar in identity.exemplars:
            best = max(best, float(np.dot(query, exemplar)))
        return best

    def _merge_identity(self, source_id: int, into: int) -> None:
        source = self._gallery.pop(source_id, None)
        target = self._gallery.get(into)
        if source is None or target is None or source.count == 0:
            return
        target.embedding_sum = source.embedding_sum if target.embedding_sum is None else target.embedding_sum + source.embedding_sum
        target.count += source.count
        for exemplar in source.exemplars:
            self._add_exemplar(target, exemplar)
        target.last_seen_monotonic = max(target.last_seen_monotonic, source.last_seen_monotonic)

    def _remember(self, global_id: int, embedding: Any, now: float) -> None:
        identity = self._gallery.get(global_id)
        if identity is None:
            return
        identity.embedding_sum = embedding.copy() if identity.embedding_sum is None else identity.embedding_sum + embedding
        identity.count += 1
        identity.last_seen_monotonic = now
        # Exemplars are short-window averages; a varied set lets a person match from a new angle or pose
        identity.window.append(embedding)
        if len(identity.window) >= _EXEMPLAR_WINDOW:
            self._add_exemplar(identity, _l2_normalize(self._np, self._np.mean(identity.window, axis=0)))
            identity.window.clear()

    def _add_exemplar(self, identity: _Identity, exemplar: Any) -> None:
        if len(identity.exemplars) >= _MAX_EXEMPLARS:
            return
        if all(float(self._np.dot(exemplar, existing)) < _EXEMPLAR_DIVERSITY for existing in identity.exemplars):
            identity.exemplars.append(exemplar)

    def _coasting_rows(
        self,
        state: _CameraTrackState,
        visible_rows: list[dict[str, Any]],
        now: float,
    ) -> list[dict[str, Any]]:
        """Hold the last box of briefly-missed tracks so the rectangle does not flicker."""
        shown_ids = {row["track_id"] for row in visible_rows}
        shown_locals = {row["local_id"] for row in visible_rows}
        coasting: list[dict[str, Any]] = []
        for local_id, memory in list(state.tracks.items()):
            if local_id in shown_locals or memory.last_row is None:
                continue
            if now - memory.last_seen_monotonic > _COAST_SECONDS:
                if now - memory.last_seen_monotonic > _STATE_TTL_SECONDS:
                    state.tracks.pop(local_id, None)
                    state.local_to_global.pop(local_id, None)
                continue
            global_id = state.local_to_global.get(local_id)
            if global_id is None or global_id in shown_ids:
                continue
            row = {**memory.last_row, "track_id": global_id}
            if any(_box_iou(row, other) > _COAST_MAX_IOU for other in visible_rows):
                continue
            coasting.append(row)
            shown_ids.add(global_id)
        return coasting

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


def _l2_normalize(np: Any, vectors: Any) -> Any:
    norm = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norm, 1e-8)


def _box_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ix = max(0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    iy = max(0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    inter = ix * iy
    union = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"]) + (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) - inter
    return inter / union if union > 0 else 0.0


def _track_color(track_id: int) -> tuple[int, int, int]:
    if track_id < 0:
        return (120, 120, 120)
    return (
        int((37 * track_id + 17) % 255),
        int((17 * track_id + 113) % 255),
        int((29 * track_id + 53) % 255),
    )
