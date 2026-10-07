from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import fetcher_enabled
from app.db import crud


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)


def _to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def create_zone(session: AsyncSession, camera_id: int, name: str, polygon: dict, capacity: int | None = None, expected_wait_time_sec: int | None = None):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return await crud.create_zone(session, camera_id, name, polygon, capacity=capacity, expected_wait_time_sec=expected_wait_time_sec)


async def list_zones(session: AsyncSession, camera_id: int | None):
    return await crud.list_zones(session, camera_id=camera_id)


async def delete_zone(session: AsyncSession, zone_id: int):
    zone = await crud.get_zone(session, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    await crud.delete_zone(session, zone)



async def zone_preview(
    session: AsyncSession,
    camera_id: int,
):
    if not fetcher_enabled():
        raise HTTPException(status_code=503, detail="Zone preview available only on fetcher")

    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    latest = await crud.get_latest_frame(session, camera_id)
    if not latest:
        raise HTTPException(
            status_code=404,
            detail="No captured frames found for this camera yet. Start capture and wait a few seconds.",
        )

    if Path(latest.path).exists():
        return FileResponse(latest.path, media_type="image/jpeg")

    raise HTTPException(
        status_code=404,
        detail="Latest captured frame file is missing on disk. Re-capture this camera.",
    )


async def get_footfall(
    session: AsyncSession,
    camera_id: int,
    start: str | None,
    end: str | None,
    zone_id: int | None,
):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    source_url = (camera.rtsp_url or "").strip().lower()
    source_type = "video" if source_url.startswith("file://") else "rtsp"

    start_dt = _to_utc_naive(_parse_datetime(start))
    end_dt = _to_utc_naive(_parse_datetime(end))

    payload = await crud.query_footfall(
        session=session,
        camera_id=camera_id,
        start=start_dt,
        end=end_dt,
        zone_id=zone_id,
        source_type=source_type,
    )

    return {
        "camera_id": camera_id,
        "zone_id": zone_id,
        "total": int(payload.get("total", 0)),
        "total_entries": int(payload.get("total_entries", 0)),
        "total_exits": int(payload.get("total_exits", 0)),
        "net_flow": int(payload.get("net_flow", 0)),
        "series": list(payload.get("series", [])),
        "zone_totals": list(payload.get("zone_totals", [])),
        "video_available_seconds": int(payload.get("video_available_seconds", 0)),
        "video_available_hours": float(payload.get("video_available_hours", 0.0)),
        "peak_traffic_hour": payload.get("peak_traffic_hour"),
        "peak_person_count_hour": payload.get("peak_person_count_hour"),
        "source_type": payload.get("source_type", source_type),
        "most_crowded_quarter": payload.get("most_crowded_quarter"),
    }


async def get_zone_analysis(
    session: AsyncSession,
    camera_id: int,
    start: str | None,
    end: str | None,
    zone_id: int | None,
):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    start_dt = _to_utc_naive(_parse_datetime(start))
    end_dt = _to_utc_naive(_parse_datetime(end))
    payload = await crud.query_zone_analysis(
        session=session,
        camera_id=camera_id,
        start=start_dt,
        end=end_dt,
        zone_id=zone_id,
    )

    return {
        "camera_id": camera_id,
        "zone_id": zone_id,
        "start_time": payload.get("start_time"),
        "end_time": payload.get("end_time"),
        "frame_interval_seconds": float(payload.get("frame_interval_seconds", 1.0)),
        "congestion_by_zone": list(payload.get("congestion_by_zone", [])),
        "tracked_paths": list(payload.get("tracked_paths", [])),
        "heatmaps": list(payload.get("heatmaps", [])),
        "service_staff_presence": list(payload.get("service_staff_presence", [])),
        "summary": dict(payload.get("summary", {})),
    }
