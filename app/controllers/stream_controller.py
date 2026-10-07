from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import fetcher_enabled, settings
from app.core.container import ServiceContainer
from app.core.rtsp import resolve_rtsp_url
from app.db import crud
from app.db.models import StreamStatus
from app.schemas import StreamRead, StreamStartRequest, StreamStatusRead, StreamStopRequest
from app.services.core import runtime_config

_INGESTION_STOP_TIMEOUT_SECONDS = 3.0
_INGESTION_FORCE_STOP_AFTER_SECONDS = 0.75
_INGESTION_STOP_POLL_SECONDS = 0.1


async def start_stream(
    session: AsyncSession,
    services: ServiceContainer,
    payload: StreamStartRequest,
) -> StreamRead:
    config = await runtime_config.get_runtime_config(session)
    metrics = await crud.count_metrics(session)
    if metrics.get("running_streams", 0) >= config.get("max_streams", settings.max_streams):
        raise HTTPException(status_code=429, detail="Max streams limit reached")

    camera = await crud.get_camera(session, payload.camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    source_url = (camera.rtsp_url or "").strip()
    source_kind, source_value = _resolve_capture_source(source_url)

    stream = await crud.get_stream_by_camera(session, payload.camera_id)
    if not stream:
        stream = await crud.create_stream(session, payload.camera_id, None)
    elif stream.port is not None:
        # Capture workers read directly from camera source URLs and do not publish a stream port.
        stream.port = None
        session.add(stream)
        await session.commit()
        await session.refresh(stream)

    if not fetcher_enabled():
        raise HTTPException(status_code=503, detail="Capture requires fetcher role")
    ingestion_manager = services.ingestion_manager
    if not ingestion_manager:
        raise HTTPException(status_code=503, detail="Capture service is not available")

    # Always restart workers on start requests so source edits (URL/file) take effect
    # immediately and we avoid stale workers after stop/start races.
    await _wait_for_ingestion_stop(ingestion_manager, stream.id)

    started_ingestion = ingestion_manager.start_ingestion(
        stream.id,
        stream.camera_id,
        source_kind,
        source_value,
        frame_interval_seconds=config.get("frame_interval_seconds"),
    )
    if not started_ingestion:
        if ingestion_manager.is_running(stream.id):
            stream = await crud.update_stream_status(session, stream, StreamStatus.running)
            return _stream_read(stream, camera)
        stream = await crud.update_stream_status(session, stream, StreamStatus.error)
        raise HTTPException(
            status_code=503,
            detail="Failed to start ingestion worker. Stop and start capture again.",
        )

    stream = await crud.update_stream_status(session, stream, StreamStatus.running)
    return _stream_read(stream, camera)


async def stop_stream(
    session: AsyncSession,
    services: ServiceContainer,
    payload: StreamStopRequest,
) -> StreamRead:
    if not payload.camera_id and not payload.stream_id:
        raise HTTPException(status_code=400, detail="camera_id or stream_id required")

    stream = None
    if payload.stream_id:
        stream = await crud.get_stream(session, payload.stream_id)
    elif payload.camera_id:
        stream = await crud.get_stream_by_camera(session, payload.camera_id)

    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    ingestion_manager = services.ingestion_manager
    if ingestion_manager:
        ingestion_manager.stop_ingestion(stream.id)

    camera = await crud.get_camera(session, stream.camera_id)
    stream = await crud.update_stream_status(session, stream, StreamStatus.stopped)
    return _stream_read(stream, camera)


async def delete_stream_record(
    session: AsyncSession,
    services: ServiceContainer,
    stream_id: int,
) -> None:
    stream = await crud.get_stream(session, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    ingestion_manager = services.ingestion_manager
    if ingestion_manager:
        await _wait_for_ingestion_stop(ingestion_manager, stream.id)

    try:
        await crud.delete_stream(session, stream)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete stream because related records are still being written. Stop capture and retry.",
        ) from exc


async def list_streams(session: AsyncSession) -> list[StreamRead]:
    streams = await crud.list_streams(session)
    cameras = await crud.list_cameras(session)
    camera_map = {camera.id: camera for camera in cameras}
    return [_stream_read(stream, camera_map.get(stream.camera_id)) for stream in streams]


async def get_stream(session: AsyncSession, stream_id: int) -> StreamRead:
    stream = await crud.get_stream(session, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    camera = await crud.get_camera(session, stream.camera_id)
    return _stream_read(stream, camera)


async def get_stream_status(
    session: AsyncSession,
    services: ServiceContainer,
    stream_id: int,
) -> StreamStatusRead:
    stream = await crud.get_stream(session, stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    ingestion_manager = services.ingestion_manager
    running = ingestion_manager.is_running(stream_id) if ingestion_manager else False

    if stream.last_heartbeat:
        timeout = timedelta(seconds=settings.stream_heartbeat_timeout_seconds)
        running = running and (datetime.utcnow() - stream.last_heartbeat <= timeout)

    camera = await crud.get_camera(session, stream.camera_id)
    return _status_read(stream, running, camera)


def _stream_read(stream, camera=None) -> StreamRead:
    rtsp_url = None
    if camera and getattr(camera, "rtsp_url", None):
        rtsp_url = resolve_rtsp_url(camera.rtsp_url)

    model = StreamRead.model_validate(stream)
    return model.model_copy(update={"rtsp_url": rtsp_url})


def _status_read(stream, running: bool, camera=None) -> StreamStatusRead:
    rtsp_url = None
    if camera and getattr(camera, "rtsp_url", None):
        rtsp_url = resolve_rtsp_url(camera.rtsp_url)

    return StreamStatusRead(
        id=stream.id,
        status=stream.status,
        last_heartbeat=stream.last_heartbeat,
        running=running,
        rtsp_url=rtsp_url,
    )


def _resolve_capture_source(source_url: str) -> tuple[str, str]:
    if not source_url:
        raise HTTPException(status_code=400, detail="Camera source URL required for capture")

    parsed = urlparse(source_url)
    scheme = parsed.scheme.lower()
    if scheme in {"rtsp", "rtsps"}:
        return "rtsp", resolve_rtsp_url(source_url)
    if scheme == "file":
        return "file", _file_path_from_uri(source_url)
    if scheme == "webcam":
        device_id = parsed.netloc or parsed.path or "0"
        return "webcam", str(device_id)

    raise HTTPException(
        status_code=400,
        detail="Unsupported camera source URL. Use rtsp://, rtsps://, file://, or webcam://",
    )


def _file_path_from_uri(source_url: str) -> str:
    parsed = urlparse(source_url)
    if parsed.scheme.lower() != "file":
        raise HTTPException(status_code=400, detail="Invalid file source URL")

    raw_path = url2pathname(parsed.path or "")
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raw_path = f"//{parsed.netloc}{raw_path}"

    path = Path(raw_path).resolve()
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"Uploaded video file not found: {path}")
    return str(path)


async def _wait_for_ingestion_stop(ingestion_manager, stream_id: int) -> None:
    if not ingestion_manager.is_running(stream_id):
        return

    ingestion_manager.stop_ingestion(stream_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _INGESTION_STOP_TIMEOUT_SECONDS
    force_stop_after = loop.time() + _INGESTION_FORCE_STOP_AFTER_SECONDS
    forced = False
    while ingestion_manager.is_running(stream_id):
        now = loop.time()
        if not forced and now >= force_stop_after:
            ingestion_manager.force_stop_ingestion(stream_id)
            forced = True
        if now >= deadline:
            raise HTTPException(
                status_code=503,
                detail="Previous ingestion worker is still stopping. Retry in a few seconds.",
            )
        await asyncio.sleep(_INGESTION_STOP_POLL_SECONDS)
