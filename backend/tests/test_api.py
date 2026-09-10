from fastapi.testclient import TestClient

from app.main import app


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
