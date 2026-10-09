"""Aggregated numbers for the overview dashboard and analytics pages."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AnalysisJob,
    Camera,
    FaceReference,
    FootfallEvent,
    Frame,
    PersonDetection,
    PersonTrack,
    QueueAnalysisJob,
    QueueJobStatus,
    Stream,
    StreamStatus,
    Zone,
)
from app.services.tracking.reid_video_export import saved_exports

_ACTIVITY_HOURS = 24
_TOP_IDENTITIES = 10


async def get_overview(session: AsyncSession) -> dict[str, Any]:
    cameras = (await session.execute(
        select(Camera).where(Camera.is_deleted.is_(False)).order_by(Camera.id)
    )).scalars().all()
    camera_ids = [c.id for c in cameras]

    live = set((await session.execute(
        select(Stream.camera_id).where(Stream.status == StreamStatus.running)
    )).scalars().all())
    zones = await _count_by_camera(session, Zone.camera_id, Zone.id)
    frames = await _count_by_camera(session, Frame.camera_id, Frame.id)
    footfall = await _footfall_by_camera(session)
    offline_unique = dict((await session.execute(
        select(AnalysisJob.camera_id, func.coalesce(func.sum(AnalysisJob.unique_persons), 0))
        .group_by(AnalysisJob.camera_id)
    )).all())
    offline_detections = dict((await session.execute(
        select(PersonTrack.camera_id, func.count(PersonDetection.id))
        .join(PersonDetection, PersonDetection.track_id == PersonTrack.id)
        .group_by(PersonTrack.camera_id)
    )).all())
    faces = (await session.execute(select(func.count(FaceReference.id)))).scalar_one()
    reid = _reid_by_camera(camera_ids)

    camera_rows = []
    for cam in cameras:
        saved = reid.get(cam.id)
        entries, exits = footfall.get(cam.id, (0, 0))
        camera_rows.append({
            "id": cam.id,
            "name": cam.name,
            "source_type": _source_type(cam.rtsp_url),
            "live": cam.id in live,
            "zones": zones.get(cam.id, 0),
            "frames": frames.get(cam.id, 0),
            "entries": entries,
            "exits": exits,
            "unique_persons": (saved or {}).get("unique_persons", 0) or int(offline_unique.get(cam.id, 0)),
            "detections": (saved or {}).get("total_detections", 0) + int(offline_detections.get(cam.id, 0)),
            "reid": saved,
        })

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "totals": {
            "cameras": len(cameras),
            "live_cameras": sum(1 for c in camera_rows if c["live"]),
            "zones": sum(c["zones"] for c in camera_rows),
            "enrolled_faces": int(faces),
            "frames_captured": sum(c["frames"] for c in camera_rows),
            "reid_videos": sum(1 for c in camera_rows if c["reid"]),
            "unique_persons": sum(c["unique_persons"] for c in camera_rows),
            "detections": sum(c["detections"] for c in camera_rows),
            "entries": sum(c["entries"] for c in camera_rows),
            "exits": sum(c["exits"] for c in camera_rows),
        },
        "cameras": camera_rows,
        "activity": await _hourly_activity(session),
        "queues": await _latest_queues(session),
    }


async def _count_by_camera(session: AsyncSession, camera_col, id_col) -> dict[int, int]:
    rows = await session.execute(select(camera_col, func.count(id_col)).group_by(camera_col))
    return {cid: int(n) for cid, n in rows.all()}


async def _footfall_by_camera(session: AsyncSession) -> dict[int, tuple[int, int]]:
    rows = await session.execute(
        select(FootfallEvent.camera_id, FootfallEvent.event_type, func.coalesce(func.sum(FootfallEvent.count), 0))
        .group_by(FootfallEvent.camera_id, FootfallEvent.event_type)
    )
    out: dict[int, list[int]] = {}
    for cid, kind, total in rows.all():
        pair = out.setdefault(cid, [0, 0])
        pair[0 if kind == "entry" else 1] += int(total)
    return {cid: (p[0], p[1]) for cid, p in out.items()}


def _reid_by_camera(camera_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Saved Re-ID video stats per camera; the whole-frame result wins over zone-limited ones."""
    out: dict[int, dict[str, Any]] = {}
    for key, entry in saved_exports().items():
        cam_text, _, zone = key.partition(":")
        if not cam_text.isdigit() or int(cam_text) not in camera_ids:
            continue
        cid = int(cam_text)
        if cid in out and zone != "all":
            continue
        out[cid] = {
            "zone": zone,
            "unique_persons": int(entry.get("unique_persons", 0)),
            "total_detections": int(entry.get("total_detections", 0)),
            "duration_seconds": float(entry.get("duration_seconds", 0.0)),
            "identities": list(entry.get("identities", []))[:_TOP_IDENTITIES],
            "timeline": entry.get("timeline", []),
            "processed_at": entry.get("created_at"),
            "download_url": f"/api/analyzer/reid-video/{entry['job_id']}/download",
            "job_id": entry["job_id"],
        }
    return out


async def _hourly_activity(session: AsyncSession) -> list[dict[str, Any]]:
    """Captured frames and entries/exits per hour for the last 24 hours (oldest first)."""
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    since = now - timedelta(hours=_ACTIVITY_HOURS - 1)
    buckets = {since + timedelta(hours=i): {"frames": 0, "entries": 0, "exits": 0} for i in range(_ACTIVITY_HOURS)}

    hour = func.strftime("%Y-%m-%d %H:00:00", Frame.timestamp)
    for label, n in (await session.execute(
        select(hour, func.count(Frame.id)).where(Frame.timestamp >= since).group_by(hour)
    )).all():
        _add(buckets, label, "frames", n)

    ev_hour = func.strftime("%Y-%m-%d %H:00:00", FootfallEvent.timestamp)
    for label, kind, n in (await session.execute(
        select(ev_hour, FootfallEvent.event_type, func.coalesce(func.sum(FootfallEvent.count), 0))
        .where(FootfallEvent.timestamp >= since).group_by(ev_hour, FootfallEvent.event_type)
    )).all():
        _add(buckets, label, "entries" if kind == "entry" else "exits", n)

    return [{"hour": h.isoformat(), **v} for h, v in sorted(buckets.items())]


def _add(buckets: dict[datetime, dict[str, int]], label: str | None, key: str, n: int) -> None:
    if not label:
        return
    try:
        bucket = buckets.get(datetime.strptime(label, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return
    if bucket is not None:
        bucket[key] += int(n)


async def _latest_queues(session: AsyncSession) -> list[dict[str, Any]]:
    """Most recent completed queue analysis per zone."""
    jobs = (await session.execute(
        select(QueueAnalysisJob, Zone.name)
        .join(Zone, Zone.id == QueueAnalysisJob.zone_id)
        .where(QueueAnalysisJob.status == QueueJobStatus.completed)
        .order_by(QueueAnalysisJob.id.desc())
    )).all()
    seen: set[int] = set()
    out = []
    for job, zone_name in jobs:
        if job.zone_id in seen:
            continue
        seen.add(job.zone_id)
        out.append({
            "camera_id": job.camera_id,
            "zone_id": job.zone_id,
            "zone_name": zone_name,
            "avg_wait_time_sec": job.avg_wait_time_sec,
            "avg_queue_count": job.avg_queue_count,
            "max_queue_count": job.max_queue_count,
        })
    return out


def _source_type(url: str | None) -> str:
    url = (url or "").lower()
    if url.startswith("file://"):
        return "Video File"
    if url.startswith("webcam://"):
        return "Webcam"
    return "RTSP"
