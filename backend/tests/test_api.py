from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from app.analysis.teams import jersey_color_feature
from app.db import _add_missing_columns, async_session_maker
from app.main import app
from app.models import Event, Match, PlayerMatchStat
from app.vision.pipeline import BallFrame, TrackFrame, TrackingResult
from app.worker.tasks import _persist_analysis


def test_health_and_empty_match_list():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        matches = client.get("/matches")
        assert matches.status_code == 200
        assert matches.json() == []


def test_upload_rejects_unsupported_court_type():
    with TestClient(app) as client:
        response = client.post(
            "/matches/upload",
            data={"name": "Entreno", "court_type": "futbol"},
            files={"file": ("clip.mp4", b"fake-bytes", "video/mp4")},
        )
        assert response.status_code == 400


def _synthetic_result() -> TrackingResult:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[:, :50] = (30, 30, 220)  # red jersey (BGR)
    frame[:, 50:] = (220, 60, 30)  # blue jersey
    red = jersey_color_feature(frame, (0, 0, 50, 100))
    blue = jersey_color_feature(frame, (50, 0, 100, 100))
    result = TrackingResult(fps=2, duration_s=3.0, calibrated=True)
    for i in range(7):
        t = i * 0.5
        result.player_frames += [
            TrackFrame(t=t, track_id=1, x=5.0 + 0.5 * i, y=5.0),
            TrackFrame(t=t, track_id=2, x=10.0, y=5.0),
            TrackFrame(t=t, track_id=3, x=15.0, y=5.0),
        ]
        ball_x = 5.1 + 0.5 * i if i < 2 else (10.1 if i < 4 else 15.1)
        result.ball_frames.append(BallFrame(t=t, x=ball_x, y=5.0))
    result.color_samples = {1: [red] * 5, 2: [red] * 5, 3: [blue] * 5}
    return result


def test_persisted_analysis_exposes_teams_report_and_deletes_cleanly():
    with TestClient(app) as client:

        async def create_and_persist() -> int:
            async with async_session_maker() as session:
                match = Match(name="Equipos", court_type="piso", source_mode="upload", status="processing")
                session.add(match)
                await session.commit()
                match_id = match.id
            await _persist_analysis(match_id, _synthetic_result())
            return match_id

        match_id = client.portal.call(create_and_persist)

        detail = client.get(f"/matches/{match_id}").json()
        assert detail["status"] == "done"
        assert detail["progress"] == 1.0
        teams = {p["track_id"]: p["team"] for p in detail["player_stats"]}
        assert teams[1] == teams[2] != teams[3]
        assert set(detail["team_summary"]) == {"A", "B"}
        colors = {detail["team_summary"][t]["color"] for t in ("A", "B")}
        red_team = detail["team_summary"][teams[1]]["color"]
        assert len(colors) == 2 and int(red_team[1:3], 16) > 180  # red channel dominates
        assert [e["event_type"] for e in detail["events"]] == ["pase", "perdida"]

        report = client.get(f"/matches/{match_id}/report")
        assert report.status_code == 200
        assert report.content.startswith(b"%PDF")

        heatmap_paths = [p["heatmap_path"] for p in detail["player_stats"]]
        assert all(Path(p).exists() for p in heatmap_paths)

        assert client.delete(f"/matches/{match_id}").status_code == 204
        assert client.get(f"/matches/{match_id}").status_code == 404
        assert not any(Path(p).exists() for p in heatmap_paths)

        async def count_orphans() -> int:
            async with async_session_maker() as session:
                stats = await session.execute(select(PlayerMatchStat).where(PlayerMatchStat.match_id == match_id))
                events = await session.execute(select(Event).where(Event.match_id == match_id))
                return len(stats.all()) + len(events.all())

        assert client.portal.call(count_orphans) == 0


def test_missing_columns_are_added_to_old_databases(tmp_path):
    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        # Schema from before the "progress" / "team_summary" / "team" columns existed.
        conn.execute(text("CREATE TABLE matches (id INTEGER PRIMARY KEY, name VARCHAR(200))"))
        conn.execute(text("INSERT INTO matches (id, name) VALUES (1, 'antigua')"))
        conn.execute(text("CREATE TABLE player_match_stats (id INTEGER PRIMARY KEY, match_id INTEGER)"))
        _add_missing_columns(conn)

    columns = {c["name"] for c in inspect(engine).get_columns("matches")}
    assert {"progress", "team_summary"} <= columns
    assert "team" in {c["name"] for c in inspect(engine).get_columns("player_match_stats")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT progress FROM matches WHERE id = 1")).scalar() == 0.0


def test_live_preview_returns_jpeg_from_a_video_source(tmp_path):
    import cv2

    video_path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 48))
    for _ in range(3):
        writer.write(np.full((48, 64, 3), 128, dtype=np.uint8))
    writer.release()

    with TestClient(app) as client:
        ok = client.post("/matches/live/preview", json={"stream_url": str(video_path)})
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "image/jpeg"
        assert ok.content[:2] == b"\xff\xd8"

        bad = client.post("/matches/live/preview", json={"stream_url": str(tmp_path / "nope.mp4")})
        assert bad.status_code == 400
