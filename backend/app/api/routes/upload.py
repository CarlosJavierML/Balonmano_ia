import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import VIDEOS_DIR
from app.court import CourtType
from app.db import get_session
from app.models import Camera, Match
from app.schemas import CalibrationIn, CameraIn, MatchOut
from app.worker.queue import enqueue

router = APIRouter(prefix="/matches", tags=["matches"])

ALLOWED_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv"}
MAX_CAMERAS = 4

_cameras_adapter = TypeAdapter(list[CameraIn])


async def _save_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    dest_path = VIDEOS_DIR / f"{uuid.uuid4().hex}{suffix}"
    with dest_path.open("wb") as out_file:
        while chunk := await file.read(1024 * 1024):
            out_file.write(chunk)
    return dest_path


@router.post("/upload", response_model=MatchOut)
async def upload_match_video(
    name: str = Form(...),
    court_type: str = Form(...),
    calibration: str | None = Form(None),
    cameras: str | None = Form(None),
    file: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Uploads one video (single camera, optional `calibration`) or several
    videos of the same session from different cameras (`file` repeated, plus
    `cameras`: a JSON list with each camera's name, court region,
    calibration and optional sync offset, in the same order as the files)."""
    if court_type not in {t.value for t in CourtType}:
        raise HTTPException(status_code=400, detail="court_type debe ser 'piso' o 'playa'")
    if len(file) > MAX_CAMERAS:
        raise HTTPException(status_code=400, detail=f"Como máximo {MAX_CAMERAS} cámaras por sesión")

    for f in file:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"Formato de vídeo no soportado: {suffix}")

    calibration_data = None
    if calibration:
        try:
            calibration_data = CalibrationIn.model_validate_json(calibration).model_dump()
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Calibración inválida: {exc}") from exc

    camera_settings: list[CameraIn] = []
    if len(file) > 1:
        try:
            camera_settings = _cameras_adapter.validate_json(cameras) if cameras else []
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Configuración de cámaras inválida: {exc}") from exc
        if camera_settings and len(camera_settings) != len(file):
            raise HTTPException(status_code=400, detail="Debe haber una configuración por cada vídeo")
        camera_settings = camera_settings or [CameraIn() for _ in file]

    match = Match(name=name, court_type=court_type, source_mode="upload", status="pending")
    if len(file) == 1:
        match.video_path = str(await _save_upload(file[0]))
        match.calibration = calibration_data
    else:
        for index, (f, cam) in enumerate(zip(file, camera_settings)):
            calib = cam.calibration.model_dump() if cam.calibration else None
            if calib is not None:
                calib["region"] = cam.region  # the camera's region is authoritative
            match.cameras.append(
                Camera(
                    index=index,
                    name=cam.name or f"Cámara {index + 1}",
                    region=cam.region,
                    video_path=str(await _save_upload(f)),
                    calibration=calib,
                    time_offset_s=0.0 if index == 0 else cam.time_offset_s,
                )
            )

    session.add(match)
    await session.flush()
    await enqueue(session, match, "video")
    await session.commit()
    await session.refresh(match)
    return match
