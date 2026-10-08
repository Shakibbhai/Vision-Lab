"""Render an uploaded video with the live person Re-ID overlay into a downloadable MP4."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_OUTPUT_FPS_MAX = 10.0
_OUTPUT_MAX_WIDTH = 1280
_MAX_KEPT_JOBS = 20


@dataclass(slots=True)
class ReidVideoJob:
    id: str
    camera_id: int
    zone_id: int | None
    status: str = "queued"  # queued | running | completed | failed
    progress: float = 0.0
    unique_persons: int = 0
    output_path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


class ReidVideoExportService:
    """Runs one export at a time on its own overlay instance, so exports never touch live identities."""

    def __init__(self) -> None:
        self._jobs: dict[str, ReidVideoJob] = {}
        self._jobs_lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._renderer: Any = None

    def start(self, camera_id: int, video_path: Path, zone: Any | None) -> ReidVideoJob:
        job = ReidVideoJob(id=uuid.uuid4().hex[:12], camera_id=camera_id, zone_id=getattr(zone, "id", None))
        with self._jobs_lock:
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.created_at)[:-_MAX_KEPT_JOBS]:
                self._jobs.pop(old.id, None)
        threading.Thread(target=self._run, args=(job, video_path, zone), daemon=True).start()
        return job

    def get(self, job_id: str) -> ReidVideoJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def _get_renderer(self) -> Any:
        if self._renderer is None:
            from app.services.tracking.person_reid_overlay import PersonReidOverlayService

            self._renderer = PersonReidOverlayService()
        return self._renderer

    def _run(self, job: ReidVideoJob, video_path: Path, zone: Any | None) -> None:
        with self._render_lock:
            job.status = "running"
            try:
                self._render(job, video_path, zone)
                job.status = "completed"
            except Exception as exc:  # noqa: BLE001
                logger.exception("Re-ID video export %s failed", job.id)
                job.status = "failed"
                job.error = str(exc)

    def _render(self, job: ReidVideoJob, video_path: Path, zone: Any | None) -> None:
        import cv2  # type: ignore

        renderer = self._get_renderer()
        if not renderer.is_available():
            raise RuntimeError(renderer.error_message())
        renderer.reset()

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError(f"Cannot open video {video_path.name}")
        source_fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, round(source_fps / _OUTPUT_FPS_MAX))

        output_dir = Path(settings.reconstructions_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"reid_camera{job.camera_id}_{job.id}.mp4"

        encoder = None
        size = (0, 0)
        index = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                index += 1
                if (index - 1) % step:
                    continue
                # Video time, not wall time: box hold and track memory behave as when watched live
                result = renderer.render_overlay(job.camera_id, f"export-{job.id}-{index}", frame, zone=zone,
                                                 now=index / source_fps)
                rendered = result["frame"]
                if encoder is None:
                    encoder, size = _open_encoder(output_path, rendered, source_fps / step)
                if (rendered.shape[1], rendered.shape[0]) != size:
                    rendered = cv2.resize(rendered, size, interpolation=cv2.INTER_AREA)
                encoder.stdin.write(rendered.tobytes())
                if total_frames:
                    job.progress = min(0.99, index / total_frames)
        finally:
            capture.release()
            return_code = None
            if encoder is not None:
                encoder.stdin.close()
                return_code = encoder.wait()

        if encoder is None:
            raise RuntimeError("No frames could be read from the video")
        if return_code != 0:
            raise RuntimeError("ffmpeg failed to encode the output video")
        job.unique_persons = renderer.identity_count()
        job.output_path = str(output_path)
        job.progress = 1.0


def _open_encoder(output_path: Path, frame: Any, fps: float) -> tuple[subprocess.Popen, tuple[int, int]]:
    """Pipe raw BGR frames into ffmpeg as browser-playable H.264 (downscaled to at most 1280 px wide)."""
    height, width = frame.shape[:2]
    if width > _OUTPUT_MAX_WIDTH:
        height = round(height * _OUTPUT_MAX_WIDTH / width)
        width = _OUTPUT_MAX_WIDTH
    size = (width - width % 2, height - height % 2)
    command = [
        settings.ffmpeg_path, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{size[0]}x{size[1]}", "-r", f"{fps:.3f}", "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]
    return subprocess.Popen(command, stdin=subprocess.PIPE), size


_reid_video_export_service: ReidVideoExportService | None = None


def get_reid_video_export_service() -> ReidVideoExportService:
    global _reid_video_export_service
    if _reid_video_export_service is None:
        _reid_video_export_service = ReidVideoExportService()
    return _reid_video_export_service
