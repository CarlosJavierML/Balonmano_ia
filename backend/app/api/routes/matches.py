from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Match
from app.schemas import MatchDetailOut, MatchOut

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
    match = await session.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    await session.delete(match)
    await session.commit()
