"""The queue worker: takes jobs from the database queue and runs them.

One worker runs at most ``settings.worker_concurrency`` jobs at a time
(default 1: video analysis saturates the CPU/GPU, so running two at once
only makes both slower). Several workers -- e.g. one per GPU machine -- can
share the same queue safely.

While a job runs, a heartbeat task periodically:
  * stores the progress on the match (shown as a progress bar),
  * refreshes ``heartbeat_at`` so other workers know this job is alive,
  * checks whether the user asked to cancel it.
If a worker dies mid-job, its heartbeat stops and `requeue_stale_jobs`
(called by every worker) puts the job back in the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import threading
import uuid
from datetime import datetime

from app.config import settings
from app.db import async_session_maker
from app.models import Job, Match
from app.worker.queue import claim_next_job, drives_match_status, requeue_stale_jobs
from app.worker.tasks import JOB_HANDLERS

logger = logging.getLogger(__name__)

# Failures that won't go away by trying again: don't waste a retry on them.
NON_RETRYABLE = (FileNotFoundError, ValueError)
# How often idle workers look for orphaned jobs, in poll cycles.
STALE_CHECK_EVERY = 10


class JobCancelledError(Exception):
    pass


class Worker:
    def __init__(self, concurrency: int | None = None, worker_id: str | None = None) -> None:
        self.concurrency = max(1, concurrency or settings.worker_concurrency)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._running: set[asyncio.Task] = set()

    async def run(self, stop: asyncio.Event) -> None:
        """Main loop: keeps up to `concurrency` jobs running until `stop`."""
        logger.info("Worker %s started (concurrency=%s)", self.worker_id, self.concurrency)
        cycle = 0
        try:
            while not stop.is_set():
                if cycle % STALE_CHECK_EVERY == 0:
                    await self._requeue_stale()
                cycle += 1

                while len(self._running) < self.concurrency:
                    job = await self._claim()
                    if job is None:
                        break
                    task = asyncio.create_task(self.execute(job.id))
                    self._running.add(task)
                    task.add_done_callback(self._running.discard)

                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_s)
        finally:
            # On shutdown, let in-flight jobs be picked up again by the next
            # worker rather than leaving them "running" until they go stale.
            for task in self._running:
                task.cancel()
            await asyncio.gather(*self._running, return_exceptions=True)
            logger.info("Worker %s stopped", self.worker_id)

    async def run_until_empty(self) -> None:
        """Processes queued jobs one by one until the queue is empty (tests, CLI)."""
        while (job := await self._claim()) is not None:
            await self.execute(job.id)

    async def _claim(self) -> Job | None:
        try:
            async with async_session_maker() as session:
                return await claim_next_job(session, self.worker_id)
        except Exception:  # noqa: BLE001 - a DB hiccup must not kill the worker loop
            logger.exception("Could not claim a job")
            return None

    async def _requeue_stale(self) -> None:
        try:
            async with async_session_maker() as session:
                if count := await requeue_stale_jobs(session):
                    logger.warning("Re-queued %s orphaned job(s)", count)
        except Exception:  # noqa: BLE001
            logger.exception("Could not check for orphaned jobs")

    async def execute(self, job_id: int) -> None:
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            match = await session.get(Match, job.match_id)
            if job.cancel_requested or match is None:
                await self._finish(job_id, "cancelled")
                return
            kind, match_id, params = job.kind, job.match_id, job.params or {}
            if drives_match_status(kind):
                match.status = "processing"
                match.progress = 0.0
            await session.commit()

        progress = {"value": 0.0}
        cancel = threading.Event()

        def on_progress(value: float) -> None:
            # Runs in the analysis thread: record progress, abort if asked.
            progress["value"] = value
            if cancel.is_set():
                raise JobCancelledError

        heartbeat = asyncio.create_task(self._heartbeat(job_id, match_id, progress, cancel))
        try:
            await JOB_HANDLERS[kind](match_id, on_progress, params)
        except JobCancelledError:
            await self._finish(job_id, "cancelled")
        except asyncio.CancelledError:
            # Worker shutting down: hand the job back to the queue untouched.
            await asyncio.shield(self._release(job_id))
            raise
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
            logger.exception("Job %s (%s, match %s) failed", job_id, kind, match_id)
            await self._fail_or_retry(job_id, exc)
        else:
            await self._finish(job_id, "done")
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, job_id: int, match_id: int, progress: dict, cancel: threading.Event) -> None:
        while True:
            await asyncio.sleep(settings.job_heartbeat_s)
            try:
                async with async_session_maker() as session:
                    job = await session.get(Job, job_id)
                    match = await session.get(Match, match_id)
                    if job is None or match is None:
                        cancel.set()  # match deleted under us
                        return
                    if job.cancel_requested:
                        cancel.set()
                    job.heartbeat_at = datetime.utcnow()
                    job.progress = round(progress["value"], 3)
                    if drives_match_status(job.kind) and match.status == "processing":
                        match.progress = round(progress["value"], 3)
                    await session.commit()
            except Exception:  # noqa: BLE001 - keep beating through transient DB errors
                logger.exception("Heartbeat failed for job %s", job_id)

    async def _finish(self, job_id: int, status: str) -> None:
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.finished_at = datetime.utcnow()
            if status == "done":
                job.progress = 1.0
            match = await session.get(Match, job.match_id) if drives_match_status(job.kind) else None
            if match is not None and status == "cancelled":
                match.status = "cancelled"
            await session.commit()

    async def _release(self, job_id: int) -> None:
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            job.status = "queued"
            job.worker_id = None
            job.attempts = max(0, job.attempts - 1)  # an interrupted run doesn't count
            match = await session.get(Match, job.match_id) if drives_match_status(job.kind) else None
            if match is not None:
                match.status = "pending"
            await session.commit()

    async def _fail_or_retry(self, job_id: int, exc: Exception) -> None:
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            match = await session.get(Match, job.match_id) if drives_match_status(job.kind) else None
            message = str(exc) or exc.__class__.__name__
            job.error = message[:1000]
            retry = not isinstance(exc, NON_RETRYABLE) and job.attempts < job.max_attempts
            if retry:
                job.status = "queued"
                job.worker_id = None
                if match is not None:
                    match.status = "pending"
                    match.error_message = f"Reintentando tras un error: {message}"[:1000]
            else:
                job.status = "failed"
                job.finished_at = datetime.utcnow()
                if match is not None:
                    match.status = "failed"
                    match.error_message = message[:1000]
            await session.commit()
