"""Database-backed job queue for analysis work.

Jobs live in the `jobs` table, so the queue survives restarts, is visible
from the API (position, progress, errors) and needs no extra service
(Redis/RabbitMQ): any number of worker processes can share it, because a
job is claimed with a single conditional UPDATE -- only the worker whose
UPDATE actually flips the row from "queued" to "running" gets it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Job, Match

ACTIVE_STATUSES = ("queued", "running")
# Jobs that (re)compute the analysis and so drive the match status
# (pending -> processing -> done/failed). Other jobs, like rendering the
# recreation video, work on a finished analysis and leave the status alone.
ANALYSIS_KINDS = frozenset({"video", "tracking"})


def drives_match_status(kind: str) -> bool:
    return kind in ANALYSIS_KINDS


class JobAlreadyActiveError(Exception):
    """The match already has a queued or running job."""


async def active_job(session: AsyncSession, match_id: int) -> Job | None:
    result = await session.execute(
        select(Job).where(Job.match_id == match_id, Job.status.in_(ACTIVE_STATUSES)).order_by(Job.id.desc())
    )
    return result.scalars().first()


async def enqueue(session: AsyncSession, match: Match, kind: str, params: dict | None = None) -> Job:
    """Queues work for a match (caller commits)."""
    if await active_job(session, match.id) is not None:
        raise JobAlreadyActiveError
    job = Job(
        match_id=match.id, kind=kind, params=params, status="queued", max_attempts=settings.job_max_attempts
    )
    session.add(job)
    if drives_match_status(kind):
        match.status = "pending"
        match.progress = 0.0
        match.error_message = None
    return job


async def claim_next_job(session: AsyncSession, worker_id: str) -> Job | None:
    """Atomically takes the oldest queued job, or returns None."""
    while True:
        candidate = await session.scalar(
            select(Job.id).where(Job.status == "queued").order_by(Job.id).limit(1)
        )
        if candidate is None:
            return None
        now = datetime.utcnow()
        claimed = await session.execute(
            update(Job)
            .where(Job.id == candidate, Job.status == "queued")
            .values(
                status="running",
                worker_id=worker_id,
                attempts=Job.attempts + 1,
                started_at=now,
                heartbeat_at=now,
            )
        )
        await session.commit()
        if claimed.rowcount == 1:
            return await session.get(Job, candidate, populate_existing=True)
        # Another worker won the race for this job; try the next one.


async def requeue_stale_jobs(session: AsyncSession) -> int:
    """Running jobs whose worker stopped sending heartbeats (crash, restart,
    container killed) go back to the queue -- or fail if out of attempts."""
    cutoff = datetime.utcnow() - timedelta(seconds=settings.job_stale_after_s)
    result = await session.execute(
        select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)
    )
    stale = result.scalars().all()
    for job in stale:
        match = await session.get(Match, job.match_id) if drives_match_status(job.kind) else None
        if job.cancel_requested:
            job.status = "cancelled"
            job.finished_at = datetime.utcnow()
            if match is not None:
                match.status = "cancelled"
        elif job.attempts < job.max_attempts:
            job.status = "queued"
            job.worker_id = None
            if match is not None:
                match.status = "pending"
        else:
            job.status = "failed"
            job.error = "El proceso de análisis se interrumpió (worker detenido)"
            job.finished_at = datetime.utcnow()
            if match is not None:
                match.status = "failed"
                match.error_message = job.error
    await session.commit()
    return len(stale)


async def request_cancel(session: AsyncSession, job: Job) -> None:
    """Queued jobs are cancelled right away; running ones are flagged and
    the worker stops at its next progress check (caller commits)."""
    match = await session.get(Match, job.match_id)
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = datetime.utcnow()
        if match is not None:
            match.status = "cancelled"
    else:
        job.cancel_requested = True


async def queue_positions(session: AsyncSession) -> dict[int, int]:
    """{job_id: 1-based position} for queued jobs, in processing order."""
    ids = (await session.execute(select(Job.id).where(Job.status == "queued").order_by(Job.id))).scalars()
    return {job_id: i for i, job_id in enumerate(ids, start=1)}
