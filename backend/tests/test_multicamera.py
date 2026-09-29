import json
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.court import CourtRegion
from app.db import async_session_maker
from app.main import app
from app.models import Camera, Job
from app.vision import live as live_module
from app.vision.audio_sync import AUDIO_SAMPLE_RATE, ffmpeg_executable
from app.vision.detector import COCO_PERSON_CLASS, Detection, FrameDetections
from app.worker import tasks
from app.worker.runner import Worker
from tests.test_audio_sync import _audio
from tests.test_fusion import CAM1_OFFSET, _cams


class RegionPipeline:
    """Fake vision pipeline: returns the synthetic left/right-half camera
    trajectories from test_fusion depending on the camera's region."""

    calls: list[str] = []

    def analyze(self, video_path, court, calibration, on_progress, region=CourtRegion.FULL):
        RegionPipeline.calls.append(str(region.value))
        on_progress(1.0)
        left, right = _cams()
        return left.result if region == CourtRegion.LEFT else right.result


@pytest.fixture(autouse=True)
def fake_pipeline(monkeypatch):
    RegionPipeline.calls = []
    monkeypatch.setattr(tasks, "VideoAnalysisPipeline", RegionPipeline)
    monkeypatch.setattr(settings, "job_heartbeat_s", 0.05)


@pytest.fixture()
def client():
    with TestClient(app) as c:

        async def clear_queue():
            async with async_session_maker() as session:
                await session.execute(delete(Job))
                await session.commit()

        c.portal.call(clear_queue)
        yield c


def _drain(client):
    client.portal.call(Worker().run_until_empty)


def _upload_two(client, cameras, files=None):
    files = files or [("file", ("izq.mp4", b"a", "video/mp4")), ("file", ("der.mp4", b"b", "video/mp4"))]
    return client.post(
        "/matches/upload",
        data={"name": "Dos cámaras", "court_type": "piso", "cameras": json.dumps(cameras)},
        files=files,
    )


CORNERS = [{"x": 0, "y": 0}, {"x": 640, "y": 0}, {"x": 640, "y": 360}, {"x": 0, "y": 360}]


def test_two_camera_upload_is_analyzed_synced_and_fused(client):
    res = _upload_two(
        client,
        [
            {"name": "Mitad izquierda", "region": "left", "calibration": {"corners": CORNERS}},
            {"name": "Mitad derecha", "region": "right", "time_offset_s": CAM1_OFFSET},
        ],
    )
    assert res.status_code == 200, res.text
    match_id = res.json()["id"]

    _drain(client)

    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "done", detail["error_message"]
    assert sorted(RegionPipeline.calls) == ["left", "right"]
    assert [(c["name"], c["region"], c["calibrated"]) for c in detail["cameras"]] == [
        ("Mitad izquierda", "left", True),
        ("Mitad derecha", "right", False),
    ]
    assert detail["cameras"][0]["sync_info"]["method"] == "reference"
    assert detail["cameras"][1]["sync_info"] == {"method": "manual", "offset_s": CAM1_OFFSET}
    # Runner across both halves + player with an id switch + static player.
    assert detail["fusion_report"]["people"] == 3
    assert len(detail["player_stats"]) == 3
    assert detail["duration_s"] == pytest.approx(6.0)
    assert detail["editable"] is True
    assert client.get(f"/matches/{match_id}/report").status_code == 200


def test_changing_a_camera_offset_refuses_without_rerunning_vision(client):
    match_id = _upload_two(client, [{"region": "left"}, {"region": "right", "time_offset_s": CAM1_OFFSET}]).json()["id"]
    _drain(client)
    calls_after_first_run = len(RegionPipeline.calls)

    assert client.patch(f"/matches/{match_id}/cameras/0", json={"time_offset_s": 1}).status_code == 400
    res = client.patch(f"/matches/{match_id}/cameras/1", json={"time_offset_s": 30.0, "name": "Fondo"})
    assert res.status_code == 200
    assert res.json()["active_job"]["kind"] == "tracking"
    _drain(client)

    detail = client.get(f"/matches/{match_id}").json()
    assert len(RegionPipeline.calls) == calls_after_first_run  # no vision re-run
    assert detail["cameras"][1]["name"] == "Fondo"
    assert detail["cameras"][1]["sync_info"]["offset_s"] == 30.0
    # A 30 s offset pushes camera 1's footage far past camera 0's: the runner
    # can no longer be matched across cameras, so more "people" appear.
    assert detail["fusion_report"]["people"] > 3
    assert detail["duration_s"] == pytest.approx(34.0)


def test_upload_validation(client):
    bad = _upload_two(client, [{"region": "left"}])  # 2 files, 1 config
    assert bad.status_code == 400
    too_many = client.post(
        "/matches/upload",
        data={"name": "x", "court_type": "piso"},
        files=[("file", (f"c{i}.mp4", b"x", "video/mp4")) for i in range(5)],
    )
    assert too_many.status_code == 400
    assert _upload_two(client, [{"region": "middle"}, {}]).status_code == 400


def test_delete_removes_every_camera_file(client):
    match_id = _upload_two(client, [{"region": "left"}, {"region": "right", "time_offset_s": 2}]).json()["id"]
    _drain(client)

    async def camera_files():
        async with async_session_maker() as session:
            cams = (await session.execute(select(Camera).where(Camera.match_id == match_id))).scalars().all()
            return [p for c in cams for p in (c.video_path, c.tracking_path)]

    files = client.portal.call(camera_files)
    assert len(files) == 4 and all(Path(p).exists() for p in files)
    assert client.delete(f"/matches/{match_id}").status_code == 204
    assert not any(Path(p).exists() for p in files)
    assert client.portal.call(camera_files) == []


def _video_with_audio(path: Path, start_s: float, duration_s: float) -> None:
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(AUDIO_SAMPLE_RATE)
        w.writeframes(_audio(start_s, duration_s, gain=1.0).tobytes())
    subprocess.run(
        [ffmpeg_executable(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=green:s=64x48:d={duration_s}",
         "-i", str(wav), "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


@pytest.mark.skipif(ffmpeg_executable() is None, reason="ffmpeg not available")
def test_offset_is_detected_from_audio_when_not_given(client, tmp_path):
    cam0, cam1 = tmp_path / "cam0.mp4", tmp_path / "cam1.mp4"
    _video_with_audio(cam0, 0.0, 40)
    _video_with_audio(cam1, CAM1_OFFSET, 35)  # started recording 2 s later

    files = [
        ("file", ("cam0.mp4", cam0.read_bytes(), "video/mp4")),
        ("file", ("cam1.mp4", cam1.read_bytes(), "video/mp4")),
    ]
    match_id = _upload_two(client, [{"region": "left"}, {"region": "right"}], files).json()["id"]
    _drain(client)

    sync = client.get(f"/matches/{match_id}").json()["cameras"][1]["sync_info"]
    assert sync["method"] == "audio"
    assert sync["offset_s"] == pytest.approx(CAM1_OFFSET, abs=0.05)
    assert client.get(f"/matches/{match_id}").json()["fusion_report"]["people"] == 3

    # Manual override, then back to automatic.
    client.patch(f"/matches/{match_id}/cameras/1", json={"time_offset_s": 0})
    _drain(client)
    assert client.get(f"/matches/{match_id}").json()["cameras"][1]["sync_info"]["method"] == "manual"
    client.patch(f"/matches/{match_id}/cameras/1", json={"auto_sync": True})
    _drain(client)
    assert client.get(f"/matches/{match_id}").json()["cameras"][1]["sync_info"]["method"] == "audio"


class OnePersonDetector:
    def detect(self, frame):
        return FrameDetections(persons=[Detection((10.0, 10.0, 30.0, 60.0), 0.9, COCO_PERSON_CLASS)], balls=[])


def test_live_session_with_two_cameras(client, tmp_path, monkeypatch):
    import cv2

    monkeypatch.setattr(live_module, "Detector", OnePersonDetector)
    streams = []
    for i in range(2):
        path = tmp_path / f"stream{i}.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (64, 48))
        for _ in range(100):
            writer.write(np.full((48, 64, 3), 90, dtype=np.uint8))
        writer.release()
        streams.append(str(path))

    res = client.post(
        "/matches/live/start",
        json={
            "name": "Directo 2 cámaras",
            "court_type": "piso",
            "cameras": [
                {"stream_url": streams[0], "name": "Izq", "region": "left"},
                {"stream_url": streams[1], "name": "Der", "region": "right"},
            ],
        },
    )
    assert res.status_code == 200, res.text
    match_id = res.json()["id"]
    time.sleep(1.0)

    snap = client.get(f"/matches/live/{match_id}/snapshot")
    assert snap.status_code == 200
    stop = client.post(f"/matches/live/{match_id}/stop")
    assert stop.status_code == 200 and stop.json()["status"] == "pending"
    _drain(client)

    detail = client.get(f"/matches/{match_id}").json()
    assert detail["status"] == "done", detail["error_message"]
    assert [c["source"] for c in detail["cameras"]] == ["stream", "stream"]
    assert detail["cameras"][1]["sync_info"] == {"method": "clock", "offset_s": 0.0}  # shared clock
    assert RegionPipeline.calls == []  # live: no offline vision pass
