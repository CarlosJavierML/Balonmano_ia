import asyncio
import base64
import binascii
import contextlib
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes import calibration, jobs, matches, reports, stream, upload
from app.config import settings
from app.db import init_db
from app.worker.runner import Worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop = asyncio.Event()
    worker_task = None
    if settings.embedded_worker:
        worker_task = asyncio.create_task(Worker().run(stop))
    try:
        yield
    finally:
        stop.set()
        if worker_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(matches.router)
app.include_router(upload.router)
app.include_router(stream.router)
app.include_router(reports.router)
app.include_router(calibration.router)
app.include_router(jobs.router)


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


def _password_ok(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, _, password = decoded.partition(":")
    return secrets.compare_digest(password.encode(), settings.access_password.encode())


if settings.access_password:

    @app.middleware("http")
    async def require_password(request: Request, call_next):
        # /health stays open for the hosting platform's health checks.
        if request.url.path == "/health" or _password_ok(request.headers.get("authorization")):
            return await call_next(request)
        return JSONResponse(
            {"detail": "Se necesita la contraseña de acceso"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Balonmano IA", charset="UTF-8"'},
        )


if settings.frontend_dir:
    FRONTEND_DIR = Path(settings.frontend_dir).resolve()

    # Registered last: API routes above take precedence. Any other GET path
    # is a static file of the built dashboard, or a client-side route
    # (/sesiones/3, /nueva...) answered with index.html.
    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        candidate = (FRONTEND_DIR / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIR):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
