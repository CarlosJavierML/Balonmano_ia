from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import REPORTS_DIR
from app.db import get_session
from app.models import Match
from app.schemas import MatchDetailOut, MatchOut
from app.vision.live import live_registry

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
async def list_matches(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Match).order_by(Match.created_at.desc()))
    return result.scalars().all()


@router.get("/{match_id}", response_model=MatchDetailOut)
async def get_match(match_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.player_stats), selectinload(Match.events))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return match


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

    files = [match.video_path, str(REPORTS_DIR / f"match_{match_id}.pdf")]
    files += [s.heatmap_path for s in match.player_stats]

    await session.delete(match)
    await session.commit()

    for path in files:
        if path:
            Path(path).unlink(missing_ok=True)
