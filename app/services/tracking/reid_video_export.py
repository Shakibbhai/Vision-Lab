"""Render an uploaded video with the live person Re-ID overlay into a downloadable MP4."""

from __future__ import annotations

import hashlib
import json
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
_INDEX_FILE = "reid_exports.json"


@dataclass(slots=True)
class ReidVideoJob:
    id: str
    camera_id: int
    zone_id: int | None
    cache_key: str = ""
    signature: str = ""
    status: str = "queued"  # queued | running | completed | failed
    progress: float = 0.0
    unique_persons: int = 0
    output_path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


class ReidVideoExportService:
    """Runs one export at a time on its own overlay instance, so exports never touch live identities.

    Finished videos are kept on disk and indexed by camera + zone together with a signature of the source
    file and the Re-ID settings, so the same request is served from disk instead of re-running the GPU.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ReidVideoJob] = {}
        self._jobs_lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._renderer: Any = None

    def start(self, camera_id: int, video_path: Path, zone: Any | None, force: bool = False) -> ReidVideoJob:
        """Return the saved or in-progress result for this source and settings, or start rendering one."""
        cache_key, signature = _cache_identity(camera_id, video_path, zone)
        existing = self.latest(camera_id, video_path, zone)
        if existing and (existing.status in ("queued", "running") or not force):
            return existing

        job = ReidVideoJob(id=uuid.uuid4().hex[:12], camera_id=camera_id, zone_id=getattr(zone, "id", None),
                           cache_key=cache_key, signature=signature)
        self._remember(job)
        threading.Thread(target=self._run, args=(job, video_path, zone), daemon=True).start()
        return job

    def latest(self, camera_id: int, video_path: Path, zone: Any | None) -> ReidVideoJob | None:
        """In-progress or saved, still-valid result for this source, zone and the current Re-ID settings."""
        cache_key, signature = _cache_identity(camera_id, video_path, zone)
        with self._jobs_lock:
            running = [j for j in self._jobs.values()
                       if j.cache_key == cache_key and j.signature == signature and j.status in ("queued", "running")]
        if running:
            return running[-1]

        entry = _read_index().get(cache_key)
        if not entry or entry.get("signature") != signature or not Path(entry.get("output_path", "")).exists():
            return None
        job = ReidVideoJob(id=entry["job_id"], camera_id=camera_id, zone_id=getattr(zone, "id", None),
                           cache_key=cache_key, signature=signature, status="completed", progress=1.0,
                           unique_persons=int(entry.get("unique_persons", 0)), output_path=entry["output_path"])
        self._remember(job)
        return job

    def get(self, job_id: str) -> ReidVideoJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def _remember(self, job: ReidVideoJob) -> None:
        with self._jobs_lock:
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.created_at)[:-_MAX_KEPT_JOBS]:
                self._jobs.pop(old.id, None)

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
                _save_to_index(job)
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


def _cache_identity(camera_id: int, video_path: Path, zone: Any | None) -> tuple[str, str]:
    """Key per camera + zone; signature changes when the video file or any Re-ID setting changes."""
    zone_id = getattr(zone, "id", None)
    stat = video_path.stat() if video_path.exists() else None
    signature = json.dumps({
        "source": str(video_path),
        "size": stat.st_size if stat else None,
        "mtime": stat.st_mtime_ns if stat else None,
        "zone": getattr(zone, "polygon", None),
        "weights": settings.reid_overlay_weights or settings.boxmot_reid_weights,
        "personvit": settings.reid_overlay_use_personvit,
        "threshold": settings.reid_match_threshold,
        "yolo": settings.yolo_model_path,
    }, sort_keys=True, default=str)
    return f"{camera_id}:{zone_id if zone_id is not None else 'all'}", hashlib.sha1(signature.encode()).hexdigest()


def _index_path() -> Path:
    return Path(settings.reconstructions_dir) / _INDEX_FILE


def _read_index() -> dict[str, Any]:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_to_index(job: ReidVideoJob) -> None:
    index = _read_index()
    previous = index.get(job.cache_key)
    if previous and previous.get("output_path") != job.output_path:
        Path(previous["output_path"]).unlink(missing_ok=True)  # keep one saved video per camera + zone
    index[job.cache_key] = {
        "job_id": job.id,
        "signature": job.signature,
        "output_path": job.output_path,
        "unique_persons": job.unique_persons,
        "created_at": job.created_at,
    }
    path = _index_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
    tmp.replace(path)


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
