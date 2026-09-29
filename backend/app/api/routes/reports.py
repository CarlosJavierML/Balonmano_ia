from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import REPORTS_DIR
from app.db import get_session
from app.models import Match, PlayerMatchStat
from app.reports.pdf_report import build_match_report

router = APIRouter(prefix="/matches", tags=["reports"])


@router.get("/{match_id}/report")
async def download_report(match_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.player_stats), selectinload(Match.events), selectinload(Match.cameras))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if match.status != "done":
        raise HTTPException(status_code=409, detail=f"La sesión aún no está lista (estado: {match.status})")

    out_path = REPORTS_DIR / f"match_{match_id}.pdf"
    build_match_report(match, match.player_stats, match.events, out_path, REPORTS_DIR / "_tmp")

    return FileResponse(
        path=str(out_path),
        media_type="application/pdf",
        filename=f"informe_{match.name}_{match_id}.pdf",
    )


@router.get("/{match_id}/heatmap/{track_id}")
async def get_heatmap(match_id: int, track_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(PlayerMatchStat).where(
            PlayerMatchStat.match_id == match_id, PlayerMatchStat.track_id == track_id
        )
    )
    stat = result.scalar_one_or_none()
    if stat is None or not stat.heatmap_path or not Path(stat.heatmap_path).exists():
        raise HTTPException(status_code=404, detail="Mapa de calor no disponible")
    return FileResponse(path=stat.heatmap_path, media_type="image/png")
