import asyncio
import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import async_session_maker
from app.main import app
from app.models import Job, Match, PlayerMatchStat
from app.worker import tasks
from app.worker.queue import claim_next_job, requeue_stale_jobs
from app.worker.runner import Worker
from tests.test_api import _synthetic_result


class FakePipeline:
    """Stands in for the YOLO pipeline: reports progress, then returns
    synthetic trajectories. Behaviour is controlled via class attributes."""

    calls = 0
    fail_times = 0
    fail_with: type[Exception] = RuntimeError
    steps = 3
    step_delay_s = 0.0
    started = threading.Event()

    def analyze(self, video_path, court, calibration, on_progress):
        FakePipeline.calls += 1
        FakePipeline.started.set()
        if FakePipeline.fail_times > 0:
            FakePipeline.fail_times -= 1
            raise FakePipeline.fail_with("fallo simulado")
        for i in range(FakePipeline.steps):
            time.sleep(FakePipeline.step_delay_s)
            on_progress((i + 1) / FakePipeline.steps)
        return _synthetic_result()


@pytest.fixture(autouse=True)
def fake_pipeline(monkeypatch):
    FakePipeline.calls = 0
    FakePipeline.fail_times = 0
    FakePipeline.fail_with = RuntimeError
    FakePipeline.steps = 3
    FakePipeline.step_delay_s = 0.0
    FakePipeline.started = threading.Event()
    monkeypatch.setattr(tasks, "VideoAnalysisPipeline", FakePipeline)
    monkeypatch.setattr(settings, "job_heartbeat_s", 0.05)
    monkeypatch.setattr(settings, "worker_poll_s", 0.05)
    monkeypatch.setattr(settings, "job_max_attempts", 2)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        # The test database is shared across tests: start each one with an
        # empty queue so leftovers from another test are never processed here.
        async def clear_queue():
            async with async_session_maker() as session:
                await session.execute(delete(Job))
                await session.commit()

        c.portal.call(clear_queue)
        yield c


def _upload(client, name="Entreno") -> int:
    res = client.post(
        "/matches/upload",
        data={"name": name, "court_type": "piso"},
        files={"file": ("clip.mp4", b"not-a-real-video", "video/mp4")},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _drain(client):
    client.portal.call(Worker().run_until_empty)


def _job_for(client, match_id) -> Job:
    async def get():
        async with async_session_maker() as session:
            return await session.scalar(select(Job).where(Job.match_id == match_id).order_by(Job.id.desc()))

    return client.portal.call(get)


def test_upload_is_queued_and_processed_in_order(client):
    first, second = _upload(client, "Primero"), _upload(client, "Segundo")

    assert client.get(f"/matches/{first}").json()["status"] == "pending"
    queue = client.get("/jobs").json()
    assert [(j["match_name"], j["status"], j["position"]) for j in queue][-2:] == [
        ("Primero", "queued", queue[-2]["position"]),
        ("Segundo", "queued", queue[-2]["position"] + 1),
    ]
    detail = client.get(f"/matches/{second}").json()
    assert detail["active_job"]["position"] == queue[-1]["position"]

    _drain(client)

    for match_id in (first, second):
        detail = client.get(f"/matches/{match_id}").json()
        assert detail["status"] == "done"
        assert detail["progress"] == 1.0
        assert detail["active_job"] is None
        assert len(detail["player_stats"]) == 3
    assert _job_for(client, first).finished_at <= _job_for(client, second).finished_at
    assert client.get("/jobs").json() == []
    finished = client.get("/jobs", params={"include_finished": True}).json()
    assert {j["match_id"] for j in finished} >= {first, second}


def test_cancel_queued_job(client):
    match_id = _upload(client)

    assert client.post(f"/matches/{match_id}/cancel").json()["status"] == "cancelled"
    assert client.get(f"/matches/{match_id}").json()["status"] == "cancelled"
    _drain(client)
    assert FakePipeline.calls == 0
    assert client.post(f"/matches/{match_id}/cancel").status_code == 409


def test_cancel_running_job_stops_the_analysis(client):
    FakePipeline.steps = 200
    FakePipeline.step_delay_s = 0.01
    match_id = _upload(client)

    async def run_and_cancel():
        worker_task = asyncio.create_task(Worker().run_until_empty())
        await asyncio.to_thread(FakePipeline.started.wait, 5)
        async with async_session_maker() as session:
            job = await session.scalar(select(Job).where(Job.match_id == match_id))
            job.cancel_requested = True
            await session.commit()
        await asyncio.wait_for(worker_task, timeout=5)

    started = time.monotonic()
    client.portal.call(run_and_cancel)

    assert time.monotonic() - started < 1.5  # didn't run all 200 steps (2s)
    assert client.get(f"/matches/{match_id}").json()["status"] == "cancelled"
    assert _job_for(client, match_id).status == "cancelled"


def test_transient_failure_is_retried(client):
    FakePipeline.fail_times = 1
    match_id = _upload(client)

    _drain(client)

    job = _job_for(client, match_id)
    assert (job.status, job.attempts) == ("done", 2)
    assert client.get(f"/matches/{match_id}").json()["status"] == "done"


def test_failures_after_last_attempt_mark_the_match_failed(client):
    FakePipeline.fail_times = 5
    match_id = _upload(client)

    _drain(client)

    job = _job_for(client, match_id)
    assert (job.status, job.attempts) == ("failed", 2)
    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "failed" and "fallo simulado" in detail["error_message"]


def test_non_retryable_failure_is_not_retried(client):
    FakePipeline.fail_times = 1
    FakePipeline.fail_with = ValueError  # e.g. "Could not open video file"
    match_id = _upload(client)

    _drain(client)

    job = _job_for(client, match_id)
    assert (job.status, job.attempts) == ("failed", 1)


def test_orphaned_running_job_is_requeued_then_failed(client):
    match_id = _upload(client)

    async def simulate_crashed_worker(attempts: int):
        async with async_session_maker() as session:
            job = await session.scalar(select(Job).where(Job.match_id == match_id))
            job.status = "running"
            job.attempts = attempts
            job.heartbeat_at = datetime.utcnow() - timedelta(seconds=settings.job_stale_after_s + 1)
            await session.commit()
            await requeue_stale_jobs(session)
            await session.refresh(job)
            return job.status

    assert client.portal.call(simulate_crashed_worker, 1) == "queued"
    assert client.get(f"/matches/{match_id}").json()["status"] == "pending"
    assert client.portal.call(simulate_crashed_worker, 2) == "failed"
    assert client.get(f"/matches/{match_id}").json()["status"] == "failed"


def test_two_workers_never_claim_the_same_job(client):
    match_id = _upload(client)

    async def race():
        async def claim(worker_id):
            async with async_session_maker() as session:
                job = await claim_next_job(session, worker_id)
                return job.id if job else None

        return await asyncio.gather(*(claim(f"w{i}") for i in range(5)))

    claimed = [j for j in client.portal.call(race) if j is not None]
    assert claimed == [_job_for(client, match_id).id]


def test_reanalyze_replaces_previous_results(client):
    match_id = _upload(client)
    _drain(client)
    client.patch(f"/matches/{match_id}/players/1", json={"team": "B"})

    res = client.post(f"/matches/{match_id}/reanalyze")
    assert res.status_code == 200 and res.json()["kind"] == "video"
    assert client.post(f"/matches/{match_id}/reanalyze").status_code == 409  # already queued
    _drain(client)

    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "done"
    assert len(detail["player_stats"]) == 3  # replaced, not duplicated
    assert FakePipeline.calls == 2

    async def overrides():
        async with async_session_maker() as session:
            return (await session.get(Match, match_id)).overrides

    assert client.portal.call(overrides) is None  # new tracks: old corrections dropped


def test_reanalyze_without_video_uses_saved_trajectories(client):
    match_id = _upload(client)
    _drain(client)

    async def remove_video():
        async with async_session_maker() as session:
            match = await session.get(Match, match_id)
            from pathlib import Path

            Path(match.video_path).unlink()

    client.portal.call(remove_video)
    assert client.post(f"/matches/{match_id}/reanalyze").json()["kind"] == "tracking"
    _drain(client)

    assert client.get(f"/matches/{match_id}").json()["status"] == "done"
    assert FakePipeline.calls == 1  # vision pipeline not run again


def test_running_job_blocks_deletion(client):
    match_id = _upload(client)

    async def mark_running():
        async with async_session_maker() as session:
            await claim_next_job(session, "test-worker")

    client.portal.call(mark_running)
    assert client.delete(f"/matches/{match_id}").status_code == 409


def test_worker_loop_processes_jobs_and_stops(client):
    ids = [_upload(client, f"S{i}") for i in range(3)]

    async def run_loop():
        stop = asyncio.Event()
        task = asyncio.create_task(Worker(concurrency=2).run(stop))
        for _ in range(200):
            async with async_session_maker() as session:
                pending = await session.scalar(select(Job).where(Job.status.in_(("queued", "running"))))
            if pending is None:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    client.portal.call(run_loop)
    assert all(client.get(f"/matches/{i}").json()["status"] == "done" for i in ids)

    async def stat_count():
        async with async_session_maker() as session:
            return len((await session.execute(select(PlayerMatchStat).where(PlayerMatchStat.match_id.in_(ids)))).all())

    assert client.portal.call(stat_count) == 9
