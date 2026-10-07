from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AnalysisJob,
    AnalysisJobStatus,
    Camera,
    FootfallEvent,
    Frame,
    FrameStatus,
    PersonDetection,
    PersonTrack,
    ReconstructionJob,
    ReconstructionStatus,
    RuntimeConfig,
    SegmentStatus,
    Stream,
    StreamStatus,
    VideoSegment,
    Zone,
    QueueAnalysisJob,
    QueueJobStatus,
    QueueFrameResult,
    FaceReference,
)



async def create_camera(session: AsyncSession, name: str, rtsp_url: str | None, location: str | None) -> Camera:
    camera = Camera(name=name, rtsp_url=rtsp_url, location=location)
    session.add(camera)
    await session.commit()
    await session.refresh(camera)
    return camera


async def list_cameras(
    session: AsyncSession,
    camera_ids: list[int] | None = None,
    include_deleted: bool = False,
) -> list[Camera]:
    stmt = select(Camera)
    if not include_deleted:
        stmt = stmt.where(Camera.is_deleted == False)

    if camera_ids is not None:
        if not camera_ids:
            return []
        stmt = stmt.where(Camera.id.in_(camera_ids))
    result = await session.execute(stmt.order_by(Camera.id))
    return list(result.scalars())


async def get_camera(session: AsyncSession, camera_id: int) -> Camera | None:
    result = await session.execute(select(Camera).where(Camera.id == camera_id))
    return result.scalar_one_or_none()


async def update_camera(session: AsyncSession, camera: Camera, **updates) -> Camera:
    for key, value in updates.items():
        setattr(camera, key, value)
    session.add(camera)
    await session.commit()
    await session.refresh(camera)
    return camera


async def delete_camera(session: AsyncSession, camera: Camera) -> None:
    camera.is_deleted = True
    session.add(camera)
    await session.commit()


async def allocate_port(session: AsyncSession, base_port: int = 8554) -> int:
    result = await session.execute(select(func.max(Stream.port)))
    max_port = result.scalar_one_or_none() or (base_port - 1)
    return max(base_port, (max_port + 1))


async def get_stream_by_camera(session: AsyncSession, camera_id: int) -> Stream | None:
    result = await session.execute(select(Stream).where(Stream.camera_id == camera_id))
    return result.scalar_one_or_none()


async def get_stream(session: AsyncSession, stream_id: int) -> Stream | None:
    result = await session.execute(select(Stream).where(Stream.id == stream_id))
    return result.scalar_one_or_none()


async def list_streams(session: AsyncSession) -> list[Stream]:
    result = await session.execute(select(Stream).order_by(Stream.id))
    return list(result.scalars())


async def list_streams_for_cameras(session: AsyncSession, camera_ids: list[int]) -> dict[int, Stream]:
    if not camera_ids:
        return {}

    result = await session.execute(
        select(Stream)
        .where(Stream.camera_id.in_(camera_ids))
        .order_by(Stream.camera_id, Stream.id.desc())
    )
    streams_by_camera: dict[int, Stream] = {}
    for stream in result.scalars():
        streams_by_camera.setdefault(stream.camera_id, stream)
    return streams_by_camera


async def delete_stream(session: AsyncSession, stream: Stream) -> None:
    # Stream rows are referenced by historical frame/segment/footfall records.
    # Preserve those records while allowing stream deletion.
    await session.execute(update(Frame).where(Frame.stream_id == stream.id).values(stream_id=None))
    await session.execute(update(VideoSegment).where(VideoSegment.stream_id == stream.id).values(stream_id=None))
    await session.execute(update(FootfallEvent).where(FootfallEvent.stream_id == stream.id).values(stream_id=None))
    await session.delete(stream)
    await session.commit()


async def create_stream(session: AsyncSession, camera_id: int, port: int | None) -> Stream:
    stream = Stream(
        camera_id=camera_id,
        port=port,
        status=StreamStatus.starting,
        started_at=datetime.utcnow(),
    )
    session.add(stream)
    await session.commit()
    await session.refresh(stream)
    return stream


async def update_stream_status(session: AsyncSession, stream: Stream, status: StreamStatus) -> Stream:
    stream.status = status
    if status == StreamStatus.running:
        stream.started_at = stream.started_at or datetime.utcnow()
        stream.stopped_at = None
    if status in {StreamStatus.stopped, StreamStatus.error}:
        stream.stopped_at = datetime.utcnow()
    session.add(stream)
    await session.commit()
    await session.refresh(stream)
    return stream


async def update_stream_heartbeat(session: AsyncSession, stream: Stream, timestamp: datetime) -> None:
    stream.last_heartbeat = timestamp
    if stream.status != StreamStatus.running:
        stream.status = StreamStatus.running
    session.add(stream)
    await session.commit()


async def create_zone(session: AsyncSession, camera_id: int, name: str, polygon: dict, capacity: int | None = None, expected_wait_time_sec: int | None = None) -> Zone:
    zone = Zone(camera_id=camera_id, name=name, polygon=polygon, capacity=capacity, expected_wait_time_sec=expected_wait_time_sec)
    session.add(zone)
    await session.commit()
    await session.refresh(zone)
    return zone


async def list_zones(session: AsyncSession, camera_id: int | None = None) -> list[Zone]:
    stmt = select(Zone)
    if camera_id is not None:
        stmt = stmt.where(Zone.camera_id == camera_id)
    result = await session.execute(stmt.order_by(Zone.id))
    return list(result.scalars())


async def list_zones_for_cameras(session: AsyncSession, camera_ids: list[int]) -> dict[int, list[Zone]]:
    if not camera_ids:
        return {}

    result = await session.execute(
        select(Zone)
        .where(Zone.camera_id.in_(camera_ids))
        .order_by(Zone.camera_id, Zone.id)
    )
    zones_by_camera: defaultdict[int, list[Zone]] = defaultdict(list)
    for zone in result.scalars():
        zones_by_camera[zone.camera_id].append(zone)
    return dict(zones_by_camera)


async def delete_zone(session: AsyncSession, zone: Zone) -> None:
    await session.delete(zone)
    await session.commit()


async def get_zone(session: AsyncSession, zone_id: int) -> Zone | None:
    result = await session.execute(select(Zone).where(Zone.id == zone_id))
    return result.scalar_one_or_none()


async def get_first_zone(session: AsyncSession, camera_id: int) -> Zone | None:
    result = await session.execute(
        select(Zone).where(Zone.camera_id == camera_id).order_by(Zone.id).limit(1)
    )
    return result.scalar_one_or_none()


async def create_frame(
    session: AsyncSession,
    camera_id: int,
    stream_id: int | None,
    path: str,
    timestamp: datetime,
    width: int | None,
    height: int | None,
    size_bytes: int,
) -> Frame:
    existing = await session.execute(select(Frame).where(Frame.path == path))
    frame = existing.scalar_one_or_none()
    if frame:
        return frame
    frame = Frame(
        camera_id=camera_id,
        stream_id=stream_id,
        path=path,
        timestamp=timestamp,
        width=width,
        height=height,
        size_bytes=size_bytes,
        status=FrameStatus.captured,
    )
    session.add(frame)
    await session.commit()
    await session.refresh(frame)
    return frame


async def get_unprocessed_frames(session: AsyncSession, limit: int = 50) -> list[Frame]:
    stmt = (
        select(Frame)
        .where(Frame.status == FrameStatus.captured)
        .order_by(Frame.timestamp)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def mark_frame_processed(session: AsyncSession, frame: Frame) -> None:
    frame.status = FrameStatus.processed
    frame.processed_at = datetime.utcnow()
    session.add(frame)
    await session.commit()


async def get_first_frame(session: AsyncSession, camera_id: int) -> Frame | None:
    result = await session.execute(
        select(Frame).where(Frame.camera_id == camera_id).order_by(Frame.timestamp).limit(1)
    )
    return result.scalar_one_or_none()


async def list_frames_for_camera(
    session: AsyncSession,
    camera_id: int,
    limit: int | None = 500,
    descending: bool = False,
) -> list[Frame]:
    stmt = select(Frame).where(Frame.camera_id == camera_id)
    stmt = stmt.order_by(Frame.timestamp.desc() if descending else Frame.timestamp)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars())


async def delete_frames_for_camera(session: AsyncSession, camera_id: int) -> list[str]:
    # Fetch only the paths (avoid loading full ORM objects for potentially thousands of frames)
    paths_result = await session.execute(select(Frame.path).where(Frame.camera_id == camera_id))
    paths = list(paths_result.scalars())

    ids_result = await session.execute(select(Frame.id).where(Frame.camera_id == camera_id))
    frame_ids = list(ids_result.scalars())

    if frame_ids:
        # Manually cascade deletes for SQLite's sake
        await session.execute(delete(PersonDetection).where(PersonDetection.frame_id.in_(frame_ids)))
        await session.execute(delete(QueueFrameResult).where(QueueFrameResult.frame_id.in_(frame_ids)))
        await session.execute(delete(Frame).where(Frame.camera_id == camera_id))

    await session.commit()
    return paths


async def delete_segments_for_camera(session: AsyncSession, camera_id: int) -> list[str]:
    paths_result = await session.execute(select(VideoSegment.path).where(VideoSegment.camera_id == camera_id))
    paths = list(paths_result.scalars())
    await session.execute(delete(VideoSegment).where(VideoSegment.camera_id == camera_id))
    await session.commit()
    return paths


async def delete_reconstruction_jobs_for_camera(session: AsyncSession, camera_id: int) -> list[str]:
    paths_result = await session.execute(
        select(ReconstructionJob.output_path).where(ReconstructionJob.camera_id == camera_id)
    )
    output_paths = [p for p in paths_result.scalars() if p]
    await session.execute(delete(ReconstructionJob).where(ReconstructionJob.camera_id == camera_id))
    await session.commit()
    return output_paths


async def count_cameras_by_rtsp_url(
    session: AsyncSession,
    rtsp_url: str,
    exclude_camera_id: int | None = None,
) -> int:
    stmt = select(func.count(Camera.id)).where(Camera.rtsp_url == rtsp_url)
    if exclude_camera_id is not None:
        stmt = stmt.where(Camera.id != exclude_camera_id)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def get_latest_frame(session: AsyncSession, camera_id: int) -> Frame | None:
    result = await session.execute(
        select(Frame).where(Frame.camera_id == camera_id).order_by(Frame.timestamp.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_frames_for_cameras(session: AsyncSession, camera_ids: list[int]) -> dict[int, Frame]:
    if not camera_ids:
        return {}

    latest_frame_times = (
        select(
            Frame.camera_id.label("camera_id"),
            func.max(Frame.timestamp).label("max_timestamp"),
        )
        .where(Frame.camera_id.in_(camera_ids))
        .group_by(Frame.camera_id)
        .subquery()
    )

    result = await session.execute(
        select(Frame)
        .join(
            latest_frame_times,
            (Frame.camera_id == latest_frame_times.c.camera_id)
            & (Frame.timestamp == latest_frame_times.c.max_timestamp),
        )
        .order_by(Frame.camera_id, Frame.id.desc())
    )
    frames_by_camera: dict[int, Frame] = {}
    for frame in result.scalars():
        frames_by_camera.setdefault(frame.camera_id, frame)
    return frames_by_camera


async def get_frames_for_range(
    session: AsyncSession,
    camera_id: int,
    start: datetime,
    end: datetime,
) -> list[Frame]:
    stmt = (
        select(Frame)
        .where(Frame.camera_id == camera_id)
        .where(Frame.timestamp >= start)
        .where(Frame.timestamp <= end)
        .order_by(Frame.timestamp)
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def create_segment(
    session: AsyncSession,
    camera_id: int,
    stream_id: int | None,
    path: str,
    start_time: datetime,
    end_time: datetime,
    duration_seconds: float,
    size_bytes: int,
) -> VideoSegment:
    existing = await session.execute(select(VideoSegment).where(VideoSegment.path == path))
    segment = existing.scalar_one_or_none()
    if segment:
        return segment
    segment = VideoSegment(
        camera_id=camera_id,
        stream_id=stream_id,
        path=path,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration_seconds,
        size_bytes=size_bytes,
        status=SegmentStatus.recorded,
    )
    session.add(segment)
    await session.commit()
    await session.refresh(segment)
    return segment


async def get_segments_for_range(
    session: AsyncSession,
    camera_id: int,
    start: datetime,
    end: datetime,
) -> list[VideoSegment]:
    stmt = (
        select(VideoSegment)
        .where(VideoSegment.camera_id == camera_id)
        .where(VideoSegment.end_time >= start)
        .where(VideoSegment.start_time <= end)
        .order_by(VideoSegment.start_time)
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def get_unprocessed_segments(session: AsyncSession, limit: int = 20) -> list[VideoSegment]:
    stmt = (
        select(VideoSegment)
        .where(VideoSegment.status == SegmentStatus.recorded)
        .order_by(VideoSegment.start_time)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def mark_segment_processed(session: AsyncSession, segment: VideoSegment) -> None:
    segment.status = SegmentStatus.processed
    segment.processed_at = datetime.utcnow()
    session.add(segment)
    await session.commit()


async def create_reconstruction_job(
    session: AsyncSession,
    camera_id: int,
    start_time: datetime,
    end_time: datetime,
) -> ReconstructionJob:
    job = ReconstructionJob(
        camera_id=camera_id,
        start_time=start_time,
        end_time=end_time,
        status=ReconstructionStatus.pending,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def update_reconstruction_job(
    session: AsyncSession,
    job: ReconstructionJob,
    status: ReconstructionStatus,
    output_path: str | None = None,
    error: str | None = None,
) -> ReconstructionJob:
    job.status = status
    job.output_path = output_path
    job.error = error
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_reconstruction_job(session: AsyncSession, job_id: int) -> ReconstructionJob | None:
    result = await session.execute(select(ReconstructionJob).where(ReconstructionJob.id == job_id))
    return result.scalar_one_or_none()


async def list_reconstruction_jobs(session: AsyncSession) -> list[ReconstructionJob]:
    result = await session.execute(select(ReconstructionJob).order_by(ReconstructionJob.id.desc()))
    return list(result.scalars())


async def upsert_runtime_config(session: AsyncSession, key: str, value: dict) -> RuntimeConfig:
    existing = await session.execute(select(RuntimeConfig).where(RuntimeConfig.key == key))
    config = existing.scalar_one_or_none()
    if config:
        config.value = value
        config.updated_at = datetime.utcnow()
    else:
        config = RuntimeConfig(key=key, value=value, updated_at=datetime.utcnow())
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config


async def list_runtime_config(session: AsyncSession) -> list[RuntimeConfig]:
    result = await session.execute(select(RuntimeConfig).order_by(RuntimeConfig.key))
    return list(result.scalars())


async def get_runtime_config(session: AsyncSession, key: str) -> RuntimeConfig | None:
    result = await session.execute(select(RuntimeConfig).where(RuntimeConfig.key == key))
    return result.scalar_one_or_none()


async def delete_old_segments(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(
        select(VideoSegment).where(VideoSegment.end_time < cutoff)
    )
    segments = list(result.scalars())
    deleted = 0
    for segment in segments:
        await session.delete(segment)
        deleted += 1
    await session.commit()
    return deleted


async def list_segments_older_than(session: AsyncSession, cutoff: datetime) -> list[VideoSegment]:
    result = await session.execute(
        select(VideoSegment).where(VideoSegment.end_time < cutoff)
    )
    return list(result.scalars())


async def delete_old_frames(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(select(Frame).where(Frame.timestamp < cutoff))
    frames = list(result.scalars())
    deleted = 0
    for frame in frames:
        await session.delete(frame)
        deleted += 1
    await session.commit()
    return deleted


async def list_frames_older_than(session: AsyncSession, cutoff: datetime) -> list[Frame]:
    result = await session.execute(select(Frame).where(Frame.timestamp < cutoff))
    return list(result.scalars())


async def count_metrics(session: AsyncSession) -> dict:
    cameras = await session.execute(select(func.count(Camera.id)))
    streams = await session.execute(select(func.count(Stream.id)))
    running_streams = await session.execute(
        select(func.count(Stream.id)).where(Stream.status == StreamStatus.running)
    )
    segments = await session.execute(select(func.count(VideoSegment.id)))
    frames = await session.execute(select(func.count(Frame.id)))
    events = await session.execute(select(func.count(FootfallEvent.id)))
    return {
        "cameras": cameras.scalar_one() or 0,
        "streams": streams.scalar_one() or 0,
        "running_streams": running_streams.scalar_one() or 0,
        "segments": segments.scalar_one() or 0,
        "frames": frames.scalar_one() or 0,
        "footfall_events": events.scalar_one() or 0,
    }


async def create_footfall_event(
    session: AsyncSession,
    camera_id: int,
    stream_id: int | None,
    zone_id: int | None,
    timestamp: datetime,
    count: int,
    event_type: str = "entry",
) -> FootfallEvent:
    event = FootfallEvent(
        camera_id=camera_id,
        stream_id=stream_id,
        zone_id=zone_id,
        timestamp=timestamp,
        event_type=event_type,
        count=count,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


def _normalize_footfall_event_type(event_type: str | None) -> str:
    if not event_type:
        return "entry"
    normalized = event_type.strip().lower()
    return "exit" if normalized == "exit" else "entry"


SERVICE_STAFF_KEYWORDS = (
    "staff",
    "service",
    "counter",
    "desk",
    "checkout",
    "cashier",
    "support",
)
ZONE_HEATMAP_GRID_X = 12
ZONE_HEATMAP_GRID_Y = 8
MAX_ZONE_PATH_TRACKS = 30
MAX_ZONE_PATH_POINTS = 120


async def query_footfall(
    session: AsyncSession,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
    zone_id: int | None = None,
    source_type: str = "rtsp",
) -> dict:
    stmt = select(FootfallEvent).where(FootfallEvent.camera_id == camera_id)
    if zone_id is not None:
        stmt = stmt.where(FootfallEvent.zone_id == zone_id)
    if start is not None:
        stmt = stmt.where(FootfallEvent.timestamp >= start)
    if end is not None:
        stmt = stmt.where(FootfallEvent.timestamp <= end)

    result = await session.execute(stmt.order_by(FootfallEvent.timestamp, FootfallEvent.id))
    events = list(result.scalars())

    zone_rows = await list_zones(session, camera_id=camera_id)
    zone_names = {zone.id: zone.name for zone in zone_rows}

    total_entries = 0
    total_exits = 0
    zone_totals_by_id: dict[int | None, dict] = {}
    series_by_time_zone: dict[tuple[datetime, int | None], dict] = {}
    series: list[dict] = []

    # Always include configured zones even when there are no events yet.
    for zone in zone_rows:
        zone_key = int(zone.id)
        if zone_id is not None and zone_key != zone_id:
            continue
        zone_totals_by_id[zone_key] = {
            "zone_id": zone_key,
            "zone_name": zone.name,
            "total_entries": 0,
            "total_exits": 0,
            "net_flow": 0,
        }

    for event in events:
        event_count = int(event.count or 0)
        normalized_type = _normalize_footfall_event_type(getattr(event, "event_type", None))
        entry_count = event_count if normalized_type == "entry" else 0
        exit_count = event_count if normalized_type == "exit" else 0

        total_entries += entry_count
        total_exits += exit_count

        zone_key = int(event.zone_id) if event.zone_id is not None else None
        bucket = zone_totals_by_id.setdefault(
            zone_key,
            {
                "zone_id": zone_key,
                "zone_name": zone_names.get(zone_key) if zone_key is not None else "Unassigned",
                "total_entries": 0,
                "total_exits": 0,
                "net_flow": 0,
            },
        )
        bucket["total_entries"] += entry_count
        bucket["total_exits"] += exit_count
        bucket["net_flow"] = bucket["total_entries"] - bucket["total_exits"]

        # Group by minute for chart smoothing
        bucket_ts = event.timestamp.replace(second=0, microsecond=0)
        time_key = (bucket_ts, zone_key)

        if time_key not in series_by_time_zone:
            series_by_time_zone[time_key] = {
                "timestamp": bucket_ts.isoformat(),
                "zone_id": zone_key,
                "event_type": "aggregated",
                "count": 0,
                "entry_count": 0,
                "exit_count": 0,
                "net_count": 0,
            }

        series_by_time_zone[time_key]["count"] += event_count
        series_by_time_zone[time_key]["entry_count"] += entry_count
        series_by_time_zone[time_key]["exit_count"] += exit_count
        series_by_time_zone[time_key]["net_count"] += entry_count - exit_count

    # Sort the aggregated series chronologically
    sorted_time_keys = sorted(series_by_time_zone.keys(), key=lambda k: (k[0], k[1] or 0))
    for k in sorted_time_keys:
        series.append(series_by_time_zone[k])

    zone_totals = list(zone_totals_by_id.values())
    zone_totals.sort(
        key=lambda item: (
            item["zone_id"] is None,
            item["zone_id"] if item["zone_id"] is not None else 0,
        )
    )

    availability = await _compute_video_availability(
        session=session,
        camera_id=camera_id,
        start=start,
        end=end,
    )
    peak_traffic_hour = _compute_peak_hourly_traffic(events)
    peak_person_count_hour = await _compute_peak_hourly_person_count(
        session=session,
        camera_id=camera_id,
        start=start,
        end=end,
        zone_id=zone_id,
        zone_names=zone_names,
    )
    most_crowded_quarter = None
    if source_type == "video":
        most_crowded_quarter = await _compute_video_crowded_quarter(
            session=session,
            camera_id=camera_id,
            start=start,
            end=end,
            zone_id=zone_id,
            zone_names=zone_names,
        )

    return {
        "total": total_entries,
        "total_entries": total_entries,
        "total_exits": total_exits,
        "net_flow": total_entries - total_exits,
        "series": series,
        "zone_totals": zone_totals,
        "video_available_seconds": int(availability.get("video_available_seconds", 0)),
        "video_available_hours": float(availability.get("video_available_hours", 0.0)),
        "peak_traffic_hour": peak_traffic_hour,
        "peak_person_count_hour": peak_person_count_hour,
        "source_type": source_type,
        "most_crowded_quarter": most_crowded_quarter,
    }


async def query_zone_analysis(
    session: AsyncSession,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
    zone_id: int | None = None,
) -> dict:
    zone_rows = await list_zones(session, camera_id=camera_id)
    zone_rows = [zone for zone in zone_rows if zone_id is None or int(zone.id) == int(zone_id)]
    zone_map = {int(zone.id): zone for zone in zone_rows}
    service_zone_ids = {zone_id_value for zone_id_value, zone in zone_map.items() if _is_service_staff_zone(zone)}

    window_start, window_end = await _resolve_time_window_bounds(
        session=session,
        camera_id=camera_id,
        start=start,
        end=end,
    )
    if window_start is None or window_end is None:
        return {
            "start_time": None,
            "end_time": None,
            "frame_interval_seconds": 1.0,
            "congestion_by_zone": _empty_zone_congestion_rows(zone_rows, service_zone_ids),
            "tracked_paths": [],
            "heatmaps": _empty_zone_heatmaps(zone_rows),
            "service_staff_presence": _empty_service_presence_rows(zone_rows, service_zone_ids),
            "summary": {
                "total_zones": len(zone_rows),
                "service_zones": len(service_zone_ids),
                "tracked_paths": 0,
                "estimated_staff_tracks": 0,
                "estimated_person_tracks": 0,
                "peak_congestion_zone": None,
            },
        }

    if start is not None and start > window_start:
        window_start = start
    if end is not None and end < window_end:
        window_end = end

    detection_rows = await _get_zone_detection_rows(
        session=session,
        camera_id=camera_id,
        start=window_start,
        end=window_end,
        zone_id=zone_id,
    )
    deduped_rows = _dedupe_zone_detection_rows(detection_rows)
    frame_interval_seconds = _estimate_frame_interval_seconds(deduped_rows)

    congestion_rows = _build_zone_congestion_rows(
        zone_rows=zone_rows,
        detection_rows=deduped_rows,
        service_zone_ids=service_zone_ids,
    )
    heatmaps = _build_zone_heatmaps(zone_rows=zone_rows, detection_rows=deduped_rows)
    tracked_paths, service_presence_rows, summary = _build_paths_and_presence(
        zone_rows=zone_rows,
        detection_rows=deduped_rows,
        service_zone_ids=service_zone_ids,
        frame_interval_seconds=frame_interval_seconds,
    )
    summary["peak_congestion_zone"] = _resolve_peak_congestion_zone(congestion_rows)

    return {
        "start_time": window_start.isoformat(),
        "end_time": window_end.isoformat(),
        "frame_interval_seconds": round(frame_interval_seconds, 2),
        "congestion_by_zone": congestion_rows,
        "tracked_paths": tracked_paths,
        "heatmaps": heatmaps,
        "service_staff_presence": service_presence_rows,
        "summary": summary,
    }


def _is_service_staff_zone(zone: Zone) -> bool:
    name_text = (getattr(zone, "name", "") or "").strip().lower()
    polygon = getattr(zone, "polygon", None) or {}
    if any(keyword in name_text for keyword in SERVICE_STAFF_KEYWORDS):
        return True

    if isinstance(polygon, dict):
        for key in ("role", "zone_type", "category", "label"):
            raw = polygon.get(key)
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if any(keyword in lowered for keyword in SERVICE_STAFF_KEYWORDS):
                    return True
        for key in ("staff_zone", "service_zone", "is_staff", "is_service"):
            raw = polygon.get(key)
            if isinstance(raw, bool) and raw:
                return True
    return False


async def _get_zone_detection_rows(
    session: AsyncSession,
    camera_id: int,
    start: datetime,
    end: datetime,
    zone_id: int | None,
) -> list[dict]:
    stmt = (
        select(
            Frame.id,
            Frame.timestamp,
            Frame.width,
            Frame.height,
            PersonTrack.job_id,
            PersonTrack.track_id,
            PersonDetection.in_zone_id,
            PersonDetection.bbox_x1,
            PersonDetection.bbox_y1,
            PersonDetection.bbox_x2,
            PersonDetection.bbox_y2,
        )
        .join(PersonDetection, PersonDetection.frame_id == Frame.id)
        .join(PersonTrack, PersonDetection.track_id == PersonTrack.id)
        .join(AnalysisJob, PersonTrack.job_id == AnalysisJob.id)
        .where(Frame.camera_id == camera_id)
        .where(AnalysisJob.status == AnalysisJobStatus.completed)
        .where(Frame.timestamp >= start)
        .where(Frame.timestamp <= end)
        .where(PersonDetection.in_zone_id.is_not(None))
    )
    if zone_id is not None:
        stmt = stmt.where(PersonDetection.in_zone_id == zone_id)

    stmt = stmt.order_by(Frame.timestamp, Frame.id, PersonTrack.job_id, PersonTrack.track_id)
    rows = (await session.execute(stmt)).all()
    output: list[dict] = []
    for row in rows:
        if row.in_zone_id is None:
            continue
        output.append(
            {
                "frame_id": int(row.id),
                "timestamp": row.timestamp,
                "frame_width": int(row.width) if row.width is not None else None,
                "frame_height": int(row.height) if row.height is not None else None,
                "job_id": int(row.job_id),
                "track_id": int(row.track_id),
                "zone_id": int(row.in_zone_id),
                "x1": float(row.bbox_x1),
                "y1": float(row.bbox_y1),
                "x2": float(row.bbox_x2),
                "y2": float(row.bbox_y2),
            }
        )
    return output


def _dedupe_zone_detection_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []

    job_counts: dict[tuple[int, int, int], int] = defaultdict(int)
    for row in rows:
        job_key = (int(row["job_id"]), int(row["frame_id"]), int(row["zone_id"]))
        job_counts[job_key] += 1

    selected_job_for_frame_zone: dict[tuple[int, int], tuple[int, int]] = {}
    for (job_id, frame_id, zone_id), count in job_counts.items():
        key = (frame_id, zone_id)
        previous = selected_job_for_frame_zone.get(key)
        if previous is None:
            selected_job_for_frame_zone[key] = (job_id, count)
            continue
        prev_job_id, prev_count = previous
        if count > prev_count or (count == prev_count and job_id > prev_job_id):
            selected_job_for_frame_zone[key] = (job_id, count)

    output: list[dict] = []
    for row in rows:
        frame_zone_key = (int(row["frame_id"]), int(row["zone_id"]))
        selected = selected_job_for_frame_zone.get(frame_zone_key)
        if selected is None:
            continue
        if int(row["job_id"]) != int(selected[0]):
            continue
        output.append(row)
    return output


def _estimate_frame_interval_seconds(rows: list[dict]) -> float:
    if not rows:
        return 1.0
    timestamps = sorted({row["timestamp"] for row in rows if row.get("timestamp") is not None})
    if len(timestamps) < 2:
        return 1.0
    diffs = [
        (timestamps[index + 1] - timestamps[index]).total_seconds()
        for index in range(len(timestamps) - 1)
        if (timestamps[index + 1] - timestamps[index]).total_seconds() > 0
    ]
    if not diffs:
        return 1.0
    return float(max(0.2, min(10.0, median(diffs))))


def _build_zone_congestion_rows(
    zone_rows: list[Zone],
    detection_rows: list[dict],
    service_zone_ids: set[int],
) -> list[dict]:
    frame_counts_by_zone: dict[int, dict[int, int]] = defaultdict(dict)
    latest_key_by_zone: dict[int, tuple[datetime, int]] = {}
    latest_count_by_zone: dict[int, int] = defaultdict(int)

    for row in detection_rows:
        zone_id = int(row["zone_id"])
        frame_id = int(row["frame_id"])
        frame_counts = frame_counts_by_zone.setdefault(zone_id, {})
        frame_counts[frame_id] = frame_counts.get(frame_id, 0) + 1
        timestamp = row.get("timestamp")
        if timestamp is not None:
            row_key = (timestamp, frame_id)
            previous = latest_key_by_zone.get(zone_id)
            if previous is None or row_key > previous:
                latest_key_by_zone[zone_id] = row_key
                latest_count_by_zone[zone_id] = int(frame_counts[frame_id])
            elif previous == row_key:
                latest_count_by_zone[zone_id] = int(frame_counts[frame_id])

    rows: list[dict] = []
    for zone in zone_rows:
        zone_id = int(zone.id)
        frame_counts = frame_counts_by_zone.get(zone_id, {})
        counts = list(frame_counts.values())
        current_count = int(latest_count_by_zone.get(zone_id, 0))
        peak_count = max(counts) if counts else 0
        avg_count = round(sum(counts) / len(counts), 2) if counts else 0.0
        capacity = int(zone.capacity) if zone.capacity is not None else None

        current_util = round((current_count / capacity) * 100, 1) if capacity and capacity > 0 else None
        peak_util = round((peak_count / capacity) * 100, 1) if capacity and capacity > 0 else None
        congestion_level = _resolve_congestion_level(current_count, peak_count, capacity)

        rows.append(
            {
                "zone_id": zone_id,
                "zone_name": zone.name,
                "is_service_zone": zone_id in service_zone_ids,
                "capacity": capacity,
                "current_count": int(current_count),
                "peak_count": int(peak_count),
                "avg_count": float(avg_count),
                "current_utilization_pct": current_util,
                "peak_utilization_pct": peak_util,
                "congestion_level": congestion_level,
                "latest_observed_at": latest_key_by_zone.get(zone_id)[0].isoformat() if latest_key_by_zone.get(zone_id) else None,
            }
        )
    return rows


def _resolve_congestion_level(current_count: int, peak_count: int, capacity: int | None) -> str:
    if capacity and capacity > 0:
        utilization = (current_count / capacity) * 100
        if utilization >= 100:
            return "critical"
        if utilization >= 80:
            return "high"
        if utilization >= 50:
            return "medium"
        return "low"

    if peak_count >= 20 or current_count >= 20:
        return "critical"
    if peak_count >= 10 or current_count >= 10:
        return "high"
    if peak_count >= 5 or current_count >= 5:
        return "medium"
    return "low"


def _build_zone_heatmaps(zone_rows: list[Zone], detection_rows: list[dict]) -> list[dict]:
    grid_by_zone: dict[int, list[list[int]]] = {
        int(zone.id): [[0 for _ in range(ZONE_HEATMAP_GRID_X)] for _ in range(ZONE_HEATMAP_GRID_Y)]
        for zone in zone_rows
    }

    for row in detection_rows:
        zone_id = int(row["zone_id"])
        if zone_id not in grid_by_zone:
            continue
        frame_width = row.get("frame_width")
        frame_height = row.get("frame_height")
        if not frame_width or not frame_height or frame_width <= 0 or frame_height <= 0:
            continue

        cx = ((float(row["x1"]) + float(row["x2"])) / 2.0) / float(frame_width)
        cy = ((float(row["y1"]) + float(row["y2"])) / 2.0) / float(frame_height)
        if cx < 0 or cy < 0:
            continue
        cx = min(max(cx, 0.0), 0.999999)
        cy = min(max(cy, 0.0), 0.999999)
        cell_x = int(cx * ZONE_HEATMAP_GRID_X)
        cell_y = int(cy * ZONE_HEATMAP_GRID_Y)
        grid_by_zone[zone_id][cell_y][cell_x] += 1

    output: list[dict] = []
    zone_map = {int(zone.id): zone for zone in zone_rows}
    for zone_id, grid in grid_by_zone.items():
        max_cell = max((max(row) for row in grid), default=0)
        normalized = [
            [round((cell / max_cell), 3) if max_cell > 0 else 0.0 for cell in row]
            for row in grid
        ]
        zone_obj = zone_map.get(zone_id)
        output.append(
            {
                "zone_id": zone_id,
                "zone_name": zone_obj.name if zone_obj is not None else f"Zone {zone_id}",
                "grid_x": ZONE_HEATMAP_GRID_X,
                "grid_y": ZONE_HEATMAP_GRID_Y,
                "max_cell_count": int(max_cell),
                "grid": grid,
                "normalized_grid": normalized,
            }
        )
    return output


def _build_paths_and_presence(
    zone_rows: list[Zone],
    detection_rows: list[dict],
    service_zone_ids: set[int],
    frame_interval_seconds: float,
) -> tuple[list[dict], list[dict], dict]:
    zone_map = {int(zone.id): zone for zone in zone_rows}

    tracks: dict[str, dict] = {}
    for row in detection_rows:
        track_key = f"{int(row['job_id'])}:{int(row['track_id'])}"
        track = tracks.get(track_key)
        if track is None:
            track = {
                "track_key": track_key,
                "job_id": int(row["job_id"]),
                "track_id": int(row["track_id"]),
                "first_seen": row["timestamp"],
                "last_seen": row["timestamp"],
                "detections": 0,
                "zone_hits": defaultdict(int),
                "zone_sequence": [],
                "last_zone": None,
                "path_points": [],
            }
            tracks[track_key] = track

        zone_id = int(row["zone_id"])
        timestamp = row["timestamp"]
        if timestamp < track["first_seen"]:
            track["first_seen"] = timestamp
        if timestamp > track["last_seen"]:
            track["last_seen"] = timestamp
        track["detections"] += 1
        track["zone_hits"][zone_id] += 1

        if track["last_zone"] != zone_id:
            track["zone_sequence"].append(zone_id)
            track["last_zone"] = zone_id

        frame_width = row.get("frame_width")
        frame_height = row.get("frame_height")
        cx = None
        cy = None
        if frame_width and frame_height and frame_width > 0 and frame_height > 0:
            cx = round((((float(row["x1"]) + float(row["x2"])) / 2.0) / float(frame_width)), 3)
            cy = round((((float(row["y1"]) + float(row["y2"])) / 2.0) / float(frame_height)), 3)
        track["path_points"].append(
            {
                "timestamp": timestamp.isoformat(),
                "zone_id": zone_id,
                "x": cx,
                "y": cy,
            }
        )

    for track in tracks.values():
        if len(track["path_points"]) <= MAX_ZONE_PATH_POINTS:
            continue
        stride = max(1, len(track["path_points"]) // MAX_ZONE_PATH_POINTS)
        sampled = track["path_points"][::stride]
        track["path_points"] = sampled[:MAX_ZONE_PATH_POINTS]

    zone_presence: dict[int, dict] = {
        int(zone.id): {
            "person_tracks": set(),
            "staff_tracks": set(),
            "person_hits": 0,
            "staff_hits": 0,
        }
        for zone in zone_rows
    }

    path_rows: list[dict] = []
    estimated_staff_tracks = 0
    estimated_person_tracks = 0

    sorted_tracks = sorted(tracks.values(), key=lambda item: item["detections"], reverse=True)
    for track in sorted_tracks:
        total_hits = int(track["detections"])
        service_hits = sum(int(track["zone_hits"].get(zone_id, 0)) for zone_id in service_zone_ids)
        service_ratio = (service_hits / total_hits) if total_hits > 0 else 0.0
        is_staff = service_hits >= 3 and service_ratio >= 0.6
        track_type = "staff" if is_staff else "person"

        if is_staff:
            estimated_staff_tracks += 1
        else:
            estimated_person_tracks += 1

        predominant_zone_id = None
        predominant_zone_hits = -1
        for z_id, hits in track["zone_hits"].items():
            if hits > predominant_zone_hits:
                predominant_zone_id = int(z_id)
                predominant_zone_hits = int(hits)

        for z_id, hits in track["zone_hits"].items():
            presence = zone_presence.get(int(z_id))
            if presence is None:
                continue
            if is_staff:
                presence["staff_tracks"].add(track["track_key"])
                presence["staff_hits"] += int(hits)
            else:
                presence["person_tracks"].add(track["track_key"])
                presence["person_hits"] += int(hits)

        zone_sequence_names = [
            zone_map.get(int(z_id)).name if zone_map.get(int(z_id)) else f"Zone {z_id}"
            for z_id in track["zone_sequence"]
        ]
        path_rows.append(
            {
                "track_key": track["track_key"],
                "job_id": int(track["job_id"]),
                "track_id": int(track["track_id"]),
                "track_type": track_type,
                "first_seen": track["first_seen"].isoformat(),
                "last_seen": track["last_seen"].isoformat(),
                "detections": int(total_hits),
                "estimated_dwell_seconds": round(total_hits * frame_interval_seconds, 1),
                "service_zone_ratio": round(service_ratio, 3),
                "predominant_zone_id": predominant_zone_id,
                "predominant_zone_name": zone_map.get(int(predominant_zone_id)).name if predominant_zone_id in zone_map else None,
                "zone_sequence": track["zone_sequence"],
                "zone_sequence_names": zone_sequence_names,
                "path_points": track["path_points"],
            }
        )

    path_rows = path_rows[:MAX_ZONE_PATH_TRACKS]

    service_presence_rows: list[dict] = []
    for zone in zone_rows:
        zone_id = int(zone.id)
        presence = zone_presence.get(zone_id, {})
        person_hits = int(presence.get("person_hits", 0))
        staff_hits = int(presence.get("staff_hits", 0))
        person_seconds = round(person_hits * frame_interval_seconds, 1)
        staff_seconds = round(staff_hits * frame_interval_seconds, 1)
        total_seconds = round(person_seconds + staff_seconds, 1)
        staff_ratio = round((staff_seconds / total_seconds), 3) if total_seconds > 0 else 0.0
        service_presence_rows.append(
            {
                "zone_id": zone_id,
                "zone_name": zone.name,
                "is_service_zone": zone_id in service_zone_ids,
                "person_tracks": len(presence.get("person_tracks", set())),
                "staff_tracks": len(presence.get("staff_tracks", set())),
                "person_presence_seconds": person_seconds,
                "staff_presence_seconds": staff_seconds,
                "total_presence_seconds": total_seconds,
                "staff_presence_ratio": staff_ratio,
            }
        )

    summary = {
        "total_zones": len(zone_rows),
        "service_zones": len(service_zone_ids),
        "tracked_paths": len(path_rows),
        "estimated_staff_tracks": estimated_staff_tracks,
        "estimated_person_tracks": estimated_person_tracks,
    }
    return path_rows, service_presence_rows, summary


def _resolve_peak_congestion_zone(congestion_rows: list[dict]) -> dict | None:
    if not congestion_rows:
        return None
    ranked = sorted(
        congestion_rows,
        key=lambda row: (
            str(row.get("congestion_level", "")) == "critical",
            str(row.get("congestion_level", "")) == "high",
            str(row.get("congestion_level", "")) == "medium",
            float(row.get("current_utilization_pct") or 0.0),
            int(row.get("peak_count") or 0),
        ),
        reverse=True,
    )
    top = ranked[0]
    return {
        "zone_id": top.get("zone_id"),
        "zone_name": top.get("zone_name"),
        "congestion_level": top.get("congestion_level"),
        "current_count": top.get("current_count"),
        "peak_count": top.get("peak_count"),
    }


def _empty_zone_congestion_rows(zone_rows: list[Zone], service_zone_ids: set[int]) -> list[dict]:
    rows: list[dict] = []
    for zone in zone_rows:
        zone_id = int(zone.id)
        rows.append(
            {
                "zone_id": zone_id,
                "zone_name": zone.name,
                "is_service_zone": zone_id in service_zone_ids,
                "capacity": int(zone.capacity) if zone.capacity is not None else None,
                "current_count": 0,
                "peak_count": 0,
                "avg_count": 0.0,
                "current_utilization_pct": None,
                "peak_utilization_pct": None,
                "congestion_level": "low",
                "latest_observed_at": None,
            }
        )
    return rows


def _empty_zone_heatmaps(zone_rows: list[Zone]) -> list[dict]:
    output: list[dict] = []
    for zone in zone_rows:
        output.append(
            {
                "zone_id": int(zone.id),
                "zone_name": zone.name,
                "grid_x": ZONE_HEATMAP_GRID_X,
                "grid_y": ZONE_HEATMAP_GRID_Y,
                "max_cell_count": 0,
                "grid": [[0 for _ in range(ZONE_HEATMAP_GRID_X)] for _ in range(ZONE_HEATMAP_GRID_Y)],
                "normalized_grid": [[0.0 for _ in range(ZONE_HEATMAP_GRID_X)] for _ in range(ZONE_HEATMAP_GRID_Y)],
            }
        )
    return output


def _empty_service_presence_rows(zone_rows: list[Zone], service_zone_ids: set[int]) -> list[dict]:
    rows: list[dict] = []
    for zone in zone_rows:
        zone_id = int(zone.id)
        rows.append(
            {
                "zone_id": zone_id,
                "zone_name": zone.name,
                "is_service_zone": zone_id in service_zone_ids,
                "person_tracks": 0,
                "staff_tracks": 0,
                "person_presence_seconds": 0.0,
                "staff_presence_seconds": 0.0,
                "total_presence_seconds": 0.0,
                "staff_presence_ratio": 0.0,
            }
        )
    return rows


def _compute_peak_hourly_traffic(events: list[FootfallEvent]) -> dict:
    hourly_counts: dict[datetime, int] = {}
    for event in events:
        event_ts = getattr(event, "timestamp", None)
        if event_ts is None:
            continue
        hour_key = event_ts.replace(minute=0, second=0, microsecond=0)
        hourly_counts[hour_key] = int(hourly_counts.get(hour_key, 0)) + int(event.count or 0)

    if not hourly_counts:
        return {
            "hour_start": None,
            "hour_end": None,
            "traffic_count": 0,
        }

    best_count = max(hourly_counts.values())
    best_hours = [hour for hour, value in hourly_counts.items() if value == best_count]
    best_hour = min(best_hours)
    return {
        "hour_start": best_hour.isoformat(),
        "hour_end": (best_hour + timedelta(hours=1)).isoformat(),
        "traffic_count": int(best_count),
    }


async def _resolve_time_window_bounds(
    session: AsyncSession,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    first_ts_stmt = select(func.min(Frame.timestamp)).where(Frame.camera_id == camera_id)
    if end is not None:
        first_ts_stmt = first_ts_stmt.where(Frame.timestamp <= end)
    first_ts_result = await session.execute(first_ts_stmt)
    first_ts = first_ts_result.scalar_one_or_none()
    if first_ts is None:
        first_segment_stmt = select(func.min(VideoSegment.start_time)).where(VideoSegment.camera_id == camera_id)
        if end is not None:
            first_segment_stmt = first_segment_stmt.where(VideoSegment.start_time <= end)
        first_segment_result = await session.execute(first_segment_stmt)
        first_ts = first_segment_result.scalar_one_or_none()

    last_ts_stmt = select(func.max(Frame.timestamp)).where(Frame.camera_id == camera_id)
    if start is not None:
        last_ts_stmt = last_ts_stmt.where(Frame.timestamp >= start)
    last_ts_result = await session.execute(last_ts_stmt)
    last_ts = last_ts_result.scalar_one_or_none()
    if last_ts is None:
        last_segment_stmt = select(func.max(VideoSegment.end_time)).where(VideoSegment.camera_id == camera_id)
        if start is not None:
            last_segment_stmt = last_segment_stmt.where(VideoSegment.end_time >= start)
        last_segment_result = await session.execute(last_segment_stmt)
        last_ts = last_segment_result.scalar_one_or_none()

    query_start = start or first_ts
    query_end = end or last_ts
    if query_start is None or query_end is None or query_end < query_start:
        return None, None
    return query_start, query_end


async def _compute_video_availability(
    session: AsyncSession,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
) -> dict:
    query_start, query_end = await _resolve_time_window_bounds(
        session=session,
        camera_id=camera_id,
        start=start,
        end=end,
    )
    if query_start is None or query_end is None:
        return {
            "video_available_seconds": 0,
            "video_available_hours": 0.0,
        }

    segment_stmt = (
        select(VideoSegment.start_time, VideoSegment.end_time)
        .where(VideoSegment.camera_id == camera_id)
        .where(VideoSegment.end_time >= query_start)
        .where(VideoSegment.start_time <= query_end)
    )
    segment_rows = (await session.execute(segment_stmt)).all()

    total_seconds = 0.0
    for segment_start, segment_end in segment_rows:
        overlap_start = max(segment_start, query_start)
        overlap_end = min(segment_end, query_end)
        if overlap_end > overlap_start:
            total_seconds += (overlap_end - overlap_start).total_seconds()

    if total_seconds <= 0:
        frame_span_stmt = select(
            func.min(Frame.timestamp),
            func.max(Frame.timestamp),
        ).where(Frame.camera_id == camera_id)
        if query_start is not None:
            frame_span_stmt = frame_span_stmt.where(Frame.timestamp >= query_start)
        if query_end is not None:
            frame_span_stmt = frame_span_stmt.where(Frame.timestamp <= query_end)
        frame_span_row = (await session.execute(frame_span_stmt)).first()
        if frame_span_row is not None:
            span_start = frame_span_row[0]
            span_end = frame_span_row[1]
            if span_start is not None and span_end is not None and span_end > span_start:
                total_seconds = (span_end - span_start).total_seconds()

    return {
        "video_available_seconds": int(round(max(total_seconds, 0.0))),
        "video_available_hours": round(max(total_seconds, 0.0) / 3600.0, 2),
    }


async def _compute_peak_hourly_person_count(
    session: AsyncSession,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
    zone_id: int | None,
    zone_names: dict[int, str],
) -> dict:
    frame_rows = await _get_frame_person_counts(
        session=session,
        camera_id=camera_id,
        start=start,
        end=end,
        zone_id=zone_id,
    )

    hourly_max_by_ts: dict[datetime, int] = {}
    for frame_ts, frame_person_count in frame_rows:
        if frame_ts is None:
            continue
        hour_key = frame_ts.replace(minute=0, second=0, microsecond=0)
        count_value = int(frame_person_count or 0)
        previous = hourly_max_by_ts.get(hour_key)
        if previous is None or count_value > previous:
            hourly_max_by_ts[hour_key] = count_value

    if not hourly_max_by_ts:
        return {
            "hour_start": None,
            "hour_end": None,
            "person_count": 0,
            "zone_id": zone_id,
            "zone_name": zone_names.get(zone_id) if zone_id is not None else "All zones",
        }

    best_count = max(hourly_max_by_ts.values())
    best_hours = [hour for hour, value in hourly_max_by_ts.items() if value == best_count]
    best_hour = min(best_hours)

    return {
        "hour_start": best_hour.isoformat(),
        "hour_end": (best_hour + timedelta(hours=1)).isoformat(),
        "person_count": int(best_count),
        "zone_id": zone_id,
        "zone_name": zone_names.get(zone_id) if zone_id is not None else "All zones",
    }


async def _compute_video_crowded_quarter(
    session: AsyncSession,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
    zone_id: int | None,
    zone_names: dict[int, str],
) -> dict | None:
    window_start, window_end = await _resolve_time_window_bounds(
        session=session,
        camera_id=camera_id,
        start=start,
        end=end,
    )
    if window_start is None or window_end is None:
        return None

    total_seconds = (window_end - window_start).total_seconds()
    if total_seconds <= 0:
        return None

    quarter_seconds = total_seconds / 4.0
    if quarter_seconds <= 0:
        return None

    frame_rows = await _get_frame_person_counts(
        session=session,
        camera_id=camera_id,
        start=window_start,
        end=window_end,
        zone_id=zone_id,
    )
    if not frame_rows:
        return None

    quarter_peak_counts = [0, 0, 0, 0]
    for frame_ts, frame_person_count in frame_rows:
        if frame_ts is None:
            continue
        offset_seconds = (frame_ts - window_start).total_seconds()
        raw_index = int(offset_seconds / quarter_seconds) if quarter_seconds > 0 else 0
        quarter_index = max(0, min(3, raw_index))
        quarter_peak_counts[quarter_index] = max(quarter_peak_counts[quarter_index], int(frame_person_count or 0))

    best_quarter_index = 0
    best_peak_count = quarter_peak_counts[0]
    for idx in range(1, 4):
        if quarter_peak_counts[idx] > best_peak_count:
            best_peak_count = quarter_peak_counts[idx]
            best_quarter_index = idx

    quarter_start = window_start + timedelta(seconds=quarter_seconds * best_quarter_index)
    if best_quarter_index == 3:
        quarter_end = window_end
    else:
        quarter_end = window_start + timedelta(seconds=quarter_seconds * (best_quarter_index + 1))

    quarter_labels = ["1st Quarter", "2nd Quarter", "3rd Quarter", "4th Quarter"]
    return {
        "quarter_index": best_quarter_index + 1,
        "quarter_label": quarter_labels[best_quarter_index],
        "start_time": quarter_start.isoformat(),
        "end_time": quarter_end.isoformat(),
        "person_count": int(best_peak_count),
        "zone_id": zone_id,
        "zone_name": zone_names.get(zone_id) if zone_id is not None else "All zones",
    }


async def _get_frame_person_counts(
    session: AsyncSession,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
    zone_id: int | None,
) -> list[tuple[datetime, int]]:
    # Aggregate by frame + analysis job first, then de-duplicate the same frame across
    # multiple completed jobs by keeping the highest observed person count.
    stmt = (
        select(
            Frame.id,
            Frame.timestamp,
            PersonTrack.job_id,
            func.count(PersonDetection.id),
        )
        .join(PersonDetection, PersonDetection.frame_id == Frame.id)
        .join(PersonTrack, PersonDetection.track_id == PersonTrack.id)
        .join(AnalysisJob, PersonTrack.job_id == AnalysisJob.id)
        .where(Frame.camera_id == camera_id)
        .where(AnalysisJob.status == AnalysisJobStatus.completed)
    )
    if start is not None:
        stmt = stmt.where(Frame.timestamp >= start)
    if end is not None:
        stmt = stmt.where(Frame.timestamp <= end)
    if zone_id is not None:
        stmt = stmt.where(PersonDetection.in_zone_id == zone_id)

    stmt = stmt.group_by(Frame.id, Frame.timestamp, PersonTrack.job_id)
    rows = (await session.execute(stmt)).all()

    dedup_by_frame: dict[int, tuple[datetime, int]] = {}
    for frame_id, frame_ts, _job_id, person_count in rows:
        frame_key = int(frame_id)
        count_value = int(person_count or 0)
        existing = dedup_by_frame.get(frame_key)
        if existing is None or count_value > existing[1]:
            dedup_by_frame[frame_key] = (frame_ts, count_value)

    result = list(dedup_by_frame.values())
    result.sort(key=lambda item: item[0])
    return result


# Analysis Job CRUD
async def create_analysis_job(
    session: AsyncSession,
    camera_id: int,
    start_time: datetime,
    end_time: datetime,
) -> AnalysisJob:
    job = AnalysisJob(
        camera_id=camera_id,
        start_time=start_time,
        end_time=end_time,
        status=AnalysisJobStatus.pending,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_analysis_job(session: AsyncSession, job_id: int) -> AnalysisJob | None:
    result = await session.execute(select(AnalysisJob).where(AnalysisJob.id == job_id))
    return result.scalar_one_or_none()


async def list_analysis_jobs(session: AsyncSession, camera_id: int | None = None) -> list[AnalysisJob]:
    stmt = select(AnalysisJob)
    if camera_id is not None:
        stmt = stmt.where(AnalysisJob.camera_id == camera_id)
    result = await session.execute(stmt.order_by(AnalysisJob.id.desc()))
    return list(result.scalars())


async def update_analysis_job(
    session: AsyncSession,
    job: AnalysisJob,
    status: AnalysisJobStatus | None = None,
    total_frames: int | None = None,
    processed_frames: int | None = None,
    unique_persons: int | None = None,
    error: str | None = None,
) -> AnalysisJob:
    if status is not None:
        job.status = status
    if total_frames is not None:
        job.total_frames = total_frames
    if processed_frames is not None:
        job.processed_frames = processed_frames
    if unique_persons is not None:
        job.unique_persons = unique_persons
    if error is not None:
        job.error = error
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


# Person Track CRUD
async def create_person_track(
    session: AsyncSession,
    job_id: int,
    track_id: int,
    camera_id: int,
    zone_id: int | None,
    first_seen: datetime,
    last_seen: datetime,
    appearances: int = 1,
    avg_confidence: float = 0.0,
    dwell_seconds: float = 0.0,
) -> PersonTrack:
    track = PersonTrack(
        job_id=job_id,
        track_id=track_id,
        camera_id=camera_id,
        zone_id=zone_id,
        first_seen=first_seen,
        last_seen=last_seen,
        appearances=appearances,
        avg_confidence=avg_confidence,
        dwell_seconds=dwell_seconds,
    )
    session.add(track)
    await session.commit()
    await session.refresh(track)
    return track


async def get_tracks_for_job(session: AsyncSession, job_id: int) -> list[PersonTrack]:
    result = await session.execute(
        select(PersonTrack).where(PersonTrack.job_id == job_id).order_by(PersonTrack.track_id)
    )
    return list(result.scalars())


async def get_tracks_for_zone(session: AsyncSession, job_id: int, zone_id: int) -> list[PersonTrack]:
    result = await session.execute(
        select(PersonTrack)
        .where(PersonTrack.job_id == job_id)
        .where(PersonTrack.zone_id == zone_id)
        .order_by(PersonTrack.track_id)
    )
    return list(result.scalars())


async def update_person_track(
    session: AsyncSession,
    track: PersonTrack,
    last_seen: datetime | None = None,
    appearances: int | None = None,
    avg_confidence: float | None = None,
    dwell_seconds: float | None = None,
) -> PersonTrack:
    if last_seen is not None:
        track.last_seen = last_seen
    if appearances is not None:
        track.appearances = appearances
    if avg_confidence is not None:
        track.avg_confidence = avg_confidence
    if dwell_seconds is not None:
        track.dwell_seconds = dwell_seconds
    session.add(track)
    await session.commit()
    await session.refresh(track)
    return track


# Person Detection CRUD
async def create_person_detection(
    session: AsyncSession,
    track_id: int,
    frame_id: int,
    bbox_x1: float,
    bbox_y1: float,
    bbox_x2: float,
    bbox_y2: float,
    confidence: float,
    in_zone_id: int | None,
    timestamp: datetime,
) -> PersonDetection:
    detection = PersonDetection(
        track_id=track_id,
        frame_id=frame_id,
        bbox_x1=bbox_x1,
        bbox_y1=bbox_y1,
        bbox_x2=bbox_x2,
        bbox_y2=bbox_y2,
        confidence=confidence,
        in_zone_id=in_zone_id,
        timestamp=timestamp,
    )
    session.add(detection)
    await session.commit()
    await session.refresh(detection)
    return detection


async def get_detections_for_frame(session: AsyncSession, frame_id: int) -> list[PersonDetection]:
    result = await session.execute(
        select(PersonDetection).where(PersonDetection.frame_id == frame_id)
    )
    return list(result.scalars())


async def get_tracking_detections_for_job(session: AsyncSession, job_id: int) -> dict[int, list[dict]]:
    result = await session.execute(
        select(
            PersonDetection.frame_id,
            PersonDetection.bbox_x1,
            PersonDetection.bbox_y1,
            PersonDetection.bbox_x2,
            PersonDetection.bbox_y2,
            PersonDetection.confidence,
            PersonDetection.in_zone_id,
            PersonTrack.track_id,
        )
        .join(PersonTrack, PersonDetection.track_id == PersonTrack.id)
        .where(PersonTrack.job_id == job_id)
        .order_by(PersonDetection.frame_id, PersonTrack.track_id)
    )

    by_frame: dict[int, list[dict]] = {}
    for row in result.all():
        frame_id = int(row.frame_id)
        by_frame.setdefault(frame_id, []).append(
            {
                "track_id": int(row.track_id),
                "bbox_x1": float(row.bbox_x1),
                "bbox_y1": float(row.bbox_y1),
                "bbox_x2": float(row.bbox_x2),
                "bbox_y2": float(row.bbox_y2),
                "confidence": float(row.confidence),
                "zone_id": int(row.in_zone_id) if row.in_zone_id is not None else None,
            }
        )

    return by_frame


async def get_zone_stats_for_job(session: AsyncSession, job_id: int) -> list[dict]:
    """Get aggregate statistics per zone for an analysis job."""
    tracks = await get_tracks_for_job(session, job_id)
    
    zone_stats: dict[int, dict] = {}
    for track in tracks:
        if track.zone_id is None:
            zone_key = 0  # No zone
        else:
            zone_key = track.zone_id
            
        if zone_key not in zone_stats:
            zone_stats[zone_key] = {
                "zone_id": track.zone_id,
                "unique_persons": 0,
                "total_appearances": 0,
                "total_dwell_seconds": 0.0,
            }
        zone_stats[zone_key]["unique_persons"] += 1
        zone_stats[zone_key]["total_appearances"] += track.appearances
        zone_stats[zone_key]["total_dwell_seconds"] += track.dwell_seconds
    
    return list(zone_stats.values())


# Queue Management CRUD
async def create_queue_analysis_job(
    session: AsyncSession,
    camera_id: int,
    zone_id: int,
    start_time: datetime,
    end_time: datetime,
) -> QueueAnalysisJob:
    job = QueueAnalysisJob(
        camera_id=camera_id,
        zone_id=zone_id,
        start_time=start_time,
        end_time=end_time,
        status=QueueJobStatus.pending,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_queue_analysis_job(session: AsyncSession, job_id: int) -> QueueAnalysisJob | None:
    result = await session.execute(select(QueueAnalysisJob).where(QueueAnalysisJob.id == job_id))
    return result.scalar_one_or_none()


async def list_queue_analysis_jobs(
    session: AsyncSession,
    camera_id: int | None = None,
    zone_id: int | None = None,
) -> list[QueueAnalysisJob]:
    stmt = select(QueueAnalysisJob)
    if camera_id is not None:
        stmt = stmt.where(QueueAnalysisJob.camera_id == camera_id)
    if zone_id is not None:
        stmt = stmt.where(QueueAnalysisJob.zone_id == zone_id)
    result = await session.execute(stmt.order_by(QueueAnalysisJob.id.desc()))
    return list(result.scalars())


async def update_queue_analysis_job(
    session: AsyncSession,
    job: QueueAnalysisJob,
    status: QueueJobStatus | None = None,
    total_frames: int | None = None,
    processed_frames: int | None = None,
    max_queue_count: int | None = None,
    avg_queue_count: float | None = None,
    avg_wait_time_sec: float | None = None,
    avg_throughput_exits_per_sec: float | None = None,
    error: str | None = None,
) -> QueueAnalysisJob:
    if status is not None:
        job.status = status
    if total_frames is not None:
        job.total_frames = total_frames
    if processed_frames is not None:
        job.processed_frames = processed_frames
    if max_queue_count is not None:
        job.max_queue_count = max_queue_count
    if avg_queue_count is not None:
        job.avg_queue_count = avg_queue_count
    if avg_wait_time_sec is not None:
        job.avg_wait_time_sec = avg_wait_time_sec
    if avg_throughput_exits_per_sec is not None:
        job.avg_throughput_exits_per_sec = avg_throughput_exits_per_sec
    if error is not None:
        job.error = error
    
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def create_queue_frame_result(
    session: AsyncSession,
    job_id: int,
    frame_id: int,
    timestamp: datetime,
    queue_count: int,
    wait_time_sec: float | None = None,
    throughput_exits_per_sec: float | None = None,
) -> QueueFrameResult:
    result = QueueFrameResult(
        job_id=job_id,
        frame_id=frame_id,
        timestamp=timestamp,
        queue_count=queue_count,
        wait_time_sec=wait_time_sec,
        throughput_exits_per_sec=throughput_exits_per_sec,
    )
    session.add(result)
    await session.commit()
    await session.refresh(result)
    return result


async def get_queue_results_for_job(session: AsyncSession, job_id: int) -> list[QueueFrameResult]:
    result = await session.execute(
        select(QueueFrameResult)
        .where(QueueFrameResult.job_id == job_id)
        .order_by(QueueFrameResult.timestamp)
    )
    return list(result.scalars())


# Face Reference CRUD
async def create_face_reference(
    session: AsyncSession,
    name: str,
    image_path: str,
    embedding: list[float],
) -> FaceReference:
    ref = FaceReference(
        name=name,
        image_path=image_path,
        embedding=embedding,
    )
    session.add(ref)
    await session.commit()
    await session.refresh(ref)
    return ref


async def list_face_references(session: AsyncSession) -> list[FaceReference]:
    result = await session.execute(select(FaceReference).order_by(FaceReference.name))
    return list(result.scalars())


async def get_face_reference(session: AsyncSession, id: int) -> FaceReference | None:
    result = await session.execute(select(FaceReference).where(FaceReference.id == id))
    return result.scalar_one_or_none()


async def delete_face_reference(session: AsyncSession, ref: FaceReference) -> None:
    await session.delete(ref)
    await session.commit()
