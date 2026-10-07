from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.core.config import settings
from app.db import crud
from app.db.models import ReconstructionStatus
from app.services.recognition.facial_expression_recognition import FacialExpressionRecognitionService
from app.services.utils.zone_geometry import region_points_from_polygon

logger = logging.getLogger(__name__)


def _extract_zone_polygons(polygon_payload: Any) -> list[list[Any]]:
    if isinstance(polygon_payload, dict):
        raw_polygons = polygon_payload.get("polygons")
        if isinstance(raw_polygons, list):
            return [poly for poly in raw_polygons if isinstance(poly, list)]
        points = polygon_payload.get("points")
        if isinstance(points, list):
            return [points]
        return []

    if isinstance(polygon_payload, list):
        if polygon_payload and isinstance(polygon_payload[0], list):
            return [poly for poly in polygon_payload if isinstance(poly, list)]
        return [polygon_payload]

    return []


def _zone_filter_from_zones(zones: list[Any]) -> Any | None:
    polygons: list[list[Any]] = []
    for zone in zones:
        polygons.extend(_extract_zone_polygons(getattr(zone, "polygon", None)))

    if not polygons:
        return None

    return SimpleNamespace(polygon={"polygons": polygons})


class ReconstructionService:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._tasks: dict[int, asyncio.Task] = {}

    async def submit(
        self,
        job_id: int,
        overlay_lines: list[str] | None = None,
        analysis_job_id: int | None = None,
        draw_tracking: bool = False,
        draw_facial_expression: bool = False,
        facial_expression_zone_id: int | None = None,
        output_fps: float | None = None,
    ) -> None:
        if job_id in self._tasks:
            return
        task = asyncio.create_task(
            self._run_job(
                job_id,
                overlay_lines=overlay_lines or [],
                analysis_job_id=analysis_job_id,
                draw_tracking=draw_tracking,
                draw_facial_expression=draw_facial_expression,
                facial_expression_zone_id=facial_expression_zone_id,
                output_fps=output_fps,
            )
        )
        self._tasks[job_id] = task

    async def _run_job(
        self,
        job_id: int,
        overlay_lines: list[str],
        analysis_job_id: int | None,
        draw_tracking: bool,
        draw_facial_expression: bool,
        facial_expression_zone_id: int | None,
        output_fps: float | None,
    ) -> None:
        sanitized_overlay_lines = _sanitize_overlay_lines(overlay_lines)
        async with self._session_factory() as session:
            job = await crud.get_reconstruction_job(session, job_id)
            if not job:
                return
            await crud.update_reconstruction_job(session, job, ReconstructionStatus.running)

            tracking_by_frame_id: dict[int, list[dict]] = {}
            facial_expression_service: FacialExpressionRecognitionService | None = None
            facial_expression_zone: Any | None = None
            segments = []
            frames = await crud.get_frames_for_range(session, job.camera_id, job.start_time, job.end_time)
            frames = [frame for frame in frames if Path(frame.path).exists()]

            if draw_tracking:
                if analysis_job_id is None:
                    await crud.update_reconstruction_job(
                        session,
                        job,
                        ReconstructionStatus.failed,
                        error="Tracking overlay requested but analysis job id is missing",
                    )
                    return
                analysis_job = await crud.get_analysis_job(session, analysis_job_id)
                if not analysis_job:
                    await crud.update_reconstruction_job(
                        session,
                        job,
                        ReconstructionStatus.failed,
                        error=f"Analysis job {analysis_job_id} not found",
                    )
                    return
                if analysis_job.status.value != "completed":
                    await crud.update_reconstruction_job(
                        session,
                        job,
                        ReconstructionStatus.failed,
                        error=f"Analysis job {analysis_job_id} is not completed",
                    )
                    return
                tracking_by_frame_id = await crud.get_tracking_detections_for_job(session, analysis_job_id)
            else:
                segments = await crud.get_segments_for_range(
                    session, job.camera_id, job.start_time, job.end_time
                )
                if not segments and not frames:
                    await crud.update_reconstruction_job(
                        session,
                        job,
                        ReconstructionStatus.failed,
                        error="No segments or frames found for requested range",
                    )
                    return

            if draw_tracking and not frames:
                await crud.update_reconstruction_job(
                    session,
                    job,
                    ReconstructionStatus.failed,
                    error="No frames found for requested range",
                )
                return

            if draw_facial_expression:
                if not frames:
                    await crud.update_reconstruction_job(
                        session,
                        job,
                        ReconstructionStatus.failed,
                        error="Facial expression overlay requested but no frames were found",
                    )
                    return

                if facial_expression_zone_id is not None:
                    facial_expression_zone = await crud.get_zone(session, facial_expression_zone_id)
                    if (
                        facial_expression_zone is None
                        or int(getattr(facial_expression_zone, "camera_id", 0)) != int(job.camera_id)
                    ):
                        await crud.update_reconstruction_job(
                            session,
                            job,
                            ReconstructionStatus.failed,
                            error=f"Facial expression zone {facial_expression_zone_id} was not found for this camera",
                        )
                        return
                else:
                    camera_zones = await crud.list_zones(session, camera_id=job.camera_id)
                    facial_expression_zone = _zone_filter_from_zones(camera_zones)
                    if facial_expression_zone is None:
                        await crud.update_reconstruction_job(
                            session,
                            job,
                            ReconstructionStatus.failed,
                            error="Facial expression overlay requires at least one configured zone",
                        )
                        return

                facial_expression_service = FacialExpressionRecognitionService()
                if not facial_expression_service.is_available():
                    await crud.update_reconstruction_job(
                        session,
                        job,
                        ReconstructionStatus.failed,
                        error=facial_expression_service.error_message(),
                    )
                    return

        output_path = Path(settings.reconstructions_dir) / f"reconstruction_{job_id}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if draw_tracking or draw_facial_expression:
                await asyncio.to_thread(
                    _concat_frames_with_overlay,
                    frames=[(frame.path, frame.timestamp, frame.id) for frame in frames],
                    output=str(output_path),
                    overlay_lines=sanitized_overlay_lines,
                    frame_detections=tracking_by_frame_id,
                    facial_expression_service=facial_expression_service,
                    facial_expression_zone=facial_expression_zone,
                    output_fps=output_fps,
                )
            elif segments:
                if sanitized_overlay_lines:
                    await asyncio.to_thread(
                        _concat_segments_with_overlay,
                        segments=[s.path for s in segments],
                        output=str(output_path),
                        overlay_lines=sanitized_overlay_lines,
                        output_fps=output_fps,
                    )
                else:
                    await asyncio.to_thread(
                        _concat_segments,
                        segments=[s.path for s in segments],
                        output=str(output_path),
                        output_fps=output_fps,
                    )
            else:
                if sanitized_overlay_lines:
                    await asyncio.to_thread(
                        _concat_frames_with_overlay,
                        frames=[(frame.path, frame.timestamp, frame.id) for frame in frames],
                        output=str(output_path),
                        overlay_lines=sanitized_overlay_lines,
                        output_fps=output_fps,
                    )
                else:
                    await asyncio.to_thread(
                        _concat_frames,
                        frames=[(frame.path, frame.timestamp) for frame in frames],
                        output=str(output_path),
                        output_fps=output_fps,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Reconstruction failed: %s", exc)
            async with self._session_factory() as session:
                job = await crud.get_reconstruction_job(session, job_id)
                if job:
                    await crud.update_reconstruction_job(
                        session,
                        job,
                        ReconstructionStatus.failed,
                        error=str(exc),
                    )
            return

        async with self._session_factory() as session:
            job = await crud.get_reconstruction_job(session, job_id)
            if job:
                await crud.update_reconstruction_job(
                    session,
                    job,
                    ReconstructionStatus.completed,
                    output_path=str(output_path),
                )


def _concat_segments(segments: list[str], output: str, output_fps: float | None = None) -> None:
    list_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as handle:
            for segment in segments:
                handle.write(f"file '{_ffconcat_escape_path(segment)}'\n")
            list_path = handle.name

        target_output_fps = _normalize_output_fps(output_fps)
        cmd = [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            settings.ffmpeg_log_level,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
        ]
        if target_output_fps is None:
            cmd.extend(["-c", "copy", output])
        else:
            cmd.extend(
                [
                    "-vf",
                    _video_filter_chain(target_output_fps),
                    "-vsync",
                    "cfr",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:v",
                    "libx264",
                    "-movflags",
                    "+faststart",
                    output,
                ]
            )

        _run_ffmpeg(cmd, "FFmpeg concat failed")
    finally:
        if list_path:
            Path(list_path).unlink(missing_ok=True)


def _concat_segments_with_overlay(
    segments: list[str],
    output: str,
    overlay_lines: list[str],
    output_fps: float | None = None,
) -> None:
    cv2 = _load_cv2()
    ordered_segments = [segment for segment in segments if Path(segment).exists()]
    if not ordered_segments:
        raise RuntimeError("No segment files found on disk for reconstruction")

    raw_output_path = None
    written_frames = 0
    writer = None
    target_size: tuple[int, int] | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            raw_output_path = handle.name

        for segment in ordered_segments:
            capture = cv2.VideoCapture(segment)
            if not capture.isOpened():
                logger.warning("Skipping unreadable segment file: %s", segment)
                continue

            try:
                fps = float(capture.get(cv2.CAP_PROP_FPS))
                if fps <= 1.0 or fps > 120.0:
                    fps = _default_reconstruction_fps()

                segment_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                segment_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break

                    if writer is None:
                        if segment_width <= 0 or segment_height <= 0:
                            segment_height, segment_width = frame.shape[:2]
                        target_size = _even_dimensions(segment_width, segment_height)
                        writer = _create_cv2_writer(cv2, raw_output_path, fps, target_size)

                    if target_size is None:
                        continue
                    if frame.shape[1] != target_size[0] or frame.shape[0] != target_size[1]:
                        frame = cv2.resize(frame, target_size, interpolation=cv2.INTER_LINEAR)

                    _draw_overlay_box(cv2, frame, overlay_lines)
                    writer.write(frame)
                    written_frames += 1
            finally:
                capture.release()

        if writer is not None:
            writer.release()
            writer = None

        if written_frames == 0:
            raise RuntimeError("No segment frames were decoded for reconstruction")

        _transcode_for_playback(
            source_path=raw_output_path,
            output=output,
            error_prefix="FFmpeg segment overlay transcode failed",
            output_fps=output_fps,
        )
    finally:
        if writer is not None:
            writer.release()
        if raw_output_path:
            Path(raw_output_path).unlink(missing_ok=True)


def _concat_frames(
    frames: list[tuple[str, datetime]],
    output: str,
    output_fps: float | None = None,
) -> None:
    if not frames:
        raise RuntimeError("No frames available to reconstruct")

    ordered_frames = [(path, ts) for path, ts in frames if Path(path).exists()]
    ordered_frames.sort(key=lambda item: item[1])
    if not ordered_frames:
        raise RuntimeError("No frame files found on disk for reconstruction")

    source_fps = _estimate_fps(ordered_frames)
    target_output_fps = _normalize_output_fps(output_fps)
    min_frame_duration = 1.0 / max(source_fps, 1e-6)
    list_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as handle:
            handle.write("ffconcat version 1.0\n")
            for idx, (path, timestamp) in enumerate(ordered_frames):
                handle.write(f"file '{_ffconcat_escape_path(path)}'\n")
                if idx < len(ordered_frames) - 1:
                    next_ts = ordered_frames[idx + 1][1]
                    duration = max((next_ts - timestamp).total_seconds(), min_frame_duration)
                    handle.write(f"duration {duration}\n")
            # Concat demuxer requires the last entry again for the final duration.
            handle.write(f"file '{_ffconcat_escape_path(ordered_frames[-1][0])}'\n")
            list_path = handle.name

        cmd = [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            settings.ffmpeg_log_level,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
        ]
        if target_output_fps is not None:
            cmd.extend(
                [
                    "-vf",
                    _video_filter_chain(target_output_fps),
                    "-vsync",
                    "cfr",
                ]
            )
        else:
            cmd.extend(
                [
                    "-vf",
                    _video_filter_chain(None),
                    "-vsync",
                    "vfr",
                ]
            )
        cmd.extend(
            [
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                "-movflags",
                "+faststart",
                output,
            ]
        )

        try:
            _run_ffmpeg(cmd, "FFmpeg frame concat failed")
        except RuntimeError as exc:
            logger.warning("%s. Retrying with image-sequence fallback.", exc)
            _concat_frames_fallback(ordered_frames, output, source_fps, output_fps=target_output_fps)
    finally:
        if list_path:
            Path(list_path).unlink(missing_ok=True)


def _concat_frames_with_overlay(
    frames: list[tuple[str, datetime, int | None]],
    output: str,
    overlay_lines: list[str],
    frame_detections: dict[int, list[dict]] | None = None,
    facial_expression_service: FacialExpressionRecognitionService | None = None,
    facial_expression_zone: Any | None = None,
    output_fps: float | None = None,
) -> None:
    if not frames:
        raise RuntimeError("No frames available to reconstruct")

    cv2 = _load_cv2()
    ordered_frames = [(path, ts, frame_id) for path, ts, frame_id in frames if Path(path).exists()]
    ordered_frames.sort(key=lambda item: item[1])
    if not ordered_frames:
        raise RuntimeError("No frame files found on disk for reconstruction")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        overlay_frames: list[tuple[str, datetime]] = []
        track_trails: dict[int, list[tuple[int, int]]] = {}
        track_last_seen: dict[int, int] = {}
        facial_expression_polygons: list[list[tuple[int, int]]] | None = None
        facial_expression_polygon_dims: tuple[int, int] | None = None

        for idx, (source_path, timestamp, frame_id) in enumerate(ordered_frames, start=1):
            image = cv2.imread(source_path)
            if image is None:
                logger.warning("Skipping unreadable frame file: %s", source_path)
                continue

            if frame_detections and frame_id is not None:
                _draw_tracking_boxes(
                    cv2,
                    image,
                    frame_detections.get(frame_id, []),
                    trail_history=track_trails,
                    trail_last_seen=track_last_seen,
                    frame_index=idx,
                )

            if facial_expression_service is not None and facial_expression_service.is_available():
                frame_h, frame_w = image.shape[:2]
                polygon_dims = (frame_w, frame_h)
                if facial_expression_zone is not None and facial_expression_polygon_dims != polygon_dims:
                    facial_expression_polygons = region_points_from_polygon(
                        getattr(facial_expression_zone, "polygon", None),
                        frame_w,
                        frame_h,
                        use_default_on_invalid=False,
                    )
                    facial_expression_polygon_dims = polygon_dims
                facial_detections = facial_expression_service.detect_faces_with_demographics(
                    image,
                    zone=facial_expression_zone,
                    precomputed_polygons=facial_expression_polygons,
                )
                _draw_facial_expression_boxes(cv2, image, facial_detections)
                _draw_zone_polygons_on_frame(
                    cv2,
                    image,
                    facial_expression_zone,
                    precomputed_polygons=facial_expression_polygons,
                )

            _draw_overlay_box(cv2, image, overlay_lines)
            rendered_path = tmp_path / f"overlay_{idx:06d}.jpg"
            if not cv2.imwrite(str(rendered_path), image):
                raise RuntimeError(f"Failed to write overlay frame: {rendered_path}")
            overlay_frames.append((str(rendered_path), timestamp))

        if not overlay_frames:
            raise RuntimeError("No valid frames were available for overlay rendering")

        _concat_frames(overlay_frames, output, output_fps=output_fps)

def _concat_frames_fallback(
    frames: list[tuple[str, datetime]],
    output: str,
    source_fps: float,
    output_fps: float | None = None,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for idx, (source, _) in enumerate(frames, start=1):
            src_path = Path(source)
            dst_path = tmp_path / f"frame_{idx:06d}.jpg"
            try:
                dst_path.symlink_to(src_path)
            except OSError:
                shutil.copy2(src_path, dst_path)

        cmd = [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            settings.ffmpeg_log_level,
            "-y",
            "-framerate",
            f"{source_fps:.3f}",
            "-i",
            str(tmp_path / "frame_%06d.jpg"),
        ]
        cmd.extend(
            [
                "-vf",
                _video_filter_chain(output_fps),
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                "-movflags",
                "+faststart",
                output,
            ]
        )
        _run_ffmpeg(cmd, "FFmpeg frame fallback concat failed")

def _transcode_for_playback(
    source_path: str,
    output: str,
    error_prefix: str,
    output_fps: float | None = None,
) -> None:
    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        settings.ffmpeg_log_level,
        "-y",
        "-i",
        source_path,
    ]
    normalized_output_fps = _normalize_output_fps(output_fps)
    if normalized_output_fps is not None:
        cmd.extend(["-vf", _video_filter_chain(normalized_output_fps), "-vsync", "cfr"])
    else:
        cmd.extend(["-vf", _video_filter_chain(None)])
    cmd.extend(
        [
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            output,
        ]
    )
    _run_ffmpeg(cmd, error_prefix)


def _run_ffmpeg(cmd: list[str], error_prefix: str) -> None:
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return

    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout
    if detail:
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        tail = " | ".join(lines[-3:])
        raise RuntimeError(f"{error_prefix}: {tail}")
    raise RuntimeError(error_prefix)


def _ffconcat_escape_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace("'", "\\'")


def _load_cv2() -> Any:
    try:
        import cv2
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("OpenCV is required to draw analytics overlay in output videos") from exc
    return cv2


def _sanitize_overlay_lines(lines: list[str] | None) -> list[str]:
    if not lines:
        return []

    sanitized: list[str] = []
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        sanitized.append(text[:120])
        if len(sanitized) >= 8:
            break
    return sanitized


def _normalize_output_fps(output_fps: float | None) -> float | None:
    if output_fps is None:
        return None
    try:
        normalized = float(output_fps)
    except (TypeError, ValueError):
        return None
    if normalized <= 0:
        return None
    return min(60.0, max(1.0, normalized))


def _video_filter_chain(output_fps: float | None) -> str:
    filters = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    normalized_output_fps = _normalize_output_fps(output_fps)
    if normalized_output_fps is not None:
        filters.append(f"fps={normalized_output_fps:.3f}")
    return ",".join(filters)


def _even_dimensions(width: int, height: int) -> tuple[int, int]:
    safe_width = max(width, 2)
    safe_height = max(height, 2)
    if safe_width % 2 != 0:
        safe_width -= 1
    if safe_height % 2 != 0:
        safe_height -= 1
    return safe_width, safe_height


def _create_cv2_writer(cv2: Any, path: str, fps: float, size: tuple[int, int]) -> Any:
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        raise RuntimeError("Failed to initialize OpenCV video writer")
    return writer


def _draw_overlay_box(cv2: Any, frame: Any, overlay_lines: list[str]) -> None:
    lines = _sanitize_overlay_lines(overlay_lines)
    if not lines:
        return

    frame_height, frame_width = frame.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.5, min(1.0, frame_height / 900.0))
    thickness = 1 if frame_height < 900 else 2
    padding = max(8, int(frame_height * 0.015))
    gap = max(4, int(frame_height * 0.008))

    text_metrics: list[tuple[int, int, int]] = []
    max_width = 0
    total_height = 0
    for line in lines:
        (text_width, text_height), baseline = cv2.getTextSize(line, font, font_scale, thickness)
        text_metrics.append((text_width, text_height, baseline))
        max_width = max(max_width, text_width)
        total_height += text_height

    box_x = padding
    box_y = padding
    box_width = max_width + padding * 2
    box_height = total_height + gap * (len(lines) - 1) + padding * 2
    box_width = min(box_width, frame_width - (padding * 2))
    box_height = min(box_height, frame_height - (padding * 2))
    if box_width <= 0 or box_height <= 0:
        return

    overlay = frame.copy()
    cv2.rectangle(
        overlay,
        (box_x, box_y),
        (box_x + box_width, box_y + box_height),
        (0, 0, 0),
        -1,
    )
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0.0, frame)
    cv2.rectangle(
        frame,
        (box_x, box_y),
        (box_x + box_width, box_y + box_height),
        (235, 235, 235),
        2,
    )

    y = box_y + padding
    text_x = box_x + padding
    max_text_y = box_y + box_height - padding
    for line, (_, text_height, _) in zip(lines, text_metrics):
        y += text_height
        if y > max_text_y:
            break
        cv2.putText(frame, line, (text_x, y), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
        y += gap


def _draw_tracking_boxes(
    cv2: Any,
    frame: Any,
    detections: list[dict],
    trail_history: dict[int, list[tuple[int, int]]] | None = None,
    trail_last_seen: dict[int, int] | None = None,
    frame_index: int | None = None,
) -> None:
    if not detections:
        return

    frame_height, frame_width = frame.shape[:2]
    parsed: list[dict[str, Any]] = []
    for det in detections:
        try:
            x1 = int(round(float(det.get("bbox_x1", 0.0))))
            y1 = int(round(float(det.get("bbox_y1", 0.0))))
            x2 = int(round(float(det.get("bbox_x2", 0.0))))
            y2 = int(round(float(det.get("bbox_y2", 0.0))))
            track_id = int(det.get("track_id", -1))
            confidence = float(det.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue

        x1 = max(0, min(x1, frame_width - 1))
        y1 = max(0, min(y1, frame_height - 1))
        x2 = max(0, min(x2, frame_width - 1))
        y2 = max(0, min(y2, frame_height - 1))
        if x2 <= x1 or y2 <= y1:
            continue

        parsed.append(
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "track_id": track_id,
                "confidence": confidence,
                "zone_id": det.get("zone_id"),
            }
        )

    if trail_history is not None and trail_last_seen is not None and frame_index is not None:
        stale_before = frame_index - 120
        stale_ids = [track_id for track_id, last_seen in trail_last_seen.items() if last_seen < stale_before]
        for track_id in stale_ids:
            trail_last_seen.pop(track_id, None)
            trail_history.pop(track_id, None)

        for item in parsed:
            track_id = int(item["track_id"])
            if track_id < 0:
                continue

            foot_x = int(round((int(item["x1"]) + int(item["x2"])) / 2))
            foot_y = int(item["y2"])
            points = trail_history.setdefault(track_id, [])
            points.append((foot_x, foot_y))
            if len(points) > 90:
                del points[:-90]
            trail_last_seen[track_id] = frame_index

        for track_id, points in trail_history.items():
            if len(points) < 2:
                continue
            color = _track_color(track_id)
            for idx in range(1, len(points)):
                cv2.line(frame, points[idx - 1], points[idx], color, 2, cv2.LINE_AA)

    for item in parsed:
        x1 = int(item["x1"])
        y1 = int(item["y1"])
        x2 = int(item["x2"])
        y2 = int(item["y2"])
        track_id = int(item["track_id"])
        _confidence = float(item["confidence"])
        _zone_id = item["zone_id"]

        color = _track_color(track_id)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        label = f"ID {track_id}"
        (label_w, label_h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        label_top = max(0, y1 - label_h - baseline - 6)
        label_bottom = min(frame_height - 1, label_top + label_h + baseline + 6)
        label_right = min(frame_width - 1, x1 + label_w + 8)

        cv2.rectangle(frame, (x1, label_top), (label_right, label_bottom), color, -1)
        cv2.putText(
            frame,
            label,
            (x1 + 4, label_bottom - baseline - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


def _draw_facial_expression_boxes(cv2: Any, frame: Any, detections: list[dict[str, Any]]) -> None:
    if not detections:
        return

    frame_height, frame_width = frame.shape[:2]
    for row in detections:
        try:
            x1 = int(round(float(row.get("x1", 0))))
            y1 = int(round(float(row.get("y1", 0))))
            x2 = int(round(float(row.get("x2", 0))))
            y2 = int(round(float(row.get("y2", 0))))
            emotion = str(row.get("emotion", "")).strip() or "Unknown"
        except Exception:  # noqa: BLE001
            continue

        x1 = max(0, min(frame_width - 1, x1))
        y1 = max(0, min(frame_height - 1, y1))
        x2 = max(0, min(frame_width - 1, x2))
        y2 = max(0, min(frame_height - 1, y2))
        if x2 <= x1 or y2 <= y1:
            continue

        color = (38, 180, 92)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        label = f"{emotion}"
        (label_w, label_h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        label_top = max(0, y1 - label_h - baseline - 6)
        label_bottom = min(frame_height - 1, label_top + label_h + baseline + 6)
        label_right = min(frame_width - 1, x1 + label_w + 8)
        cv2.rectangle(frame, (x1, label_top), (label_right, label_bottom), color, -1)
        cv2.putText(
            frame,
            label,
            (x1 + 4, label_bottom - baseline - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


def _track_color(track_id: int) -> tuple[int, int, int]:
    if track_id < 0:
        return (0, 255, 0)
    return (
        int((37 * track_id + 17) % 255),
        int((17 * track_id + 113) % 255),
        int((29 * track_id + 53) % 255),
    )


def _estimate_fps(frames: list[tuple[str, datetime]]) -> float:
    default_fps = _default_reconstruction_fps()
    if len(frames) < 2:
        return default_fps

    deltas: list[float] = []
    for idx in range(len(frames) - 1):
        delta = (frames[idx + 1][1] - frames[idx][1]).total_seconds()
        if delta > 1e-6:
            deltas.append(delta)

    if not deltas:
        return default_fps

    sorted_deltas = sorted(deltas)
    mid = len(sorted_deltas) // 2
    if len(sorted_deltas) % 2 == 0:
        median_delta = (sorted_deltas[mid - 1] + sorted_deltas[mid]) / 2.0
    else:
        median_delta = sorted_deltas[mid]

    # Clamp timestamp jitter/outliers but preserve high-FPS streams.
    clamped_delta = min(max(median_delta, 1.0 / 120.0), 1.0)
    fps = 1.0 / clamped_delta
    return min(60.0, max(1.0, fps))


def _default_reconstruction_fps() -> float:
    interval = max(1e-6, float(settings.frame_interval_seconds))
    fps = 1.0 / interval
    return min(60.0, max(1.0, fps))


def _draw_zone_polygons_on_frame(
    cv2: Any,
    frame: Any,
    zone: Any,
    precomputed_polygons: list[list[tuple[int, int]]] | None = None,
) -> None:
    """Draw zone polygon boundary/boundaries onto a video frame for facial expression mode."""
    if zone is None:
        return
    try:
        from app.services.utils.zone_geometry import region_points_from_polygon
        import numpy as np

        frame_h, frame_w = frame.shape[:2]
        if frame_w <= 0 or frame_h <= 0:
            return

        polygons = precomputed_polygons
        if polygons is None:
            polygons = region_points_from_polygon(
                getattr(zone, "polygon", None),
                frame_w,
                frame_h,
                use_default_on_invalid=False,
            )
        if not polygons:
            return

        thickness = max(2, int(round(min(frame_w, frame_h) / 540)))
        for poly in polygons:
            pts = np.array(poly, dtype=np.int32).reshape((-1, 1, 2))
            # Semi-transparent teal fill
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pts], (166, 184, 20))
            cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
            # Solid teal zone boundary
            cv2.polylines(frame, [pts], isClosed=True, color=(166, 184, 20), thickness=thickness, lineType=cv2.LINE_AA)

    except Exception as exc:  # noqa: BLE001
        logger.debug("Zone polygon draw on frame failed: %s", exc)
