from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse
from urllib.request import url2pathname

from fastapi import FastAPI

from app.core.config import ensure_data_dir, fetcher_enabled
from app.core.container import ServiceContainer, build_service_container
from app.core.logging import configure_logging
from app.core.rtsp import resolve_rtsp_url
from app.db import crud
from app.db.models import StreamStatus
from app.db.session import AsyncSessionLocal, init_db
from app.services.core import runtime_config

logger = logging.getLogger(__name__)


async def _cleanup_loop(services: ServiceContainer) -> None:
    while True:
        await asyncio.sleep(2.0)
        ingestion_manager = services.ingestion_manager
        if ingestion_manager:
            await ingestion_manager.cleanup_finished()
            await _resume_running_streams(services)


async def _retention_loop() -> None:
    while True:
        await asyncio.sleep(60.0)
        async with AsyncSessionLocal() as session:
            config_values = await runtime_config.get_runtime_config(session)
            retention_days = config_values.get("retention_days", 7)
            cutoff = datetime.utcnow() - timedelta(days=retention_days)
            frames = await crud.list_frames_older_than(session, cutoff)
            for frame in frames:
                try:
                    Path(frame.path).unlink(missing_ok=True)
                except Exception:
                    pass
            if frames:
                await crud.delete_old_frames(session, cutoff)


def _resolve_capture_source_for_resume(source_url: str) -> tuple[str, str] | None:
    value = source_url.strip()
    if not value:
        return None

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme in {"rtsp", "rtsps"}:
        return "rtsp", resolve_rtsp_url(value)
    if scheme == "webcam":
        return "webcam", str(parsed.netloc or "0")
    if scheme == "file":
        raw_path = url2pathname(parsed.path or "")
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            raw_path = f"//{parsed.netloc}{raw_path}"
        path = Path(raw_path).resolve()
        if path.is_file():
            return "file", str(path)
    return None


async def _resume_running_streams(services: ServiceContainer) -> None:
    ingestion_manager = services.ingestion_manager
    if not ingestion_manager:
        return

    async with AsyncSessionLocal() as session:
        streams = await crud.list_streams(session)
        for stream in streams:
            if stream.status not in {StreamStatus.running, StreamStatus.starting}:
                continue
            if ingestion_manager.is_running(stream.id):
                continue

            camera = await crud.get_camera(session, stream.camera_id)
            source_url = (camera.rtsp_url or "").strip() if camera else ""
            resolved = _resolve_capture_source_for_resume(source_url)
            if resolved is None:
                await crud.update_stream_status(session, stream, StreamStatus.error)
                logger.warning(
                    "Unable to resume stream %s for camera %s: invalid or missing source URL",
                    stream.id,
                    stream.camera_id,
                )
                continue

            source_kind, source_value = resolved
            started = ingestion_manager.start_ingestion(
                stream.id,
                stream.camera_id,
                source_kind,
                source_value,
            )
            if started or ingestion_manager.is_running(stream.id):
                await crud.update_stream_status(session, stream, StreamStatus.running)
            else:
                await crud.update_stream_status(session, stream, StreamStatus.error)
                logger.warning(
                    "Unable to resume ingestion worker for stream %s (camera %s)",
                    stream.id,
                    stream.camera_id,
                )


def create_lifespan():
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        ensure_data_dir()
        await init_db()

        services = build_service_container(AsyncSessionLocal)
        app.state.services = services

        if services.ingestion_manager:
            services.ingestion_manager.start()
            await _resume_running_streams(services)
        if services.analytics_engine:
            services.analytics_engine.start()

        tasks = []
        if services.ingestion_manager:
            tasks.append(asyncio.create_task(_cleanup_loop(services)))
            tasks.append(asyncio.create_task(services.ingestion_manager.consume_events()))
        if services.analytics_engine:
            tasks.append(asyncio.create_task(services.analytics_engine.run()))
        if fetcher_enabled():
            tasks.append(asyncio.create_task(_retention_loop()))

        try:
            yield
        finally:
            if services.ingestion_manager:
                services.ingestion_manager.shutdown()
            if services.analytics_engine:
                services.analytics_engine.stop()
            for task in tasks:
                task.cancel()
            # Mark all running/starting streams as stopped so they are not
            # auto-resumed on next startup with stale status.
            try:
                async with AsyncSessionLocal() as session:
                    streams = await crud.list_streams(session)
                    for stream in streams:
                        if stream.status in {StreamStatus.running, StreamStatus.starting}:
                            await crud.update_stream_status(session, stream, StreamStatus.stopped)
            except Exception:
                pass

    return lifespan
