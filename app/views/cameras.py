from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import ServiceContainer
from app.core.dependencies import get_service_container
from app.controllers import camera_controller
from app.db.session import get_session
from app.schemas import CameraCreate, CameraRead, CameraUpdate

router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.post("", response_model=CameraRead, status_code=status.HTTP_201_CREATED)
async def create_camera(payload: CameraCreate, session: AsyncSession = Depends(get_session)):
    return await camera_controller.create_camera(session, payload)


@router.get("", response_model=list[CameraRead])
async def list_cameras(session: AsyncSession = Depends(get_session)):
    return await camera_controller.list_cameras(session)


@router.get("/{camera_id}", response_model=CameraRead)
async def get_camera(camera_id: int, session: AsyncSession = Depends(get_session)):
    return await camera_controller.get_camera(session, camera_id)


@router.put("/{camera_id}", response_model=CameraRead)
async def update_camera(
    camera_id: int,
    payload: CameraUpdate,
    session: AsyncSession = Depends(get_session),
    services: ServiceContainer = Depends(get_service_container),
):
    return await camera_controller.update_camera(session, services, camera_id, payload)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: int,
    session: AsyncSession = Depends(get_session),
    services: ServiceContainer = Depends(get_service_container),
):
    await camera_controller.delete_camera(session, services, camera_id)
    return None
