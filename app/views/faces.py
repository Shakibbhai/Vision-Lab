from __future__ import annotations

import os
import shutil
from uuid import uuid4
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.session import get_session as get_db

router = APIRouter(prefix="/faces", tags=["Face Management"], redirect_slashes=False)

try:
    from deepface import DeepFace
except ImportError:
    DeepFace = None

FACES_DIR = "data/faces"


@router.on_event("startup")
async def startup_event():
    os.makedirs(FACES_DIR, exist_ok=True)


@router.get("")
@router.get("/")
async def list_faces(session: AsyncSession = Depends(get_db)):
    faces = await crud.list_face_references(session)
    return [
        {
            "id": f.id,
            "name": f.name,
            "image_path": f.image_path,
            "created_at": f.created_at,
        }
        for f in faces
    ]


@router.get("/{face_id}/image")
async def get_face_image(face_id: int, session: AsyncSession = Depends(get_db)):
    face = await crud.get_face_reference(session, face_id)
    if not face:
        raise HTTPException(status_code=404, detail="Face not found")
    if not face.image_path or not os.path.exists(face.image_path):
        raise HTTPException(status_code=404, detail="Image file not found")
    return FileResponse(face.image_path, media_type="image/jpeg")


@router.post("")
@router.post("/")
async def create_face(
    name: str = Form(...),
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    session: AsyncSession = Depends(get_db)
):
    if DeepFace is None:
        raise HTTPException(status_code=500, detail="DeepFace is not installed (missing deepface package).")

    incoming_files = [f for f in ([file] if file is not None else []) + (files or []) if f is not None]
    if not incoming_files:
        raise HTTPException(status_code=400, detail="At least one image file is required.")

    os.makedirs(FACES_DIR, exist_ok=True)
    created: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    safe_name = "".join(c if c.isalnum() or c in {"-", "_"} else "_" for c in name.strip()) or "face"

    for upload in incoming_files:
        original_name = os.path.basename(upload.filename or "image.jpg")
        filename = f"{safe_name}_{uuid4().hex}_{original_name}"
        file_path = os.path.join(FACES_DIR, filename)

        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)

            embedding_objs = DeepFace.represent(
                img_path=file_path,
                model_name="ArcFace",
                detector_backend="opencv",
            )
            if not embedding_objs:
                raise HTTPException(status_code=400, detail="No face detected in the image.")

            embedding = embedding_objs[0]["embedding"]
            face = await crud.create_face_reference(
                session=session,
                name=name,
                image_path=file_path,
                embedding=embedding,
            )
            created.append(
                {
                    "id": face.id,
                    "name": face.name,
                    "image_path": face.image_path,
                    "created_at": face.created_at,
                }
            )
        except HTTPException as exc:
            if os.path.exists(file_path):
                os.remove(file_path)
            errors.append({"filename": original_name, "detail": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001
            if os.path.exists(file_path):
                os.remove(file_path)
            errors.append({"filename": original_name, "detail": str(exc)})

    if not created:
        detail = "; ".join(f"{item['filename']}: {item['detail']}" for item in errors) or "Failed to process face images."
        raise HTTPException(status_code=400, detail=detail)

    return {
        "created": created,
        "errors": errors,
    }


@router.delete("/{face_id}")
@router.delete("/{face_id}/")
async def delete_face(face_id: int, session: AsyncSession = Depends(get_db)):
    face = await crud.get_face_reference(session, face_id)
    if not face:
        raise HTTPException(status_code=404, detail="Face not found")
        
    await crud.delete_face_reference(session, face)
    if face.image_path and os.path.exists(face.image_path):
        try:
            os.remove(face.image_path)
        except OSError:
            pass # Non-fatal if we can't delete the file, but the DB record is gone.
        
    return {"status": "ok"}
