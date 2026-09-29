from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import REPORTS_DIR
from app.db import get_session
from app.models import Match
from app.schemas import JobOut, MatchDetailOut, MatchOut, PlayerUpdateIn, TeamNamesIn
from app.vision.live import live_registry
from app.worker.queue import active_job, queue_positions
from app.worker.tasks import TrackingUnavailableError, recompute_tactics

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
async def list_matches(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Match).order_by(Match.created_at.desc()))
    return result.scalars().all()


async def _load_match(session: AsyncSession, match_id: int) -> Match:
    result = await session.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.player_stats), selectinload(Match.events))
        .execution_options(populate_existing=True)
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return match


async def _detail(session: AsyncSession, match_id: int) -> MatchDetailOut:
    match = await _load_match(session, match_id)
    detail = MatchDetailOut.model_validate(match)
    job = await active_job(session, match_id)
    if job is not None:
        positions = await queue_positions(session)
        detail.active_job = JobOut.model_validate(job).model_copy(update={"position": positions.get(job.id)})
    return detail


@router.get("/{match_id}", response_model=MatchDetailOut)
async def get_match(match_id: int, session: AsyncSession = Depends(get_session)):
    return await _detail(session, match_id)


@router.patch("/{match_id}/teams", response_model=MatchDetailOut)
async def rename_teams(match_id: int, payload: TeamNamesIn, session: AsyncSession = Depends(get_session)):
    match = await _load_match(session, match_id)
    names = dict(match.team_names or {})
    for team, name in payload.names.items():
        name = name.strip()
        if name:
            names[team] = name[:60]
        else:
            names.pop(team, None)  # empty name = back to "Equipo A/B"
    match.team_names = names or None
    await session.commit()
    return await _detail(session, match_id)


@router.patch("/{match_id}/players/{track_id}", response_model=MatchDetailOut)
async def update_player(
    match_id: int, track_id: int, payload: PlayerUpdateIn, session: AsyncSession = Depends(get_session)
):
    """Rename a tracked person and/or fix their team or role. Team/role
    changes re-run the tactical analysis (passes vs. turnovers, possession)
    from the saved trajectories, without re-processing the video."""
    match = await _load_match(session, match_id)
    stat = next((s for s in match.player_stats if s.track_id == track_id), None)
    if stat is None:
        raise HTTPException(status_code=404, detail="Jugador no encontrado en esta sesión")

    if payload.label is not None:
        stat.label = payload.label.strip() or f"Jugador #{track_id}"

    fields = payload.model_fields_set
    if payload.reset or fields & {"team", "role"}:
        if not match.editable:
            raise HTTPException(
                status_code=409,
                detail="Esta sesión se analizó con una versión anterior y no guarda las trayectorias; "
                "vuelve a analizar el vídeo para poder corregir equipos.",
            )
        overrides = {k: dict(v) for k, v in (match.overrides or {}).items()}
        fix = {} if payload.reset else overrides.get(str(track_id), {})
        if "team" in fields:
            fix["team"] = payload.team
        if "role" in fields:
            fix["role"] = payload.role
        if fix:
            overrides[str(track_id)] = fix
        else:
            overrides.pop(str(track_id), None)
        match.overrides = overrides or None
        try:
            await recompute_tactics(session, match)
        except TrackingUnavailableError as exc:  # pragma: no cover - guarded by `editable`
            raise HTTPException(status_code=409, detail="Trayectorias no disponibles") from exc

    await session.commit()
    return await _detail(session, match_id)


@router.delete("/{match_id}", status_code=204)
async def delete_match(match_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.player_stats), selectinload(Match.events))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if live_registry.get(match_id) is not None:
        raise HTTPException(status_code=409, detail="Detén la transmisión en directo antes de eliminarla")
    job = await active_job(session, match_id)
    if job is not None and job.status == "running":
        raise HTTPException(status_code=409, detail="Cancela el análisis en curso antes de eliminar la sesión")

    files = [match.video_path, match.tracking_path, str(REPORTS_DIR / f"match_{match_id}.pdf")]
    files += [s.heatmap_path for s in match.player_stats]

    await session.delete(match)
    await session.commit()

    for path in files:
        if path:
            Path(path).unlink(missing_ok=True)
