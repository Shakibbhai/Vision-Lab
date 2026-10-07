from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import monitoring_controller
from app.db.session import get_session
from app.schemas import AnalyticsRunState, BrowserFrameIngestResponse, MonitoringResponse, RealtimeMonitoringResponse

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/metrics", response_model=MonitoringResponse)
async def metrics(session: AsyncSession = Depends(get_session)):
    return await monitoring_controller.metrics(session)


@router.get("/realtime", response_model=RealtimeMonitoringResponse)
async def realtime(
    camera_id: list[int] | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await monitoring_controller.realtime(session, camera_id)


@router.get("/analytics", response_model=AnalyticsRunState)
async def analytics_state():
    return monitoring_controller.analytics_state()


@router.post("/analytics/start", response_model=AnalyticsRunState)
async def start_analytics():
    return monitoring_controller.start_analytics()


@router.post("/analytics/stop", response_model=AnalyticsRunState)
async def stop_analytics():
    return monitoring_controller.stop_analytics()


@router.get("/cameras/{camera_id}/frame")
async def latest_camera_frame(
    camera_id: int,
    zone_id: int | None = Query(default=None),
    analytics_mode: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await monitoring_controller.latest_frame(session, camera_id, zone_id, analytics_mode)


@router.get("/cameras/{camera_id}/first-frame")
async def first_camera_frame(
    camera_id: int,
    zone_id: int | None = Query(default=None),
    analytics_mode: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await monitoring_controller.first_frame(session, camera_id, zone_id, analytics_mode)


@router.get("/cameras/{camera_id}/source-frame")
async def source_camera_frame(
    camera_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await monitoring_controller.source_frame(session, camera_id)


@router.post("/cameras/{camera_id}/browser-frame", response_model=BrowserFrameIngestResponse)
async def ingest_browser_camera_frame(
    camera_id: int,
    frame: UploadFile = File(...),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
):
    payload = await frame.read()
    return await monitoring_controller.ingest_browser_frame(
        session,
        camera_id,
        payload,
        width=width,
        height=height,
    )


@router.get("/cameras/{camera_id}/facial-expression/realtime")
async def realtime_facial_expression(
    camera_id: int,
    zone_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await monitoring_controller.facial_expression_realtime(session, camera_id, zone_id)


@router.get("/cameras/{camera_id}/stream.mjpg")
async def camera_mjpeg_stream(
    camera_id: int,
    request: Request,
    zone_id: int | None = Query(default=None),
    analytics_mode: str | None = Query(default=None),
):
    return await monitoring_controller.mjpeg_stream(camera_id, request, zone_id, analytics_mode)
