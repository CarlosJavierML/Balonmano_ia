from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PixelCorner(BaseModel):
    x: float
    y: float


class CalibrationIn(BaseModel):
    """Four court corners in pixel space, top-left/top-right/bottom-right/bottom-left,
    used to compute the pixel-to-world homography for this fixed camera."""

    corners: list[PixelCorner] = Field(min_length=4, max_length=4)


class MatchCreate(BaseModel):
    name: str
    court_type: str  # "piso" | "playa"
    calibration: CalibrationIn | None = None


class MatchOut(BaseModel):
    id: int
    name: str
    court_type: str
    source_mode: str
    status: str
    duration_s: float | None
    progress: float = 0.0
    error_message: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlayerStatOut(BaseModel):
    track_id: int
    player_id: int | None
    label: str
    team: str | None = None
    distance_m: float
    avg_speed_kmh: float
    max_speed_kmh: float
    sprint_count: int
    time_in_zones: dict[str, Any]
    heatmap_path: str | None

    model_config = ConfigDict(from_attributes=True)


class EventOut(BaseModel):
    event_type: str
    timestamp_s: float
    track_id_from: int | None
    track_id_to: int | None
    x: float | None
    y: float | None
    meta: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class MatchDetailOut(MatchOut):
    team_summary: dict[str, Any] | None = None
    player_stats: list[PlayerStatOut] = []
    events: list[EventOut] = []


class LiveStreamStart(BaseModel):
    name: str
    court_type: str
    stream_url: str
    calibration: CalibrationIn | None = None


class LiveStatsSnapshot(BaseModel):
    match_id: int
    elapsed_s: float
    active_tracks: int
    recent_events: list[EventOut]
    player_stats: list[PlayerStatOut]
    team_summary: dict[str, Any] | None = None
