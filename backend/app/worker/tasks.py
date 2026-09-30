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
from app.court import CourtCalibration, CourtConfig, CourtRegion, CourtType, get_court_config
from app.db import async_session_maker
from app.models import Camera, Event, Match, PlayerMatchStat
from app.reports.pdf_report import render_heatmap_image
from app.vision.audio_sync import estimate_offset, extract_envelope
from app.vision.fusion import CameraTracking, fuse_cameras
from app.vision.pipeline import TrackingResult, VideoAnalysisPipeline

logger = logging.getLogger(__name__)


def build_calibration(court: CourtConfig, calibration: dict | None) -> CourtCalibration | None:
    if not calibration:
        return None
    corners = [(c["x"], c["y"]) for c in calibration["corners"]]
    return CourtCalibration(court, corners, CourtRegion(calibration.get("region", "full")))


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


def tracking_path_for(match_id: int, camera_index: int | None = None) -> Path:
    """Saved trajectories of a match (fused, for multi-camera), or of one of
    its cameras before fusion."""
    suffix = "" if camera_index is None else f"_cam_{camera_index}"
    return TRACKING_DIR / f"match_{match_id}{suffix}.json.gz"


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


async def persist_analysis(
    match_id: int, result: TrackingResult, *, new_tracks: bool, fusion_report: dict | None = None
) -> None:
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
        match.fusion_report = fusion_report
        match.team_summary = analysis.team_summary or None
        match.progress = 1.0
        match.status = "done"
        match.error_message = None
        await session.commit()


async def _load_cameras(match_id: int) -> tuple[Match, list[Camera]] | None:
    async with async_session_maker() as session:
        match = await session.scalar(
            select(Match).where(Match.id == match_id).options(selectinload(Match.cameras))
        )
        if match is None:
            return None
        return match, list(match.cameras)


async def run_video_job(match_id: int, on_progress: Callable[[float], None]) -> None:
    """Runs the vision pipeline on the match's uploaded video(s).

    ``on_progress`` is called from the analysis thread; it may raise to
    abort the analysis (that's how cancellation reaches the pipeline)."""
    loaded = await _load_cameras(match_id)
    if loaded is None:
        return
    match, cameras = loaded
    court = get_court_config(CourtType(match.court_type))
    pipeline = VideoAnalysisPipeline()

    if not cameras:  # single-camera session
        if not match.video_path or not Path(match.video_path).exists():
            raise FileNotFoundError("El vídeo de esta sesión ya no existe en el servidor")
        calibration = build_calibration(court, match.calibration)
        result = await asyncio.to_thread(pipeline.analyze, match.video_path, court, calibration, on_progress)
        await persist_analysis(match_id, result, new_tracks=True)
        return

    for cam in cameras:
        if not cam.video_path or not Path(cam.video_path).exists():
            raise FileNotFoundError(f"El vídeo de la cámara «{cam.name or cam.index + 1}» ya no existe en el servidor")

    # Vision on each camera in turn (95% of the progress bar), then fusion.
    n = len(cameras)
    for i, cam in enumerate(cameras):

        def camera_progress(value: float, i: int = i) -> None:
            on_progress(0.95 * (i + value) / n)

        result = await asyncio.to_thread(
            pipeline.analyze,
            cam.video_path,
            court,
            build_calibration(court, cam.calibration),
            camera_progress,
            CourtRegion(cam.region),
        )
        path = tracking_path_for(match_id, cam.index)
        await asyncio.to_thread(result.save, path)
        async with async_session_maker() as session:
            row = await session.get(Camera, cam.id)
            row.tracking_path = str(path)
            await session.commit()

    await fuse_and_persist(match_id)


async def _resolve_offsets(cameras: list[Camera]) -> list[tuple[float, dict]]:
    """Session-time offset for each camera, plus how it was obtained.

    Camera 0 is the reference. A manual offset wins; live streams share the
    server clock; uploaded videos are synced from their audio (reusing a
    previous detection if there is one), falling back to 0."""
    reference_envelope = None
    resolved: list[tuple[float, dict]] = []
    for cam in cameras:
        if cam.index == cameras[0].index:
            resolved.append((0.0, {"method": "reference", "offset_s": 0.0}))
        elif cam.time_offset_s is not None:
            resolved.append((cam.time_offset_s, {"method": "manual", "offset_s": cam.time_offset_s}))
        elif cam.stream_url:
            resolved.append((0.0, {"method": "clock", "offset_s": 0.0}))
        elif cam.sync_info and cam.sync_info.get("method") == "audio":
            resolved.append((cam.sync_info["offset_s"], cam.sync_info))
        else:
            if reference_envelope is None and cameras[0].video_path:
                reference_envelope = await asyncio.to_thread(extract_envelope, cameras[0].video_path)
            envelope = await asyncio.to_thread(extract_envelope, cam.video_path) if cam.video_path else None
            if reference_envelope is None or envelope is None:
                resolved.append((0.0, {"method": "none", "offset_s": 0.0, "reason": "sin audio"}))
                continue
            estimate = estimate_offset(reference_envelope, envelope)
            info = {"offset_s": round(estimate.offset_s, 3), "confidence": round(estimate.confidence, 1)}
            if estimate.reliable:
                resolved.append((estimate.offset_s, {"method": "audio", **info}))
            else:
                resolved.append(
                    (
                        0.0,
                        {
                            "method": "none",
                            "offset_s": 0.0,
                            "detected_offset_s": info["offset_s"],
                            "confidence": info["confidence"],
                            "reason": "audio no concluyente",
                        },
                    )
                )
    return resolved


async def fuse_and_persist(match_id: int) -> None:
    """Syncs and fuses the saved per-camera trajectories, then analyzes the
    result like a single-camera session. Cheap: no vision models involved,
    so it's also what runs after the user changes a camera's sync."""
    loaded = await _load_cameras(match_id)
    if loaded is None:
        return
    match, cameras = loaded
    court = get_court_config(CourtType(match.court_type))

    trackings = []
    for cam in cameras:
        if not cam.tracking_path or not Path(cam.tracking_path).exists():
            raise FileNotFoundError(f"Faltan las trayectorias de la cámara «{cam.name or cam.index + 1}»")
        trackings.append(await asyncio.to_thread(TrackingResult.load, Path(cam.tracking_path)))

    offsets = await _resolve_offsets(cameras)
    async with async_session_maker() as session:
        for cam, (_, info) in zip(cameras, offsets):
            row = await session.get(Camera, cam.id)
            row.sync_info = info
        await session.commit()

    fused, report = await asyncio.to_thread(
        fuse_cameras,
        [
            CameraTracking(cam.index, CourtRegion(cam.region), offset, result)
            for cam, (offset, _), result in zip(cameras, offsets, trackings)
        ],
        court,
    )
    await persist_analysis(match_id, fused, new_tracks=True, fusion_report=report.as_dict())


async def run_tracking_job(match_id: int, on_progress: Callable[[float], None]) -> None:
    """Analyzes trajectories already saved to disk -- a live session that was
    just stopped, a re-analysis without the video, or a multi-camera re-sync.
    No vision models involved."""
    loaded = await _load_cameras(match_id)
    if loaded is None:
        return
    _, cameras = loaded
    on_progress(0.3)
    if cameras:
        await fuse_and_persist(match_id)
        return

    path = tracking_path_for(match_id)
    if not path.exists():
        raise FileNotFoundError("No se encontraron las trayectorias guardadas de esta sesión")
    result = await asyncio.to_thread(TrackingResult.load, path)
    await persist_analysis(match_id, result, new_tracks=False)


JOB_HANDLERS = {
    "video": run_video_job,
    "tracking": run_tracking_job,
}
