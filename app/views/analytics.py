from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import analytics_controller
from app.db.session import get_session
from app.schemas import FootfallResponse, ZoneAnalysisResponse, ZoneCreate, ZoneRead

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/zones", response_model=ZoneRead)
async def create_zone(payload: ZoneCreate, session: AsyncSession = Depends(get_session)):
    return await analytics_controller.create_zone(
        session,
        payload.camera_id,
        payload.name,
        payload.polygon,
        payload.capacity,
        payload.expected_wait_time_sec,
    )


@router.get("/zones", response_model=list[ZoneRead])
async def list_zones(
    camera_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await analytics_controller.list_zones(session, camera_id)


@router.delete("/zones/{zone_id}", status_code=204)
async def delete_zone(zone_id: int, session: AsyncSession = Depends(get_session)):
    await analytics_controller.delete_zone(session, zone_id)


@router.get("/zone-preview")
async def zone_preview(
    camera_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
):
    return await analytics_controller.zone_preview(session, camera_id)


@router.get("/footfall", response_model=FootfallResponse)
async def get_footfall(
    camera_id: int = Query(...),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    zone_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await analytics_controller.get_footfall(session, camera_id, start, end, zone_id)


@router.get("/zone-analysis", response_model=ZoneAnalysisResponse)
async def get_zone_analysis(
    camera_id: int = Query(...),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    zone_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await analytics_controller.get_zone_analysis(session, camera_id, start, end, zone_id)
