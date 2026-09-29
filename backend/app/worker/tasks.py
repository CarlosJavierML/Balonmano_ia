"""Background processing tasks.

Run via FastAPI's BackgroundTasks for this MVP (single-process, good enough
for a club running a handful of analyses at a time). For heavier concurrent
load, swap this module's entry points for Celery/RQ tasks behind a proper
job queue -- see docs/ROADMAP.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.analysis.session import analyze_session
from app.config import HEATMAPS_DIR
from app.court import CourtCalibration, CourtType, get_court_config
from app.db import async_session_maker
from app.models import Event, Match, PlayerMatchStat
from app.reports.pdf_report import render_heatmap_image
from app.vision.pipeline import TrackingResult, VideoAnalysisPipeline

logger = logging.getLogger(__name__)

PROGRESS_FLUSH_S = 2.0


def _build_calibration(match: Match) -> CourtCalibration | None:
    if not match.calibration:
        return None
    court = get_court_config(CourtType(match.court_type))
    corners = [(c["x"], c["y"]) for c in match.calibration["corners"]]
    return CourtCalibration(court, corners)


async def _stop_task(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _persist_analysis(match_id: int, result: TrackingResult) -> None:
    async with async_session_maker() as session:
        match = await session.get(Match, match_id)
        if match is None:
            return

        court = get_court_config(CourtType(match.court_type))
        analysis = analyze_session(result, court)

        for track_id, stats in analysis.player_stats.items():
            heatmap_path = None
            if stats.heatmap_grid:
                path = HEATMAPS_DIR / f"match_{match_id}_track_{track_id}.png"
                render_heatmap_image(stats.heatmap_grid, path, title=f"Jugador #{track_id}")
                heatmap_path = str(path)

            session.add(
                PlayerMatchStat(
                    match_id=match_id,
                    track_id=track_id,
                    label=f"Jugador #{track_id}",
                    team=analysis.team_by_track.get(track_id),
                    distance_m=stats.distance_m,
                    avg_speed_kmh=stats.avg_speed_kmh,
                    max_speed_kmh=stats.max_speed_kmh,
                    sprint_count=stats.sprint_count,
                    time_in_zones=stats.time_in_zones,
                    heatmap_path=heatmap_path,
                )
            )

        for ev in analysis.events:
            session.add(
                Event(
                    match_id=match_id,
                    event_type=ev.event_type,
                    timestamp_s=ev.t,
                    track_id_from=ev.track_id_from,
                    track_id_to=ev.track_id_to,
                    x=ev.x,
                    y=ev.y,
                    meta=ev.meta,
                )
            )

        match.duration_s = result.duration_s
        match.team_summary = analysis.team_summary or None
        match.progress = 1.0
        match.status = "done"
        await session.commit()


async def process_uploaded_video(match_id: int) -> None:
    async with async_session_maker() as session:
        match = await session.get(Match, match_id)
        if match is None or match.video_path is None:
            return
        match.status = "processing"
        video_path = match.video_path
        court = get_court_config(CourtType(match.court_type))
        calibration = _build_calibration(match)
        await session.commit()

    progress = {"value": 0.0}

    def on_progress(value: float) -> None:
        # Called from the analysis thread; a plain dict write is enough for
        # the async flusher below to pick it up.
        progress["value"] = value

    async def flush_progress() -> None:
        last_written = -1.0
        while True:
            await asyncio.sleep(PROGRESS_FLUSH_S)
            value = round(progress["value"], 3)
            if value == last_written:
                continue
            async with async_session_maker() as session:
                match = await session.get(Match, match_id)
                if match is not None and match.status == "processing":
                    match.progress = value
                    await session.commit()
            last_written = value

    flusher = asyncio.create_task(flush_progress())
    try:
        pipeline = VideoAnalysisPipeline()
        result = await asyncio.to_thread(pipeline.analyze, video_path, court, calibration, on_progress)
        await _stop_task(flusher)
        await _persist_analysis(match_id, result)
    except Exception as exc:  # noqa: BLE001 - surface any failure on the match record
        logger.exception("Video analysis failed for match %s", match_id)
        async with async_session_maker() as session:
            match = await session.get(Match, match_id)
            if match is not None:
                match.status = "failed"
                match.error_message = str(exc)
                await session.commit()
    finally:
        await _stop_task(flusher)


async def finalize_live_session(match_id: int, result: TrackingResult) -> None:
    """Persists the accumulated stats/events once a live stream is stopped."""
    try:
        await _persist_analysis(match_id, result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to finalize live session for match %s", match_id)
        async with async_session_maker() as session:
            match = await session.get(Match, match_id)
            if match is not None:
                match.status = "failed"
                match.error_message = str(exc)
                await session.commit()
