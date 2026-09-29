"""Analysis work executed by the queue worker (see `app.worker.runner`).

Everything here is plain async code over the database: the runner decides
*when* a job runs (queue order, concurrency, retries, cancellation), these
functions decide *what* running it means.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.analysis.session import SessionAnalysis, analyze_session
from app.config import HEATMAPS_DIR, TRACKING_DIR
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


def _event_rows(match_id: int, analysis: SessionAnalysis) -> list[Event]:
    return [
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
        for ev in analysis.events
    ]


class TrackingUnavailableError(Exception):
    """The session has no saved trajectories (analyzed by an older version)."""


def tracking_path_for(match_id: int) -> Path:
    return TRACKING_DIR / f"match_{match_id}.json.gz"


async def recompute_tactics(session: AsyncSession, match: Match) -> None:
    """Recomputes teams, roles, events and team summary from the saved
    trajectories, applying the match's manual overrides. Physical stats
    don't depend on teams, so stat rows are updated in place (keeping any
    custom player names). ``match`` must have player_stats and events loaded."""
    if not match.tracking_path or not Path(match.tracking_path).exists():
        raise TrackingUnavailableError
    result = await asyncio.to_thread(TrackingResult.load, Path(match.tracking_path))
    court = get_court_config(CourtType(match.court_type))
    analysis = analyze_session(result, court, match.overrides)

    for stat in match.player_stats:
        stat.team = analysis.team_by_track.get(stat.track_id)
        stat.role = analysis.role_by_track.get(stat.track_id)
    match.events.clear()
    match.events.extend(_event_rows(match.id, analysis))
    match.team_summary = analysis.team_summary or None


async def persist_analysis(match_id: int, result: TrackingResult, *, new_tracks: bool) -> None:
    """Analyzes trajectories and stores stats/events for a match, replacing
    any previous analysis. ``new_tracks`` means the vision pipeline ran
    again: track ids changed, so earlier manual corrections no longer apply."""
    async with async_session_maker() as session:
        match = await session.scalar(
            select(Match)
            .where(Match.id == match_id)
            .options(selectinload(Match.player_stats), selectinload(Match.events))
        )
        if match is None:
            return

        # Re-analysis: drop the previous results (and their heatmap images).
        for stat in match.player_stats:
            if stat.heatmap_path:
                Path(stat.heatmap_path).unlink(missing_ok=True)
        match.player_stats.clear()
        match.events.clear()
        if new_tracks:
            match.overrides = None

        tracking_path = tracking_path_for(match_id)
        if new_tracks or not tracking_path.exists():
            await asyncio.to_thread(result.save, tracking_path)
        match.tracking_path = str(tracking_path)

        court = get_court_config(CourtType(match.court_type))
        analysis = analyze_session(result, court, match.overrides)

        for track_id, stats in analysis.player_stats.items():
            heatmap_path = None
            if stats.heatmap_grid:
                path = HEATMAPS_DIR / f"match_{match_id}_track_{track_id}.png"
                render_heatmap_image(stats.heatmap_grid, path, title=f"Jugador #{track_id}")
                heatmap_path = str(path)

            match.player_stats.append(
                PlayerMatchStat(
                    match_id=match_id,
                    track_id=track_id,
                    label=f"Jugador #{track_id}",
                    team=analysis.team_by_track.get(track_id),
                    role=analysis.role_by_track.get(track_id),
                    distance_m=stats.distance_m,
                    avg_speed_kmh=stats.avg_speed_kmh,
                    max_speed_kmh=stats.max_speed_kmh,
                    sprint_count=stats.sprint_count,
                    time_in_zones=stats.time_in_zones,
                    heatmap_path=heatmap_path,
                )
            )

        match.events.extend(_event_rows(match_id, analysis))

        match.duration_s = result.duration_s
        match.team_summary = analysis.team_summary or None
        match.progress = 1.0
        match.status = "done"
        match.error_message = None
        await session.commit()


async def run_video_job(match_id: int, on_progress: Callable[[float], None]) -> None:
    """Runs the vision pipeline on the match's uploaded video.

    ``on_progress`` is called from the analysis thread; it may raise to
    abort the analysis (that's how cancellation reaches the pipeline)."""
    async with async_session_maker() as session:
        match = await session.get(Match, match_id)
        if match is None:
            return
        if not match.video_path or not Path(match.video_path).exists():
            raise FileNotFoundError("El vídeo de esta sesión ya no existe en el servidor")
        video_path = match.video_path
        court = get_court_config(CourtType(match.court_type))
        calibration = _build_calibration(match)

    pipeline = VideoAnalysisPipeline()
    result = await asyncio.to_thread(pipeline.analyze, video_path, court, calibration, on_progress)
    await persist_analysis(match_id, result, new_tracks=True)


async def run_tracking_job(match_id: int, on_progress: Callable[[float], None]) -> None:
    """Analyzes trajectories already saved to disk (e.g. a live session that
    was just stopped) -- no vision models involved."""
    path = tracking_path_for(match_id)
    if not path.exists():
        raise FileNotFoundError("No se encontraron las trayectorias guardadas de esta sesión")
    result = await asyncio.to_thread(TrackingResult.load, path)
    on_progress(0.5)
    await persist_analysis(match_id, result, new_tracks=False)


JOB_HANDLERS = {
    "video": run_video_job,
    "tracking": run_tracking_job,
}
