from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db import crud


RUNTIME_KEYS: dict[str, type] = {
    "stream_tick_seconds": float,
    "stream_heartbeat_timeout_seconds": float,
    "ffmpeg_segment_time_seconds": int,
    "segment_scan_seconds": float,
    "analytics_poll_seconds": float,
    "frame_interval_seconds": float,
    "analytics_processing_fps": float,
    "retention_days": int,
    "max_streams": int,
    "analytics_use_yolo": bool,
    "yolo_model_path": str,
    "yolo_confidence": float,
    "yolo_iou": float,
    "yolo_tracker": str,
    "yolo_verbose": bool,
    "boxmot_reid_weights": str,
    "boxmot_device": str,
    "strongsort_min_hits": int,
    "strongsort_n_init": int,
    "strongsort_min_conf": float,
    "personvit_use_reid": bool,
    "personvit_reid_weights": str,
    "personvit_similarity_threshold": float,
    "personvit_trust_checkpoint": bool,
    "analytics_frame_stride": int,
    "analytics_max_frames": int,
    "dashboard_overlay_modes": dict,
}


def _normalize_value(value: Any, expected_type: type) -> Any:
    if expected_type is bool:
        return bool(value)
    if expected_type is int:
        return int(value)
    if expected_type is float:
        return float(value)
    if expected_type is dict:
        if not isinstance(value, dict):
            raise ValueError("Expected an object value")
        return value
    return value


def get_default_config() -> dict[str, Any]:
    return {key: getattr(settings, key) for key in RUNTIME_KEYS}


async def get_runtime_config(session) -> dict[str, Any]:
    config = get_default_config()
    overrides = await crud.list_runtime_config(session)
    for item in overrides:
        if item.key not in RUNTIME_KEYS:
            continue
        config[item.key] = item.value.get("value")
    return config


async def set_runtime_config(session, key: str, value: Any) -> dict[str, Any]:
    if key not in RUNTIME_KEYS:
        raise ValueError("Unsupported config key")
    normalized = _normalize_value(value, RUNTIME_KEYS[key])
    await crud.upsert_runtime_config(session, key, {"value": normalized})
    return await get_runtime_config(session)


async def backup_config(session) -> str:
    data = await get_runtime_config(session)
    path = Path(settings.config_backup_path)
    path.write_text(json.dumps({"updated_at": datetime.utcnow().isoformat(), "config": data}, indent=2))
    return str(path)


async def restore_config(session, content: dict) -> dict[str, Any]:
    config = content.get("config", {})
    for key, value in config.items():
        if key in RUNTIME_KEYS:
            await crud.upsert_runtime_config(session, key, {"value": value})
    return await get_runtime_config(session)
