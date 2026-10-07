from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import ServiceContainer
from app.db import crud
from app.schemas import ReconstructionCreate


async def create_reconstruction(
    session: AsyncSession,
    services: ServiceContainer,
    payload: ReconstructionCreate,
):
    job = await crud.create_reconstruction_job(
        session,
        payload.camera_id,
        payload.start_time,
        payload.end_time,
    )
    service = services.reconstruction_service
    if not service:
        raise HTTPException(status_code=503, detail="Reconstruction service is not available")
    await service.submit(
        job.id,
        overlay_lines=payload.overlay_lines,
        analysis_job_id=payload.analysis_job_id,
        draw_tracking=payload.draw_tracking,
        draw_facial_expression=payload.draw_facial_expression,
        facial_expression_zone_id=payload.facial_expression_zone_id,
        output_fps=payload.output_fps,
    )
    return job


async def list_reconstructions(session: AsyncSession):
    return await crud.list_reconstruction_jobs(session)


async def get_reconstruction(session: AsyncSession, job_id: int):
    job = await crud.get_reconstruction_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def download_reconstruction(session: AsyncSession, job_id: int):
    job = await crud.get_reconstruction_job(session, job_id)
    if not job or not job.output_path:
        raise HTTPException(status_code=404, detail="Output not ready")
    output_path = job.output_path
    if not output_path or not Path(output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found on disk")

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"reconstruction_{job_id}.mp4",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
