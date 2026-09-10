import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import VIDEOS_DIR
from app.court import CourtType
from app.db import get_session
from app.models import Match
from app.schemas import CalibrationIn, MatchOut
from app.worker.tasks import process_uploaded_video

router = APIRouter(prefix="/matches", tags=["matches"])

ALLOWED_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv"}


@router.post("/upload", response_model=MatchOut)
async def upload_match_video(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    court_type: str = Form(...),
    calibration: str | None = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    if court_type not in {t.value for t in CourtType}:
        raise HTTPException(status_code=400, detail="court_type debe ser 'piso' o 'playa'")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Formato de vídeo no soportado: {suffix}")

    calibration_data = None
    if calibration:
        try:
            calibration_data = CalibrationIn.model_validate_json(calibration).model_dump()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Calibración inválida: {exc}") from exc

    dest_path = VIDEOS_DIR / f"{uuid.uuid4().hex}{suffix}"
    with dest_path.open("wb") as out_file:
        while chunk := await file.read(1024 * 1024):
            out_file.write(chunk)

    match = Match(
        name=name,
        court_type=court_type,
        source_mode="upload",
        status="pending",
        video_path=str(dest_path),
        calibration=calibration_data,
    )
    session.add(match)
    await session.commit()
    await session.refresh(match)

    background_tasks.add_task(process_uploaded_video, match.id)
    return match
