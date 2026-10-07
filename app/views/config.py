from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import config_controller
from app.db.session import get_session
from app.schemas import RuntimeConfigRead, RuntimeConfigUpdate

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=RuntimeConfigRead)
async def get_config(session: AsyncSession = Depends(get_session)):
    return await config_controller.get_config(session)


@router.put("", response_model=RuntimeConfigRead)
async def update_config(
    payload: RuntimeConfigUpdate,
    session: AsyncSession = Depends(get_session),
):
    return await config_controller.update_config(session, payload.key, payload.value)


@router.post("/backup")
async def backup(session: AsyncSession = Depends(get_session)):
    return await config_controller.backup(session)


@router.post("/restore", response_model=RuntimeConfigRead)
async def restore(payload: dict, session: AsyncSession = Depends(get_session)):
    return await config_controller.restore(session, payload)
