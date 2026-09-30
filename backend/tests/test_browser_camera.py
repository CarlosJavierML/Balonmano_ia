import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import async_session_maker
from app.main import app
from app.models import Job
from app.vision import live as live_module
from app.vision.detector import COCO_PERSON_CLASS, Detection, FrameDetections
from app.worker.runner import Worker


class MovingPersonDetector:
    """One person whose box follows the bright square drawn in each frame."""

    def detect(self, frame):
        ys, xs = np.nonzero(frame[:, :, 1] > 200)
        if len(xs) == 0:
            return FrameDetections(persons=[], balls=[])
        box = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
        return FrameDetections(persons=[Detection(box, 0.9, COCO_PERSON_CLASS)], balls=[])


def _jpeg(x: int) -> bytes:
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[100:180, x : x + 20] = (60, 255, 60)
    return cv2.imencode(".jpg", frame)[1].tobytes()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(live_module, "Detector", MovingPersonDetector)
    with TestClient(app) as c:

        async def clear_queue():
            async with async_session_maker() as session:
                await session.execute(delete(Job))
                await session.commit()

        c.portal.call(clear_queue)
        yield c


def test_phone_camera_frames_are_analyzed_live_and_saved(client):
    res = client.post("/matches/live/start", json={"name": "Móvil", "court_type": "piso", "source": "browser"})
    assert res.status_code == 200, res.text
    match = res.json()
    assert match["live_source"] == "browser" and match["status"] == "processing"
    match_id = match["id"]

    for i in range(12):  # 2 s at 6 fps, the player running right at ~13 km/h
        r = client.post(
            f"/matches/live/{match_id}/frame",
            params={"t": i / 6},
            content=_jpeg(20 + i * 5),
            headers={"Content-Type": "image/jpeg"},
        )
        # The server answers once the frame is analyzed: sending sequentially
        # (like the page does) never loses frames.
        assert r.status_code == 200 and r.json()["frames_processed"] == i + 1

    snap = client.get(f"/matches/live/{match_id}/snapshot").json()
    assert snap["source_connected"] and snap["source_error"] is None
    assert len(snap["player_stats"]) == 1 and snap["player_stats"][0]["distance_m"] > 5

    stop = client.post(f"/matches/live/{match_id}/stop")
    assert stop.status_code == 200 and stop.json()["status"] == "pending"
    client.portal.call(Worker().run_until_empty)

    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "done", detail["error_message"]
    assert detail["live_source"] == "browser"
    assert len(detail["player_stats"]) == 1


def test_frame_endpoint_rejects_bad_requests(client):
    assert client.post("/matches/live/999999/frame", content=_jpeg(10)).status_code == 404

    match_id = client.post(
        "/matches/live/start", json={"name": "Móvil", "court_type": "piso", "source": "browser"}
    ).json()["id"]
    assert client.post(f"/matches/live/{match_id}/frame", content=b"").status_code == 400
    # Undecodable frames are accepted but ignored, without breaking the session.
    assert client.post(f"/matches/live/{match_id}/frame", content=b"not a jpeg").status_code == 200
    ok = client.post(f"/matches/live/{match_id}/frame", params={"t": 0.5}, content=_jpeg(40))
    assert ok.json()["frames_processed"] == 1
    # A frame older than the last analyzed one (network reordering) is skipped.
    late = client.post(f"/matches/live/{match_id}/frame", params={"t": 0.2}, content=_jpeg(60))
    assert late.json()["frames_processed"] == 1
    client.post(f"/matches/live/{match_id}/stop")


def test_rtsp_sessions_do_not_accept_pushed_frames(client, tmp_path):
    path = tmp_path / "cam.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (64, 48))
    for _ in range(10):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()
    match_id = client.post(
        "/matches/live/start", json={"name": "IP", "court_type": "piso", "stream_url": str(path)}
    ).json()["id"]

    assert client.get(f"/matches/{match_id}").json()["live_source"] == "rtsp"
    assert client.post(f"/matches/live/{match_id}/frame", content=_jpeg(10)).status_code == 409
    client.post(f"/matches/live/{match_id}/stop")


def test_model_is_loaded_before_the_first_frame(client, monkeypatch):
    warmed = []

    class SlowDetector(MovingPersonDetector):
        def warm_up(self):
            warmed.append(True)

    monkeypatch.setattr(live_module, "Detector", SlowDetector)
    match_id = client.post(
        "/matches/live/start", json={"name": "Móvil", "court_type": "piso", "source": "browser"}
    ).json()["id"]
    r = client.post(f"/matches/live/{match_id}/frame", params={"t": 0.0}, content=_jpeg(40))
    assert r.json()["frames_processed"] == 1
    assert warmed == [True]
    assert client.get(f"/matches/live/{match_id}/snapshot").json()["ai_ready"] is True
    client.post(f"/matches/live/{match_id}/stop")
