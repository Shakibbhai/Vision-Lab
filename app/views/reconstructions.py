from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import ServiceContainer
from app.core.dependencies import get_service_container
from app.controllers import reconstruction_controller
from app.db.session import get_session
from app.schemas import ReconstructionCreate, ReconstructionRead

router = APIRouter(prefix="/reconstructions", tags=["reconstructions"])


@router.post("", response_model=ReconstructionRead, status_code=201)
async def create_reconstruction(
    payload: ReconstructionCreate,
    session: AsyncSession = Depends(get_session),
    services: ServiceContainer = Depends(get_service_container),
):
    return await reconstruction_controller.create_reconstruction(session, services, payload)


@router.get("", response_model=list[ReconstructionRead])
async def list_reconstructions(session: AsyncSession = Depends(get_session)):
    return await reconstruction_controller.list_reconstructions(session)


@router.get("/{job_id}", response_model=ReconstructionRead)
async def get_reconstruction(job_id: int, session: AsyncSession = Depends(get_session)):
    return await reconstruction_controller.get_reconstruction(session, job_id)


@router.get("/{job_id}/download")
async def download_reconstruction(job_id: int, session: AsyncSession = Depends(get_session)):
    return await reconstruction_controller.download_reconstruction(session, job_id)
