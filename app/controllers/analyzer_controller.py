from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.stream_controller import _file_path_from_uri
from app.core.config import fetcher_enabled
from app.db import crud
from app.db.models import AnalysisJobStatus
from app.db.session import AsyncSessionLocal
from app.models.analyzer import (
    AnalysisJobResponse,
    AnalysisRequest,
    FacialExpressionRecognitionRequest,
    TotalPersonDetectionRequest,
    FaceRecognitionRequest,
    ReidVideoJobResponse,
    ReidVideoRequest,
)
from app.services.recognition.facial_expression_recognition import FacialExpressionRecognitionService
from app.services.tracking.person_tracking import PersonTrackingService
from app.services.tracking.reid_video_export import get_reid_video_export_service
from app.services.detection.total_person_detection import TotalPersonDetectionService
from app.services.recognition.face_recognition import FaceRecognitionService

_tracking_service: PersonTrackingService | None = None
_total_person_service: TotalPersonDetectionService | None = None
_FACIAL_EXPRESSION_RETRY_SECONDS = 30.0
_facial_expression_service: FacialExpressionRecognitionService | None = None
_facial_expression_retry_after_monotonic = 0.0
logger = logging.getLogger(__name__)


def get_tracking_service() -> PersonTrackingService:
    global _tracking_service
    if _tracking_service is None:
        _tracking_service = PersonTrackingService()
    return _tracking_service


def get_total_person_service() -> TotalPersonDetectionService:
    global _total_person_service
    if _total_person_service is None:
        _total_person_service = TotalPersonDetectionService()
    return _total_person_service


def get_facial_expression_service() -> FacialExpressionRecognitionService:
    global _facial_expression_service, _facial_expression_retry_after_monotonic

    now = time.monotonic()
    should_initialize = _facial_expression_service is None
    should_retry = (
        _facial_expression_service is not None
        and not _facial_expression_service.is_available()
        and now >= _facial_expression_retry_after_monotonic
    )
    if should_initialize or should_retry:
        _facial_expression_service = FacialExpressionRecognitionService()
        if not _facial_expression_service.is_available():
            _facial_expression_retry_after_monotonic = now + _FACIAL_EXPRESSION_RETRY_SECONDS
            logger.warning(
                "Facial expression analyzer unavailable; will retry initialization in %.0f seconds",
                _FACIAL_EXPRESSION_RETRY_SECONDS,
            )
        else:
            _facial_expression_retry_after_monotonic = 0.0
    return _facial_expression_service


_face_recognition_service: FaceRecognitionService | None = None
_face_recognition_retry_after_monotonic = 0.0

def get_face_recognition_service() -> FaceRecognitionService:
    global _face_recognition_service, _face_recognition_retry_after_monotonic

    now = time.monotonic()
    should_initialize = _face_recognition_service is None
    should_retry = (
        _face_recognition_service is not None
        and not _face_recognition_service.is_available()
        and now >= _face_recognition_retry_after_monotonic
    )
    if should_initialize or should_retry:
        _face_recognition_service = FaceRecognitionService()
        if not _face_recognition_service.is_available():
            _face_recognition_retry_after_monotonic = now + 30.0
            logger.warning("Face recognition analyzer unavailable; will retry initialization in 30 seconds")
        else:
            _face_recognition_retry_after_monotonic = 0.0
    return _face_recognition_service


def _parse_datetime(value: str) -> datetime:
    cleaned = value.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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


async def get_analyzer_status():
    service = get_tracking_service()
    return {
        "available": service.is_available(),
        "fetcher_enabled": fetcher_enabled(),
    }


async def list_cameras_for_analysis(session: AsyncSession):
    cameras = await crud.list_cameras(session)
    result = []
    for camera in cameras:
        stream = await crud.get_stream_by_camera(session, camera.id)
        zones = await crud.list_zones(session, camera_id=camera.id)
        first_frame = await crud.get_first_frame(session, camera.id)
        latest_frame = await crud.get_latest_frame(session, camera.id)

        result.append(
            {
                "id": camera.id,
                "name": camera.name,
                "location": camera.location,
                "rtsp_url": camera.rtsp_url,
                "stream_status": stream.status.value if stream else None,
                "stream_port": stream.port if stream else None,
                "zone_count": len(zones),
                "first_frame_time": first_frame.timestamp.isoformat() if first_frame else None,
                "latest_frame_time": latest_frame.timestamp.isoformat() if latest_frame else None,
            }
        )
    return result


async def list_zones_for_camera(session: AsyncSession, camera_id: int):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    zones = await crud.list_zones(session, camera_id=camera_id)
    return [
        {
            "id": zone.id,
            "name": zone.name,
            "polygon": zone.polygon,
            "created_at": zone.created_at.isoformat(),
        }
        for zone in zones
    ]


async def start_analysis(
    session: AsyncSession,
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
):
    if not fetcher_enabled():
        raise HTTPException(status_code=503, detail="Analyzer only available on fetcher service")

    camera = await crud.get_camera(session, request.camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    try:
        start_dt = _parse_datetime(request.start)
        end_dt = _parse_datetime(request.end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {exc}") from exc
    start_query_dt = _to_utc_naive(start_dt)
    end_query_dt = _to_utc_naive(end_dt)

    if end_query_dt < start_query_dt:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start")

    fallback_used = False
    frames = await crud.get_frames_for_range(session, request.camera_id, start_query_dt, end_query_dt)
    if not frames:
        latest_frame = await crud.get_latest_frame(session, request.camera_id)
        if latest_frame is not None:
            fallback_end = latest_frame.timestamp
            fallback_start = fallback_end - timedelta(minutes=30)
            fallback_frames = await crud.get_frames_for_range(
                session,
                request.camera_id,
                fallback_start,
                fallback_end,
            )
            if fallback_frames:
                frames = fallback_frames
                start_query_dt = fallback_frames[0].timestamp
                end_query_dt = fallback_frames[-1].timestamp
                fallback_used = True

    if not frames:
        raise HTTPException(status_code=404, detail="No frames found for the specified time range")

    zones = await crud.list_zones(session, camera_id=request.camera_id)
    if request.zone_id is not None:
        zones = [zone for zone in zones if int(zone.id) == int(request.zone_id)]
        if not zones:
            raise HTTPException(status_code=404, detail="Requested zone was not found for this camera")

    service = get_tracking_service()
    if not service.is_available():
        raise HTTPException(status_code=503, detail=service.error_message())

    job = await crud.create_analysis_job(session, request.camera_id, start_query_dt, end_query_dt)

    async def _run_background_job() -> None:
        async with AsyncSessionLocal() as bg_session:
            bg_frames = await crud.get_frames_for_range(
                bg_session,
                request.camera_id,
                start_query_dt,
                end_query_dt,
            )
            bg_zones = await crud.list_zones(bg_session, camera_id=request.camera_id)
            if request.zone_id is not None:
                bg_zones = [zone for zone in bg_zones if int(zone.id) == int(request.zone_id)]

            if not bg_frames:
                bg_job = await crud.get_analysis_job(bg_session, job.id)
                if bg_job is not None:
                    await crud.update_analysis_job(
                        bg_session,
                        bg_job,
                        status=AnalysisJobStatus.failed,
                        total_frames=0,
                        processed_frames=0,
                        unique_persons=0,
                        error="No frames found for the specified time range",
                    )
                return

            await service.run_analysis(
                bg_session,
                job_id=job.id,
                camera_id=request.camera_id,
                frames=bg_frames,
                zones=bg_zones,
            )

    background_tasks.add_task(_run_background_job)
    return {
        "job_id": job.id,
        "status": job.status.value,
        "message": "Analysis job queued using latest available frames" if fallback_used else "Analysis job queued",
    }


async def detect_total_persons(
    session: AsyncSession,
    request: TotalPersonDetectionRequest,
):
    if not fetcher_enabled():
        raise HTTPException(status_code=503, detail="Total person detection available only on fetcher service")

    camera = await crud.get_camera(session, request.camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    try:
        start_dt = _parse_datetime(request.start)
        end_dt = _parse_datetime(request.end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {exc}") from exc

    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start")

    frames = await crud.get_frames_for_range(session, request.camera_id, start_dt, end_dt)
    if not frames:
        raise HTTPException(status_code=404, detail="No frames found for the specified time range")

    service = get_total_person_service()
    if not service.is_available():
        raise HTTPException(status_code=503, detail="YOLO detector is unavailable for total person detection")

    result = await asyncio.to_thread(service.analyze_frames, frames)
    return {
        "camera_id": request.camera_id,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "detector_available": bool(result.get("available", False)),
        "processed_frames": int(result.get("processed_frames", 0)),
        "total_person_detections": int(result.get("total_person_detections", 0)),
        "max_person_count": int(result.get("max_person_count", 0)),
        "avg_person_count": float(result.get("avg_person_count", 0.0)),
        "series": result.get("series", []),
    }


async def detect_facial_expressions(
    session: AsyncSession,
    request: FacialExpressionRecognitionRequest,
):
    if not fetcher_enabled():
        raise HTTPException(status_code=503, detail="Facial expression recognition available only on fetcher service")

    camera = await crud.get_camera(session, request.camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    zones_for_camera = await crud.list_zones(session, camera_id=request.camera_id)
    zone = None
    zone_name: str | None = None
    if request.zone_id is not None:
        zone = await crud.get_zone(session, request.zone_id)
        if not zone or int(zone.camera_id) != int(request.camera_id):
            raise HTTPException(status_code=404, detail="Zone not found for this camera")
        zone_name = zone.name
    else:
        zone = _zone_filter_from_zones(zones_for_camera)
        if zone is None:
            raise HTTPException(
                status_code=400,
                detail="No configured zone polygons found for this camera",
            )
        zone_name = "All Zones"

    try:
        start_dt = _parse_datetime(request.start)
        end_dt = _parse_datetime(request.end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {exc}") from exc

    start_query_dt = _to_utc_naive(start_dt)
    end_query_dt = _to_utc_naive(end_dt)
    if end_query_dt < start_query_dt:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start")

    frames = await crud.get_frames_for_range(session, request.camera_id, start_query_dt, end_query_dt)
    if not frames:
        raise HTTPException(status_code=404, detail="No frames found for the specified time range")

    service = get_facial_expression_service()
    if not service.is_available():
        raise HTTPException(status_code=503, detail=service.error_message())

    result = await asyncio.to_thread(service.analyze_frames, frames, zone)
    return {
        "camera_id": request.camera_id,
        "zone_id": request.zone_id,
        "zone_name": zone_name,
        "start_time": start_query_dt.isoformat(),
        "end_time": end_query_dt.isoformat(),
        "detector_available": True,
        "processed_frames": int(result.get("processed_frames", 0)),
        "analyzed_faces": int(result.get("analyzed_faces", 0)),
        "dominant_emotion": result.get("dominant_emotion"),
        "emotion_counts": result.get("emotion_counts", {}),
        "emotion_percentages": result.get("emotion_percentages", {}),
        "avg_age": result.get("avg_age"),
        "gender_counts": result.get("gender_counts", {}),
        "age_distribution": result.get("age_distribution", {}),
        "series": result.get("series", []),
    }


async def detect_face_recognition(
    session: AsyncSession,
    request: FaceRecognitionRequest,
):
    if not fetcher_enabled():
        raise HTTPException(status_code=503, detail="Face recognition available only on fetcher service")

    camera = await crud.get_camera(session, request.camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    zones_for_camera = await crud.list_zones(session, camera_id=request.camera_id)
    zone = None
    zone_name: str | None = None
    if request.zone_id is not None:
        zone = await crud.get_zone(session, request.zone_id)
        if not zone or int(zone.camera_id) != int(request.camera_id):
            raise HTTPException(status_code=404, detail="Zone not found for this camera")
        zone_name = zone.name
    else:
        zone = _zone_filter_from_zones(zones_for_camera)
        if zone is None:
            raise HTTPException(
                status_code=400,
                detail="No configured zone polygons found for this camera",
            )
        zone_name = "All Zones"

    try:
        start_dt = _parse_datetime(request.start)
        end_dt = _parse_datetime(request.end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {exc}") from exc

    start_query_dt = _to_utc_naive(start_dt)
    end_query_dt = _to_utc_naive(end_dt)
    if end_query_dt < start_query_dt:
        raise HTTPException(status_code=400, detail="end must be greater than or equal to start")

    frames = await crud.get_frames_for_range(session, request.camera_id, start_query_dt, end_query_dt)
    if not frames:
        raise HTTPException(status_code=404, detail="No frames found for the specified time range")

    service = get_face_recognition_service()
    if not service.is_available():
        raise HTTPException(status_code=503, detail=service.error_message())

    result = await asyncio.to_thread(service.analyze_frames, frames, zone)
    return {
        "camera_id": request.camera_id,
        "zone_id": request.zone_id,
        "zone_name": zone_name,
        "start_time": start_query_dt.isoformat(),
        "end_time": end_query_dt.isoformat(),
        "detector_available": True,
        "processed_frames": int(result.get("processed_frames", 0)),
        "analyzed_faces": int(result.get("analyzed_faces", 0)),
        "recognized_persons_counts": result.get("recognized_persons_counts", {}),
        "series": result.get("series", []),
    }



async def list_analysis_jobs(session: AsyncSession, camera_id: int | None):
    jobs = await crud.list_analysis_jobs(session, camera_id=camera_id)
    return [_job_to_response(job) for job in jobs]


async def get_analysis_job(session: AsyncSession, job_id: int):
    job = await crud.get_analysis_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_response(job)


async def get_analysis_stats(session: AsyncSession, job_id: int):
    job = await crud.get_analysis_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    zone_stats = await crud.get_zone_stats_for_job(session, job_id)

    zones = await crud.list_zones(session, camera_id=job.camera_id)
    zone_map = {zone.id: zone.name for zone in zones}

    enhanced_stats = []
    for stat in zone_stats:
        zone_id = stat.get("zone_id")
        enhanced_stats.append(
            {
                "zone_id": zone_id,
                "zone_name": zone_map.get(zone_id) if zone_id else "Outside zones",
                "unique_persons": stat["unique_persons"],
                "total_appearances": stat["total_appearances"],
                "total_dwell_seconds": stat["total_dwell_seconds"],
            }
        )

    return {
        "job_id": job.id,
        "camera_id": job.camera_id,
        "status": job.status.value,
        "unique_persons": job.unique_persons,
        "zone_stats": enhanced_stats,
        "start_time": job.start_time.isoformat(),
        "end_time": job.end_time.isoformat(),
    }


async def get_person_tracks(session: AsyncSession, job_id: int, zone_id: int | None):
    job = await crud.get_analysis_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if zone_id is not None:
        tracks = await crud.get_tracks_for_zone(session, job_id, zone_id)
    else:
        tracks = await crud.get_tracks_for_job(session, job_id)

    return [
        {
            "id": track.id,
            "track_id": track.track_id,
            "zone_id": track.zone_id,
            "first_seen": track.first_seen.isoformat(),
            "last_seen": track.last_seen.isoformat(),
            "appearances": track.appearances,
            "avg_confidence": track.avg_confidence,
            "dwell_seconds": track.dwell_seconds,
        }
        for track in tracks
    ]


def _job_to_response(job) -> AnalysisJobResponse:
    return AnalysisJobResponse(
        id=job.id,
        camera_id=job.camera_id,
        start_time=job.start_time.isoformat(),
        end_time=job.end_time.isoformat(),
        status=job.status.value,
        total_frames=job.total_frames,
        processed_frames=job.processed_frames,
        unique_persons=job.unique_persons,
        error=job.error,
        created_at=job.created_at.isoformat(),
    )


async def start_reid_video(session: AsyncSession, request: ReidVideoRequest) -> ReidVideoJobResponse:
    camera_id, video_path, zone = await _reid_video_source(session, request.camera_id, request.zone_id)
    job = get_reid_video_export_service().start(camera_id, video_path, zone, force=request.force)
    return _reid_video_response(job)


async def get_latest_reid_video(session: AsyncSession, camera_id: int, zone_id: int | None) -> ReidVideoJobResponse:
    camera_id, video_path, zone = await _reid_video_source(session, camera_id, zone_id)
    job = get_reid_video_export_service().latest(camera_id, video_path, zone)
    if not job:
        raise HTTPException(status_code=404, detail="No Re-ID video for this source and settings yet")
    return _reid_video_response(job)


async def _reid_video_source(session: AsyncSession, camera_id: int, zone_id: int | None):
    camera = await crud.get_camera(session, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    if not (camera.rtsp_url or "").startswith("file://"):
        raise HTTPException(status_code=400, detail="Re-ID video export is available for uploaded video sources only")
    video_path = Path(_file_path_from_uri(camera.rtsp_url))

    zone = None
    if zone_id is not None:
        zone = await crud.get_zone(session, zone_id)
        if not zone or zone.camera_id != camera.id:
            raise HTTPException(status_code=404, detail="Zone not found for this camera")
    return camera.id, video_path, zone


async def get_reid_video(job_id: str) -> ReidVideoJobResponse:
    job = get_reid_video_export_service().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Re-ID video job not found")
    return _reid_video_response(job)


async def download_reid_video(job_id: str):
    job = get_reid_video_export_service().get(job_id)
    if not job or job.status != "completed" or not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=404, detail="Re-ID video is not ready")
    return FileResponse(job.output_path, media_type="video/mp4",
                        filename=f"person_reid_camera{job.camera_id}_{job.id}.mp4")


def _reid_video_response(job) -> ReidVideoJobResponse:
    return ReidVideoJobResponse(
        job_id=job.id,
        camera_id=job.camera_id,
        zone_id=job.zone_id,
        status=job.status,
        progress=round(job.progress, 3),
        unique_persons=job.unique_persons,
        error=job.error,
        download_url=f"/api/analyzer/reid-video/{job.id}/download" if job.status == "completed" else None,
    )
