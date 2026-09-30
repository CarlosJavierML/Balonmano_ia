from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Match
from app.schemas import JobOut, ReplayVideoIn
from app.worker.queue import JobAlreadyActiveError, enqueue, queue_positions
from app.worker.tasks import TrackingUnavailableError, load_replay

router = APIRouter(prefix="/matches", tags=["replay"])

NOT_AVAILABLE = (
    "Esta sesión se analizó con una versión anterior y no guarda las trayectorias; "
    "vuelve a analizarla para ver la recreación."
)


async def _finished_match(session: AsyncSession, match_id: int) -> Match:
    match = await session.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if match.status != "done":
        raise HTTPException(status_code=409, detail=f"La sesión aún no está lista (estado: {match.status})")
    return match


@router.get("/{match_id}/replay")
async def get_replay(match_id: int, session: AsyncSession = Depends(get_session)):
    """Everything needed to animate the session on a court diagram:
    trajectories (t, x, y in meters) of every person and the ball, their
    names/teams/colors, and the tactical events."""
    await _finished_match(session, match_id)
    try:
        return await load_replay(match_id)
    except TrackingUnavailableError as exc:
        raise HTTPException(status_code=409, detail=NOT_AVAILABLE) from exc


@router.post("/{match_id}/replay-video", response_model=JobOut)
async def render_replay_video(match_id: int, payload: ReplayVideoIn, session: AsyncSession = Depends(get_session)):
    """Queues rendering the recreation as an MP4 video (x1, x2 or x4 speed)."""
    match = await _finished_match(session, match_id)
    if not match.tracking_path or not Path(match.tracking_path).exists():
        raise HTTPException(status_code=409, detail=NOT_AVAILABLE)
    try:
        job = await enqueue(session, match, "replay_video", {"speed": payload.speed})
    except JobAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail="Esta sesión ya tiene un trabajo en cola o en curso") from exc
    await session.commit()
    positions = await queue_positions(session)
    return JobOut.model_validate(job).model_copy(update={"position": positions.get(job.id)})


@router.get("/{match_id}/replay-video")
async def download_replay_video(match_id: int, session: AsyncSession = Depends(get_session)):
    match = await session.get(Match, match_id)
    if match is None or not match.replay_video or not Path(match.replay_video["path"]).exists():
        raise HTTPException(status_code=404, detail="El vídeo de la recreación no está generado")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in match.name).strip() or "sesion"
    return FileResponse(
        match.replay_video["path"],
        media_type="video/mp4",
        filename=f"recreacion_{safe_name}_{match_id}.mp4",
        content_disposition_type="inline",
    )
