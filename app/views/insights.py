"""Aggregated analytics for the overview dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import insights_controller
from app.db.session import get_session

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/overview")
async def get_overview(session: AsyncSession = Depends(get_session)):
    return await insights_controller.get_overview(session)
