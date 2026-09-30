from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import settings
from app.db import async_session_maker
from app.main import app
from app.models import Job, Match
from app.vision.audio_sync import ffmpeg_executable
from app.worker import tasks
from app.worker.runner import Worker
from tests.test_queue import FakePipeline


@pytest.fixture()
def client(monkeypatch):
    FakePipeline.calls = 0
    FakePipeline.fail_times = 0
    FakePipeline.steps = 1
    FakePipeline.step_delay_s = 0.0
    monkeypatch.setattr(tasks, "VideoAnalysisPipeline", FakePipeline)
    monkeypatch.setattr(settings, "job_heartbeat_s", 0.05)
    with TestClient(app) as c:

        async def clear_queue():
            async with async_session_maker() as session:
                await session.execute(delete(Job))
                await session.commit()

        c.portal.call(clear_queue)
        yield c


def _analyzed_match(client) -> int:
    match_id = client.post(
        "/matches/upload",
        data={"name": "Recreación", "court_type": "piso"},
        files={"file": ("clip.mp4", b"x", "video/mp4")},
    ).json()["id"]
    client.portal.call(Worker().run_until_empty)
    assert client.get(f"/matches/{match_id}").json()["status"] == "done"
    return match_id


def test_replay_data_has_trajectories_teams_and_events(client):
    match_id = _analyzed_match(client)
    client.patch(f"/matches/{match_id}/teams", json={"names": {"A": "Cadete A"}})

    res = client.get(f"/matches/{match_id}/replay", headers={"Accept-Encoding": "gzip"})
    assert res.status_code == 200
    replay = res.json()

    assert replay["court"]["length_m"] == 40 and replay["court"]["width_m"] == 20
    assert len(replay["players"]) == 3
    runner = next(p for p in replay["players"] if p["track_id"] == 1)
    assert runner["label"] == "Jugador #1" and runner["team"] in ("A", "B")
    assert runner["color"].startswith("#")
    assert runner["points"][0] == [0.0, 5.0, 5.0]  # [t, x, y]
    assert len(replay["ball"]) == 7
    assert [e["type"] for e in replay["events"]] == ["pase", "perdida"]
    assert "Cadete A" in {t["name"] for t in replay["teams"].values()}


def test_replay_needs_a_finished_analysis_with_trajectories(client):
    async def old_match() -> int:
        async with async_session_maker() as session:
            match = Match(name="Antigua", court_type="piso", source_mode="upload", status="done")
            session.add(match)
            await session.commit()
            return match.id

    match_id = client.portal.call(old_match)
    assert client.get(f"/matches/{match_id}/replay").status_code == 409
    assert client.post(f"/matches/{match_id}/replay-video", json={"speed": 1}).status_code == 409
    assert client.get("/matches/999999/replay").status_code == 404


@pytest.mark.skipif(ffmpeg_executable() is None, reason="ffmpeg not available")
def test_replay_video_is_rendered_without_touching_the_match_status(client):
    match_id = _analyzed_match(client)
    assert client.post(f"/matches/{match_id}/replay-video", json={"speed": 3}).status_code == 422

    job = client.post(f"/matches/{match_id}/replay-video", json={"speed": 2}).json()
    assert job["kind"] == "replay_video"
    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "done"  # stats stay visible while rendering
    assert detail["active_job"]["kind"] == "replay_video"
    assert client.get(f"/matches/{match_id}/replay-video").status_code == 404

    client.portal.call(Worker().run_until_empty)

    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "done" and detail["active_job"] is None
    assert detail["replay_video"]["speed"] == 2 and "path" not in detail["replay_video"]
    video = client.get(f"/matches/{match_id}/replay-video")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert video.content[4:8] == b"ftyp"

    async def video_path() -> str:
        async with async_session_maker() as session:
            return (await session.get(Match, match_id)).replay_video["path"]

    path = Path(client.portal.call(video_path))
    # A new analysis makes the old recreation obsolete: it's removed.
    client.post(f"/matches/{match_id}/reanalyze")
    client.portal.call(Worker().run_until_empty)
    assert client.get(f"/matches/{match_id}").json()["replay_video"] is None
    assert not path.exists()


def test_failed_render_does_not_fail_the_match(client, monkeypatch):
    match_id = _analyzed_match(client)

    def broken_render(*args, **kwargs):
        raise ValueError("sin ffmpeg")

    monkeypatch.setattr(tasks, "render_replay_video", broken_render)
    client.post(f"/matches/{match_id}/replay-video", json={"speed": 1})
    client.portal.call(Worker().run_until_empty)

    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "done" and detail["error_message"] is None
    finished = client.get("/jobs", params={"include_finished": True}).json()
    render_job = next(j for j in finished if j["kind"] == "replay_video" and j["match_id"] == match_id)
    assert render_job["status"] == "failed" and "sin ffmpeg" in render_job["error"]
