from __future__ import annotations

import re
import secrets
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import crud

CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
SAFE_VIDEO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".webm",
    ".m4v",
    ".ts",
    ".mpeg",
    ".mpg",
}


def _uploads_root() -> Path:
    root = Path(settings.uploads_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _preview_root() -> Path:
    root = (Path(settings.frames_dir).resolve() / "uploaded_first_frames")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _guess_extension(file: UploadFile) -> str:
    ext = Path(file.filename or "").suffix.lower().strip()
    if ext in SAFE_VIDEO_EXTENSIONS:
        return ext

    content_type = (file.content_type or "").lower()
    if content_type in {"video/mp4", "application/mp4"}:
        return ".mp4"
    if content_type == "video/x-msvideo":
        return ".avi"
    if content_type in {"video/quicktime", "video/mov"}:
        return ".mov"
    if content_type == "video/x-matroska":
        return ".mkv"
    if content_type == "video/webm":
        return ".webm"

    return ".mp4"


def _sanitize_stem(filename: str) -> str:
    stem = Path(filename).stem.strip().lower()
    stem = re.sub(r"[^a-z0-9_-]+", "-", stem)
    stem = stem.strip("-")
    return stem[:60] or "video"


async def _store_upload(file: UploadFile, target: Path) -> int:
    size_bytes = 0
    try:
        with target.open("wb") as output:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Uploaded file exceeds limit ({MAX_UPLOAD_BYTES} bytes)",
                    )
                output.write(chunk)
    finally:
        await file.close()

    return size_bytes


def _camera_name_from_filename(filename: str) -> str:
    cleaned = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return cleaned[:200] if cleaned else "Uploaded Video"


def _uploaded_local_path(source_url: str | None) -> Path | None:
    if not source_url:
        return None

    parsed = urlparse(source_url)
    if parsed.scheme.lower() != "file":
        return None

    candidate = Path(url2pathname(parsed.path or "")).resolve()
    root = _uploads_root()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _remove_if_stale(path: Path | None, keep: Path) -> None:
    if path is None:
        return
    if path == keep:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        # Best-effort cleanup only.
        pass


def _remove_paths(paths: list[str], keep: set[Path] | None = None) -> None:
    keep_set = {path.resolve() for path in (keep or set())}
    for raw in paths:
        try:
            candidate = Path(raw).resolve()
        except Exception:
            continue
        if candidate in keep_set:
            continue
        try:
            candidate.unlink(missing_ok=True)
        except Exception:
            # Best-effort cleanup only.
            pass


def _extract_first_frame(video_path: Path, token: str) -> Path:
    preview_path = _preview_root() / f"{video_path.stem}-{token}-first.jpg"
    command = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        settings.ffmpeg_log_level,
        "-y",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(preview_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg binary not found: {settings.ffmpeg_path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to extract first frame: {exc}") from exc

    if result.returncode != 0 or not preview_path.is_file() or preview_path.stat().st_size <= 0:
        preview_path.unlink(missing_ok=True)
        detail = (result.stderr or "").strip().splitlines()[-1] if result.stderr else "ffmpeg returned non-zero status"
        raise HTTPException(status_code=500, detail=f"Failed to extract first frame from uploaded video: {detail}")

    return preview_path.resolve()


async def upload_video_source(
    session: AsyncSession,
    file: UploadFile,
    name: str | None = None,
    location: str | None = None,
    camera_id: int | None = None,
) -> dict:
    if file.filename is None or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Video filename is required")

    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Only video files are allowed")

    extension = _guess_extension(file)
    safe_stem = _sanitize_stem(file.filename)
    token = secrets.token_hex(5)

    root = _uploads_root()
    target = root / f"{safe_stem}-{token}{extension}"

    try:
        size_bytes = await _store_upload(file, target)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {exc}") from exc

    try:
        preview_path = _extract_first_frame(target.resolve(), token)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise

    source_url = target.resolve().as_uri()
    requested_name = (name or "").strip()
    camera_name = requested_name or _camera_name_from_filename(file.filename)

    old_uploaded: Path | None = None
    old_frame_paths: list[str] = []
    camera = None
    created_camera = False
    if camera_id is not None:
        camera = await crud.get_camera(session, camera_id)
        if not camera:
            preview_path.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=404, detail="Camera not found")

        old_uploaded = _uploaded_local_path(camera.rtsp_url)
        updates: dict[str, str | None] = {"rtsp_url": source_url}
        if requested_name:
            updates["name"] = requested_name
        if location is not None:
            updates["location"] = location
        camera = await crud.update_camera(
            session,
            camera,
            **updates,
        )
    else:
        camera = await crud.create_camera(
            session,
            name=camera_name,
            rtsp_url=source_url,
            location=location,
        )
        created_camera = True

    try:
        old_frame_paths = await crud.delete_frames_for_camera(session, camera.id)
        await crud.create_frame(
            session=session,
            camera_id=camera.id,
            stream_id=None,
            path=str(preview_path),
            timestamp=datetime.utcnow(),
            width=None,
            height=None,
            size_bytes=preview_path.stat().st_size,
        )
    except Exception as exc:  # noqa: BLE001
        if created_camera and camera is not None:
            try:
                await crud.delete_camera(session, camera)
            except Exception:
                pass
        preview_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to register uploaded video preview frame: {exc}") from exc

    _remove_paths(old_frame_paths, keep={preview_path, target.resolve()})
    _remove_if_stale(old_uploaded, target.resolve())

    return {
        "camera": camera,
        "source_type": "video_file",
        "file_name": file.filename,
        "size_bytes": size_bytes,
        "source_url": source_url,
    }
