"""Queue management API endpoints for zone-based queue counting."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import queue_controller
from app.db.session import get_session
from app.models.queue import QueueAnalysisRequest, QueueJobResponse

router = APIRouter(prefix="/queue", tags=["queue-management"])


@router.get("/status")
async def get_queue_status():
    return await queue_controller.get_queue_status()


@router.get("/cameras")
async def list_cameras_for_queue(
    session: AsyncSession = Depends(get_session),
):
    return await queue_controller.list_cameras_for_queue(session)


@router.get("/zones/{camera_id}")
async def list_zones_for_queue(
    camera_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await queue_controller.list_zones_for_queue(session, camera_id)


@router.post("/analyze")
async def start_queue_analysis(
    request: QueueAnalysisRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    return await queue_controller.start_queue_analysis(session, request, background_tasks)


@router.get("/jobs")
async def list_queue_jobs(
    camera_id: int | None = Query(default=None),
    zone_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[QueueJobResponse]:
    return await queue_controller.list_queue_jobs(session, camera_id, zone_id)


@router.get("/jobs/{job_id}")
async def get_queue_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> QueueJobResponse:
    return await queue_controller.get_queue_job(session, job_id)


@router.get("/results/{job_id}")
async def get_queue_results(
    job_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await queue_controller.get_queue_results(session, job_id)
