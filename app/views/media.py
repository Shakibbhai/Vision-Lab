from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import media_controller
from app.db.session import get_session
from app.schemas import MediaUploadResponse

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload", response_model=MediaUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    location: str | None = Form(default=None),
    camera_id: int | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await media_controller.upload_video_source(
        session=session,
        file=file,
        name=name,
        location=location,
        camera_id=camera_id,
    )
