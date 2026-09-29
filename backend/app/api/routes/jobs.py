from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Job, Match
from app.schemas import JobOut, QueueItemOut
from app.vision.live import live_registry
from app.worker.queue import (
    ACTIVE_STATUSES,
    JobAlreadyActiveError,
    active_job,
    enqueue,
    queue_positions,
    request_cancel,
)
from app.worker.tasks import tracking_path_for

router = APIRouter(tags=["queue"])

RECENT_FINISHED_LIMIT = 10


@router.get("/jobs", response_model=list[QueueItemOut])
async def list_jobs(include_finished: bool = False, session: AsyncSession = Depends(get_session)):
    """The analysis queue: running jobs first, then queued ones in the order
    they will run; optionally followed by the most recently finished."""
    rows = (
        await session.execute(
            select(Job, Match).join(Match, Match.id == Job.match_id).where(Job.status.in_(ACTIVE_STATUSES))
        )
    ).all()
    if include_finished:
        rows += (
            await session.execute(
                select(Job, Match)
                .join(Match, Match.id == Job.match_id)
                .where(Job.status.not_in(ACTIVE_STATUSES))
                .order_by(Job.finished_at.desc(), Job.id.desc())
                .limit(RECENT_FINISHED_LIMIT)
            )
        ).all()

    positions = await queue_positions(session)
    order = {"running": 0, "queued": 1}
    rows.sort(key=lambda r: (order.get(r[0].status, 2), positions.get(r[0].id, 0)))
    return [
        QueueItemOut(
            **JobOut.model_validate(job).model_dump(exclude={"position"}),
            position=positions.get(job.id),
            match_name=match.name,
            progress=match.progress,
        )
        for job, match in rows
    ]


@router.post("/matches/{match_id}/cancel", response_model=JobOut)
async def cancel_analysis(match_id: int, session: AsyncSession = Depends(get_session)):
    job = await active_job(session, match_id)
    if job is None:
        raise HTTPException(status_code=409, detail="Esta sesión no tiene ningún análisis en cola ni en curso")
    await request_cancel(session, job)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/matches/{match_id}/reanalyze", response_model=JobOut)
async def reanalyze(match_id: int, session: AsyncSession = Depends(get_session)):
    """Queues a full new analysis: re-runs the vision pipeline on the
    uploaded video, or re-analyzes the saved trajectories of a live
    session. Useful after a failure, a cancellation, or to benefit from an
    improved model/algorithm."""
    match = await session.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if live_registry.get(match_id) is not None:
        raise HTTPException(status_code=409, detail="La transmisión en directo sigue activa")

    if match.video_path and Path(match.video_path).exists():
        kind = "video"
    elif tracking_path_for(match_id).exists():
        kind = "tracking"
    else:
        raise HTTPException(
            status_code=409, detail="No queda ni el vídeo ni las trayectorias de esta sesión para reanalizarla"
        )

    try:
        job = await enqueue(session, match, kind)
    except JobAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail="Esta sesión ya tiene un análisis en cola o en curso") from exc
    await session.commit()
    positions = await queue_positions(session)
    return JobOut.model_validate(job).model_copy(update={"position": positions.get(job.id)})
