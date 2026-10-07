from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Callable

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import crud
from app.db.models import Frame, FrameStatus, Stream, StreamStatus

logger = logging.getLogger(__name__)

_MIN_CAPTURE_INTERVAL_SECONDS = 1.0


@dataclass
class IngestionEvent:
    kind: str
    camera_id: int
    stream_id: int
    path: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None
    size_bytes: int | None = None
    status: FrameStatus | None = None
    message: str | None = None


@dataclass
class IngestionEntry:
    stream_id: int
    camera_id: int
    stop_event: threading.Event
    future: Future
    process_ref: "_IngestionProcessRef"


@dataclass
class _IngestionProcessRef:
    lock: threading.Lock = field(default_factory=threading.Lock)
    process: subprocess.Popen | None = None

    def get(self) -> subprocess.Popen | None:
        with self.lock:
            return self.process

    def set(self, process: subprocess.Popen | None) -> None:
        with self.lock:
            self.process = process

    def clear(self, expected: subprocess.Popen | None = None) -> None:
        with self.lock:
            if expected is None or self.process is expected:
                self.process = None


class StreamIngestionManager:
    def __init__(self, session_factory: Callable[[], AsyncSession]):
        self._session_factory = session_factory
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._lock = threading.Lock()
        self._jobs: dict[int, IngestionEntry] = {}
        self._queue: Queue[IngestionEvent | None] = Queue()
        self._running = False

    def start(self) -> None:
        self._running = True

    def shutdown(self) -> None:
        self._running = False
        with self._lock:
            entries = list(self._jobs.values())
        for entry in entries:
            entry.stop_event.set()
            process = entry.process_ref.get()
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                except Exception:
                    logger.debug("Failed to kill ffmpeg ingestion process during shutdown", exc_info=True)
        for entry in entries:
            entry.future.cancel()
        self._queue.put(None)
        self._executor.shutdown(wait=False)

    def start_ingestion(
        self,
        stream_id: int,
        camera_id: int,
        source_kind: str,
        source_value: str,
        frame_interval_seconds: float | None = None,
    ) -> bool:
        if shutil.which(settings.ffmpeg_path) is None:
            logger.error("ffmpeg binary not found: %s", settings.ffmpeg_path)
            return False
        with self._lock:
            existing = self._jobs.get(stream_id)
            if existing:
                if existing.future.done():
                    self._jobs.pop(stream_id, None)
                else:
                    return False
            stop_event = threading.Event()
            process_ref = _IngestionProcessRef()
            future = self._executor.submit(
                _run_ingestion,
                stop_event,
                self._queue,
                stream_id,
                camera_id,
                source_kind,
                source_value,
                process_ref,
                frame_interval_seconds,
            )
            self._jobs[stream_id] = IngestionEntry(
                stream_id=stream_id,
                camera_id=camera_id,
                stop_event=stop_event,
                future=future,
                process_ref=process_ref,
            )
        return True

    def stop_ingestion(self, stream_id: int) -> bool:
        return self._signal_stop(stream_id, force=False)

    def force_stop_ingestion(self, stream_id: int) -> bool:
        return self._signal_stop(stream_id, force=True)

    def _signal_stop(self, stream_id: int, *, force: bool) -> bool:
        with self._lock:
            entry = self._jobs.get(stream_id)
            if not entry:
                return False
            entry.stop_event.set()
        process = entry.process_ref.get()
        if process is not None and process.poll() is None:
            try:
                if force:
                    process.kill()
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            except Exception:
                logger.debug("Failed to signal ffmpeg ingestion process", exc_info=True)
        with self._lock:
            if entry.future.done():
                self._jobs.pop(stream_id, None)
        return True

    def is_running(self, stream_id: int) -> bool:
        with self._lock:
            entry = self._jobs.get(stream_id)
            if not entry:
                return False
            return not entry.future.done()

    async def consume_events(self) -> None:
        while self._running:
            event = await asyncio.to_thread(self._queue.get)
            if event is None:
                break
            try:
                async with self._session_factory() as session:
                    try:
                        await _handle_event(session, event)
                    except Exception:  # noqa: BLE001
                        await session.rollback()
                        # Keep consumer alive even if a single event is invalid/stale.
                        logger.exception(
                            "Failed to process ingestion event kind=%s stream_id=%s camera_id=%s",
                            event.kind,
                            event.stream_id,
                            event.camera_id,
                        )
            finally:
                self._queue.task_done()

    async def cleanup_finished(self) -> None:
        with self._lock:
            finished = [key for key, entry in self._jobs.items() if entry.future.done()]
            for key in finished:
                self._jobs.pop(key, None)


def _run_ingestion(
    stop_event: threading.Event,
    queue: Queue[IngestionEvent | None],
    stream_id: int,
    camera_id: int,
    source_kind: str,
    source_value: str,
    process_ref: _IngestionProcessRef,
    frame_interval_seconds: float | None = None,
) -> None:
    base_dir = Path(settings.frames_dir) / f"camera_{camera_id}" / f"stream_{stream_id}"
    base_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale frame files from previous runs to avoid redundant files and
    # stale-frame ingestion when stream jobs restart.
    for stale_path in base_dir.glob("*.jpg"):
        try:
            stale_path.unlink()
        except OSError:
            logger.warning("Failed to remove stale frame file: %s", stale_path)

    if frame_interval_seconds is None:
        frame_interval_seconds = settings.frame_interval_seconds

    capture_interval_seconds = max(float(frame_interval_seconds), _MIN_CAPTURE_INTERVAL_SECONDS)
    fps_value = max(0.1, 1.0 / max(capture_interval_seconds, 1e-6))
    scan_sleep_seconds = max(
        0.005,
        min(
            0.25,
            float(settings.segment_scan_seconds),
            max(capture_interval_seconds, 0.005),
        ),
    )
    emit_interval_seconds = capture_interval_seconds
    while not stop_event.is_set():
        run_prefix = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        output_pattern = str(base_dir / f"{run_prefix}_%06d.jpg")
        next_frame_index = 0
        last_emitted_monotonic = 0.0
        pending_frame_signatures: dict[int, tuple[int, int]] = {}
        try:
            cmd = _build_ffmpeg_command(source_kind, source_value, fps_value, output_pattern)
        except ValueError as exc:
            queue.put(
                IngestionEvent(
                    kind="status",
                    camera_id=camera_id,
                    stream_id=stream_id,
                    status=FrameStatus.error,
                    message=str(exc),
                )
            )
            return

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process_ref.set(process)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to start ffmpeg ingestion: %s", exc)
            queue.put(
                IngestionEvent(
                    kind="status",
                    camera_id=camera_id,
                    stream_id=stream_id,
                    status=FrameStatus.error,
                    message=str(exc),
                )
            )
            time.sleep(1.0)
            continue

        try:
            while not stop_event.is_set():
                if process.poll() is not None:
                    break

                emitted_any = False
                latest_ready: tuple[Path, datetime, int] | None = None
                while True:
                    path = base_dir / f"{run_prefix}_{next_frame_index:06d}.jpg"
                    if not path.exists():
                        break
                    stat = path.stat()
                    if stat.st_size <= 0:
                        # ffmpeg may create placeholder files before JPEG payload is fully flushed.
                        break
                    signature = (int(stat.st_size), int(stat.st_mtime_ns))
                    if pending_frame_signatures.get(next_frame_index) != signature:
                        pending_frame_signatures[next_frame_index] = signature
                        # Require one stable scan before publishing the frame so the
                        # dashboard never reads a half-written JPEG.
                        break
                    pending_frame_signatures.pop(next_frame_index, None)
                    timestamp = datetime.utcfromtimestamp(stat.st_mtime)
                    size_bytes = stat.st_size
                    latest_ready = (path, timestamp, size_bytes)
                    next_frame_index += 1

                if latest_ready is not None:
                    now_monotonic = time.monotonic()
                    if (
                        last_emitted_monotonic == 0.0
                        or (now_monotonic - last_emitted_monotonic) >= emit_interval_seconds
                    ):
                        latest_path, timestamp, size_bytes = latest_ready
                        queue.put(
                            IngestionEvent(
                                kind="frame",
                                camera_id=camera_id,
                                stream_id=stream_id,
                                path=str(latest_path),
                                start_time=timestamp,
                                end_time=timestamp,
                                duration_seconds=0.0,
                                size_bytes=size_bytes,
                                status=FrameStatus.captured,
                            )
                        )
                        last_emitted_monotonic = now_monotonic
                        emitted_any = True

                if not emitted_any:
                    time.sleep(scan_sleep_seconds)

            if stop_event.is_set():
                _terminate_process(process)
                return

            queue.put(
                IngestionEvent(
                    kind="status",
                    camera_id=camera_id,
                    stream_id=stream_id,
                    status=FrameStatus.error,
                    message="ffmpeg ingestion stopped; retrying",
                )
            )
            time.sleep(1.0)
        finally:
            process_ref.clear(process)


def _build_ffmpeg_command(
    source_kind: str,
    source_value: str,
    fps_value: float,
    output_pattern: str,
) -> list[str]:
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        settings.ffmpeg_log_level,
    ]

    if source_kind == "rtsp":
        cmd.extend(
            [
                "-rtsp_transport",
                settings.ffmpeg_rtsp_transport,
                "-i",
                source_value,
            ]
        )
    elif source_kind == "file":
        cmd.extend(["-stream_loop", "-1", "-re", "-i", source_value])
    elif source_kind == "webcam":
        if sys.platform == "darwin":
            cmd.extend(["-f", "avfoundation", "-framerate", "30", "-i", source_value])
        elif sys.platform.startswith("linux"):
            cmd.extend(["-f", "v4l2", "-framerate", "30", "-i", f"/dev/video{source_value}"])
        elif sys.platform == "win32":
            cmd.extend(["-f", "dshow", "-i", f"video={source_value}"])
        else:
            # Fallback for unknown OS
            cmd.extend(["-i", source_value])
    else:
        raise ValueError(f"Unsupported ingestion source kind: {source_kind}")

    cmd.extend(
        [
            "-vcodec",
            "mjpeg",
            "-vf",
            f"fps={fps_value}",
            "-start_number",
            "0",
            "-f",
            "image2",
            output_pattern,
        ]
    )
    return cmd


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1.0)
    except Exception:
        logger.exception("Failed to terminate ffmpeg ingestion process")


async def _handle_event(session: AsyncSession, event: IngestionEvent) -> None:
    if event.kind == "frame" and event.path:
        frame = Frame(
            camera_id=event.camera_id,
            stream_id=event.stream_id,
            path=event.path,
            timestamp=event.start_time or datetime.utcnow(),
            width=None,
            height=None,
            size_bytes=event.size_bytes or 0,
            status=FrameStatus.captured,
        )
        session.add(frame)
        await session.execute(
            update(Stream)
            .where(Stream.id == event.stream_id)
            .values(
                last_heartbeat=event.end_time or datetime.utcnow(),
                status=StreamStatus.running,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            # Duplicate paths or foreign-key races should not kill ingestion.
            await session.rollback()
        return
