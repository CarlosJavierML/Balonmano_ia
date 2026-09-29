import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.session import analyze_session
from app.court import CourtRegion, CourtType, get_court_config
from app.db import get_session
from app.models import Camera, Match
from app.schemas import LiveStatsSnapshot, LiveStreamStart, MatchOut, PlayerStatOut
from app.vision.live import LiveSession, MultiCameraLiveSession, grab_preview_frame, live_registry
from app.worker.queue import enqueue
from app.worker.tasks import build_calibration, tracking_path_for

router = APIRouter(prefix="/matches/live", tags=["live"])

RECENT_EVENTS_LIMIT = 20
ACTIVE_TRACK_WINDOW_S = 3.0


class PreviewRequest(BaseModel):
    stream_url: str


@router.post("/preview", response_class=Response)
async def preview_stream_frame(payload: PreviewRequest):
    """Returns one JPEG frame from the stream, so the user can click the
    court corners on it to calibrate before starting the live session."""
    try:
        jpeg = await asyncio.to_thread(grab_preview_frame, payload.stream_url)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=jpeg, media_type="image/jpeg")


@router.post("/start", response_model=MatchOut)
async def start_live_match(payload: LiveStreamStart, session: AsyncSession = Depends(get_session)):
    if payload.court_type not in {t.value for t in CourtType}:
        raise HTTPException(status_code=400, detail="court_type debe ser 'piso' o 'playa'")

    court = get_court_config(CourtType(payload.court_type))
    match = Match(name=payload.name, court_type=payload.court_type, source_mode="live", status="processing")

    if payload.cameras and len(payload.cameras) > 1:
        clock_start = time.time()
        sessions: list[tuple[int, LiveSession]] = []
        for index, cam in enumerate(payload.cameras):
            calib_json = cam.calibration.model_dump() if cam.calibration else None
            if calib_json is not None:
                calib_json["region"] = cam.region
            match.cameras.append(
                Camera(
                    index=index,
                    name=cam.name or f"Cámara {index + 1}",
                    region=cam.region,
                    stream_url=cam.stream_url,
                    calibration=calib_json,
                    time_offset_s=None,  # synced by the shared server clock
                )
            )
            live = LiveSession(
                cam.stream_url,
                court,
                build_calibration(court, calib_json),
                region=CourtRegion(cam.region),
                clock_start=clock_start,
            )
            sessions.append((index, live))
        live_session: LiveSession | MultiCameraLiveSession = MultiCameraLiveSession(sessions, court)
    else:
        single = payload.cameras[0] if payload.cameras else None
        stream_url = single.stream_url if single else payload.stream_url
        if not stream_url:
            raise HTTPException(status_code=400, detail="Indica la URL del stream")
        calibration_json = (single.calibration if single else payload.calibration)
        calibration_json = calibration_json.model_dump() if calibration_json else None
        match.stream_url = stream_url
        match.calibration = calibration_json
        live_session = LiveSession(stream_url, court, build_calibration(court, calibration_json))

    session.add(match)
    await session.commit()
    await session.refresh(match)

    live_session.start()
    live_registry.add(match.id, live_session)
    return match


@router.post("/{match_id}/stop", response_model=MatchOut)
async def stop_live_match(match_id: int, session: AsyncSession = Depends(get_session)):
    live_session = live_registry.get(match_id)
    if live_session is None:
        raise HTTPException(status_code=404, detail="No hay una transmisión activa con ese id")

    match = await session.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    # Stop first so the snapshot includes every frame processed up to the
    # moment the capture thread exits.
    await asyncio.to_thread(live_session.stop)
    result = live_session.snapshot()
    live_registry.remove(match_id)

    # Save the trajectories and let the queue worker do the (quick) final
    # analysis, so it's retried/visible like any other job.
    if isinstance(live_session, MultiCameraLiveSession):
        # Per-camera trajectories; the worker fuses them.
        cameras = {
            c.index: c
            for c in (await session.execute(select(Camera).where(Camera.match_id == match_id))).scalars()
        }
        for index, cam_result in live_session.camera_snapshots():
            path = tracking_path_for(match_id, index)
            await asyncio.to_thread(cam_result.save, path)
            cameras[index].tracking_path = str(path)
    else:
        tracking_path = tracking_path_for(match_id)
        await asyncio.to_thread(result.save, tracking_path)
        match.tracking_path = str(tracking_path)
    match.duration_s = result.duration_s
    await enqueue(session, match, "tracking")
    await session.commit()
    await session.refresh(match)
    return match


@router.get("/{match_id}/snapshot", response_model=LiveStatsSnapshot)
async def live_snapshot(match_id: int, session: AsyncSession = Depends(get_session)):
    live_session = live_registry.get(match_id)
    if live_session is None:
        raise HTTPException(status_code=404, detail="No hay una transmisión activa con ese id")

    match = await session.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    court = get_court_config(CourtType(match.court_type))
    result = live_session.snapshot()

    analysis = analyze_session(result, court)
    recent_events = analysis.events[-RECENT_EVENTS_LIMIT:]

    active_cutoff = result.duration_s - ACTIVE_TRACK_WINDOW_S
    active_tracks = len({f.track_id for f in result.player_frames if f.t >= active_cutoff})

    return LiveStatsSnapshot(
        match_id=match_id,
        elapsed_s=result.duration_s,
        active_tracks=active_tracks,
        recent_events=[
            {
                "event_type": e.event_type,
                "timestamp_s": e.t,
                "track_id_from": e.track_id_from,
                "track_id_to": e.track_id_to,
                "x": e.x,
                "y": e.y,
                "meta": e.meta,
            }
            for e in recent_events
        ],
        player_stats=[
            PlayerStatOut(
                track_id=s.track_id,
                player_id=None,
                label=f"Jugador #{s.track_id}",
                team=analysis.team_by_track.get(s.track_id),
                role=analysis.role_by_track.get(s.track_id),
                distance_m=s.distance_m,
                avg_speed_kmh=s.avg_speed_kmh,
                max_speed_kmh=s.max_speed_kmh,
                sprint_count=s.sprint_count,
                time_in_zones=s.time_in_zones,
                heatmap_path=None,
            )
            for s in analysis.player_stats.values()
        ],
        team_summary=analysis.team_summary or None,
    )
