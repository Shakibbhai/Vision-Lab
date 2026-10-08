from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_tracker_config_path(boxmot_module: Any, tracker_name: str) -> Path:
    pkg_dir = Path(boxmot_module.__file__).resolve().parent
    candidates = sorted(pkg_dir.rglob(f"{tracker_name}.yaml"))
    if not candidates:
        candidates = sorted(pkg_dir.rglob(f"{tracker_name}.yml"))
    if not candidates:
        # Some BoxMOT versions keep configs under nested names (e.g. "byte_track.yaml").
        candidates = sorted(pkg_dir.rglob(f"*{tracker_name}*.yaml"))
    if not candidates:
        candidates = sorted(pkg_dir.rglob(f"*{tracker_name}*.yml"))
    if not candidates:
        raise FileNotFoundError(f"Could not find {tracker_name} config under {pkg_dir}")
    return candidates[0]


def resolve_strongsort_config_path(boxmot_module: Any) -> Path:
    return resolve_tracker_config_path(boxmot_module, "strongsort")


# Identity counts of Re-ID training sets missing from BoxMOT's table; the classifier shape must match the
# checkpoint even though only the embedding is used (e.g. the official CLIP-ReID MSMT17 model).
_EXTRA_REID_CLASS_COUNTS = {"msmt17": 1041}


def create_reid_encoder(weights: Path, device: str) -> Any:
    """Load a BoxMOT Re-ID model (OSNet, CLIP-ReID, ...); its get_features(xyxy, image) returns embeddings."""
    from boxmot.reid.core import config as reid_config  # type: ignore
    from boxmot.reid.core.auto_backend import ReidAutoBackend  # type: ignore

    for dataset, classes in _EXTRA_REID_CLASS_COUNTS.items():
        reid_config.NR_CLASSES_DICT.setdefault(dataset, classes)
    return ReidAutoBackend(weights=Path(weights), device=device, half=False).model


def create_strongsort_tracker(
    create_tracker_fn: Any,
    config_path: Path,
    reid_weights: str,
    device: str,
    *,
    half: bool = False,
    per_class: bool = False,
    min_hits: int = 1,
    n_init: int = 1,
    min_conf: float = 0.2,
) -> Any:
    import yaml  # type: ignore

    with config_path.open("r", encoding="utf-8") as file:
        yaml_config = yaml.safe_load(file) or {}

    tracker_params: dict[str, Any] = {}
    if isinstance(yaml_config, dict):
        for key, value in yaml_config.items():
            if isinstance(value, dict) and "default" in value:
                tracker_params[key] = value["default"]

    # Sampling every few seconds can produce short tracklets.
    # Lower confirmation thresholds so valid detections are not discarded.
    tracker_params["min_hits"] = max(1, int(min_hits))
    if "n_init" in tracker_params:
        tracker_params["n_init"] = max(1, int(n_init))
    if "min_conf" in tracker_params:
        tracker_params["min_conf"] = max(0.0, float(min_conf))

    return create_tracker_fn(
        "strongsort",
        tracker_config=str(config_path),
        reid_weights=reid_weights,
        device=device,
        half=half,
        per_class=per_class,
        evolve_param_dict=tracker_params,
    )


def create_bytetrack_tracker(
    create_tracker_fn: Any,
    config_path: Path,
    device: str,
    *,
    half: bool = False,
    per_class: bool = False,
    track_thresh: float = 0.25,
    match_thresh: float = 0.8,
    track_buffer: int = 30,
) -> Any:
    import yaml  # type: ignore

    with config_path.open("r", encoding="utf-8") as file:
        yaml_config = yaml.safe_load(file) or {}

    tracker_params: dict[str, Any] = {}
    if isinstance(yaml_config, dict):
        for key, value in yaml_config.items():
            if isinstance(value, dict) and "default" in value:
                tracker_params[key] = value["default"]

    # Keep permissive defaults for sparse frame sampling.
    for key in ("track_thresh", "track_high_thresh", "det_thresh"):
        if key in tracker_params:
            tracker_params[key] = max(0.0, float(track_thresh))
    for key in ("match_thresh", "match_thres"):
        if key in tracker_params:
            tracker_params[key] = max(0.0, float(match_thresh))
    for key in ("track_buffer", "track_buf"):
        if key in tracker_params:
            tracker_params[key] = max(1, int(track_buffer))

    return create_tracker_fn(
        "bytetrack",
        tracker_config=str(config_path),
        device=device,
        half=half,
        per_class=per_class,
        evolve_param_dict=tracker_params,
    )


def ensure_strongsort_outputs(tracker: Any, tracked: Any, np_module: Any) -> Any:
    array = np_module.asarray(tracked) if tracked is not None else np_module.empty((0, 8), dtype=np_module.float32)
    if array.size > 0:
        return array

    inner_tracker = getattr(tracker, "tracker", None)
    inner_tracks = getattr(inner_tracker, "tracks", None)
    if not inner_tracks:
        return np_module.empty((0, 8), dtype=np_module.float32)

    rows: list[list[float]] = []
    for track in inner_tracks:
        try:
            if hasattr(track, "is_deleted") and track.is_deleted():
                continue
            if int(getattr(track, "time_since_update", 9999)) >= 1:
                continue

            x1, y1, x2, y2 = track.to_tlbr()
            track_id = int(getattr(track, "id", -1))
            if track_id < 0:
                continue

            conf = float(getattr(track, "conf", 0.0))
            cls = float(getattr(track, "cls", 0.0))
            det_ind = float(getattr(track, "det_ind", -1))
            rows.append([float(x1), float(y1), float(x2), float(y2), float(track_id), conf, cls, det_ind])
        except Exception:
            continue

    if not rows:
        return np_module.empty((0, 8), dtype=np_module.float32)
    return np_module.asarray(rows, dtype=np_module.float32)


def ensure_bytetrack_outputs(tracked: Any, np_module: Any) -> Any:
    if tracked is None:
        return np_module.empty((0, 8), dtype=np_module.float32)

    array = np_module.asarray(tracked)
    if array.size == 0:
        return np_module.empty((0, 8), dtype=np_module.float32)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    return array.astype(np_module.float32, copy=False)
