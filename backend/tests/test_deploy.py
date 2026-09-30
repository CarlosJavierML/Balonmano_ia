"""Single-container deployment features: serving the built dashboard and the
optional access password. Both are configured at import time, so each check
runs in a fresh Python process with its own environment."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run(script: str, tmp_path: Path, **env: str) -> None:
    full_env = {
        **os.environ,
        "BALONMANO_DATA_DIR": str(tmp_path / "data"),
        "BALONMANO_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/deploy.db",
        "BALONMANO_EMBEDDED_WORKER": "false",
        **env,
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=BACKEND_DIR,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_serves_built_frontend_next_to_the_api(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("no")

    _run(
        """
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            assert c.get("/").text == "<div id=root></div>"
            assert c.get("/sesiones/3").text == "<div id=root></div>"  # client-side route
            assert c.get("/assets/app.js").text == "console.log(1)"
            assert c.get("/assets/../../secret.txt").text != "no"  # no path traversal
            assert c.get("/matches").json() == []  # API still wins
            assert c.get("/health").json()["status"] == "ok"
        """,
        tmp_path,
        BALONMANO_FRONTEND_DIR=str(dist),
    )
    assert (tmp_path / "data" / "videos").is_dir()  # data dir from BALONMANO_DATA_DIR


def test_access_password_protects_everything_but_health(tmp_path):
    _run(
        """
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
            r = c.get("/matches")
            assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
            assert c.get("/matches", auth=("club", "mal")).status_code == 401
            assert c.get("/matches", auth=("cualquiera", "balon-2026")).status_code == 200
            assert c.get("/matches", headers={"Authorization": "Basic %%%"}).status_code == 401
        """,
        tmp_path,
        BALONMANO_ACCESS_PASSWORD="balon-2026",
    )
