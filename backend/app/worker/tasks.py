"""Background processing tasks.

Run via FastAPI's BackgroundTasks for this MVP (single-process, good enough
for a club running a handful of analyses at a time). For heavier concurrent
load, swap this module's entry points for Celery/RQ tasks behind a proper
job queue -- see docs/ROADMAP.md.
"""

from __future__ import annotations

import asyncio
import logging

from app.analysis.physical import compute_player_stats
from app.analysis.tactical import detect_events
from app.config import HEATMAPS_DIR
from app.court import CourtCalibration, CourtType, get_court_config
from app.db import async_session_maker
from app.models import Event, Match, PlayerMatchStat
from app.reports.pdf_report import render_heatmap_image
from app.vision.pipeline import TrackingResult, VideoAnalysisPipeline

logger = logging.getLogger(__name__)


def _build_calibration(match: Match) -> CourtCalibration | None:
    if not match.calibration:
        return None
    court = get_court_config(CourtType(match.court_type))
    corners = [(c["x"], c["y"]) for c in match.calibration["corners"]]
    return CourtCalibration(court, corners)


async def _persist_analysis(match_id: int, result: TrackingResult) -> None:
    async with async_session_maker() as session:
        match = await session.get(Match, match_id)
        if match is None:
            return

        court = get_court_config(CourtType(match.court_type))
        player_stats = compute_player_stats(result.player_frames, court)
        events = detect_events(result.player_frames, result.ball_frames, court)

        for track_id, stats in player_stats.items():
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
                    distance_m=stats.distance_m,
                    avg_speed_kmh=stats.avg_speed_kmh,
                    max_speed_kmh=stats.max_speed_kmh,
                    sprint_count=stats.sprint_count,
                    time_in_zones=stats.time_in_zones,
                    heatmap_path=heatmap_path,
                )
            )

        for ev in events:
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

    try:
        pipeline = VideoAnalysisPipeline()
        result = await asyncio.to_thread(pipeline.analyze, video_path, court, calibration)
        await _persist_analysis(match_id, result)
    except Exception as exc:  # noqa: BLE001 - surface any failure on the match record
        logger.exception("Video analysis failed for match %s", match_id)
        async with async_session_maker() as session:
            match = await session.get(Match, match_id)
            if match is not None:
                match.status = "failed"
                match.error_message = str(exc)
                await session.commit()


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
