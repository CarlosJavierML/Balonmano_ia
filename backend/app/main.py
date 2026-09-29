import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
