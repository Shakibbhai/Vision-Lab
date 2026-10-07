from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import ServiceContainer
from app.core.dependencies import get_service_container
from app.controllers import stream_controller
from app.db.session import get_session
from app.schemas import StreamRead, StreamStartRequest, StreamStatusRead, StreamStopRequest

router = APIRouter(prefix="/streams", tags=["streams"])


@router.post("/start", response_model=StreamRead)
async def start_stream(
    payload: StreamStartRequest,
    session: AsyncSession = Depends(get_session),
    services: ServiceContainer = Depends(get_service_container),
):
    return await stream_controller.start_stream(session, services, payload)


@router.post("/stop", response_model=StreamRead)
async def stop_stream(
    payload: StreamStopRequest,
    session: AsyncSession = Depends(get_session),
    services: ServiceContainer = Depends(get_service_container),
):
    return await stream_controller.stop_stream(session, services, payload)


@router.get("", response_model=list[StreamRead])
async def list_streams(session: AsyncSession = Depends(get_session)):
    return await stream_controller.list_streams(session)


@router.get("/{stream_id}", response_model=StreamRead)
async def get_stream(stream_id: int, session: AsyncSession = Depends(get_session)):
    return await stream_controller.get_stream(session, stream_id)


@router.delete("/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(
    stream_id: int,
    session: AsyncSession = Depends(get_session),
    services: ServiceContainer = Depends(get_service_container),
):
    await stream_controller.delete_stream_record(session, services, stream_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{stream_id}/status", response_model=StreamStatusRead)
async def get_stream_status(
    stream_id: int,
    session: AsyncSession = Depends(get_session),
    services: ServiceContainer = Depends(get_service_container),
):
    return await stream_controller.get_stream_status(session, services, stream_id)
