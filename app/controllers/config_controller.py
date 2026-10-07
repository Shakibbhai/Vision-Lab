from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core import runtime_config


async def get_config(session: AsyncSession):
    config = await runtime_config.get_runtime_config(session)
    return {"config": config}


async def update_config(session: AsyncSession, key: str, value: object):
    try:
        config = await runtime_config.set_runtime_config(session, key, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"config": config}


async def backup(session: AsyncSession):
    path = await runtime_config.backup_config(session)
    return {"path": path}


async def restore(session: AsyncSession, payload: dict):
    config = await runtime_config.restore_config(session, payload)
    return {"config": config}

