"""Analyzer API endpoints for running YOLO-based person detection and tracking."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import analyzer_controller
from app.db.session import get_session
from app.models.analyzer import (
    AnalysisJobResponse,
    AnalysisRequest,
    FacialExpressionRecognitionRequest,
    FacialExpressionRecognitionResponse,
    TotalPersonDetectionRequest,
    TotalPersonDetectionResponse,
    FaceRecognitionRequest,
    FaceRecognitionResponse,
    ReidVideoJobResponse,
    ReidVideoRequest,
)

router = APIRouter(prefix="/analyzer", tags=["analyzer"])


@router.get("/status")
async def get_analyzer_status():
    return await analyzer_controller.get_analyzer_status()


@router.get("/cameras")
async def list_cameras_for_analysis(
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.list_cameras_for_analysis(session)


@router.get("/zones/{camera_id}")
async def list_zones_for_camera(
    camera_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.list_zones_for_camera(session, camera_id)


@router.post("/run")
async def start_analysis(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.start_analysis(session, request, background_tasks)


@router.post("/total-person-detection", response_model=TotalPersonDetectionResponse)
async def detect_total_persons(
    request: TotalPersonDetectionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.detect_total_persons(session, request)


@router.post(
    "/facial-expression-recognition",
    response_model=FacialExpressionRecognitionResponse,
)
async def detect_facial_expressions(
    request: FacialExpressionRecognitionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.detect_facial_expressions(session, request)


@router.post(
    "/face-recognition",
    response_model=FaceRecognitionResponse,
)
async def detect_face_recognition(
    request: FaceRecognitionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.detect_face_recognition(session, request)


@router.get("/jobs")
async def list_analysis_jobs(
    camera_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[AnalysisJobResponse]:
    return await analyzer_controller.list_analysis_jobs(session, camera_id)


@router.get("/jobs/{job_id}")
async def get_analysis_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> AnalysisJobResponse:
    return await analyzer_controller.get_analysis_job(session, job_id)


@router.get("/stats/{job_id}")
async def get_analysis_stats(
    job_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.get_analysis_stats(session, job_id)


@router.get("/tracks/{job_id}")
async def get_person_tracks(
    job_id: int,
    zone_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.get_person_tracks(session, job_id, zone_id)


@router.post("/reid-video", response_model=ReidVideoJobResponse)
async def start_reid_video(
    request: ReidVideoRequest,
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.start_reid_video(session, request)


@router.get("/reid-video", response_model=ReidVideoJobResponse)
async def get_latest_reid_video(
    camera_id: int = Query(...),
    zone_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await analyzer_controller.get_latest_reid_video(session, camera_id, zone_id)


@router.get("/reid-video/{job_id}", response_model=ReidVideoJobResponse)
async def get_reid_video(job_id: str):
    return await analyzer_controller.get_reid_video(job_id)


@router.get("/reid-video/{job_id}/download")
async def download_reid_video(job_id: str):
    return await analyzer_controller.download_reid_video(job_id)
