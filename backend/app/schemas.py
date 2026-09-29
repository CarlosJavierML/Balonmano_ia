from datetime import datetime
from typing import Any, Literal

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
    role: str | None = None
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


class JobOut(BaseModel):
    id: int
    match_id: int
    kind: str
    status: str
    attempts: int
    max_attempts: int
    cancel_requested: bool
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Filled in by the API for queued jobs (1 = next to run).
    position: int | None = None

    model_config = ConfigDict(from_attributes=True)


class QueueItemOut(JobOut):
    match_name: str
    progress: float


class MatchDetailOut(MatchOut):
    # The queued/running analysis job for this session, if any.
    active_job: JobOut | None = None
    team_summary: dict[str, Any] | None = None
    team_names: dict[str, str] | None = None
    # Whether manual team/role corrections can be applied (trajectories saved).
    editable: bool = False
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


class TeamNamesIn(BaseModel):
    names: dict[Literal["A", "B"], str]


class PlayerUpdateIn(BaseModel):
    """Manual correction of one tracked person. Omitted fields are left
    unchanged; ``team: null`` explicitly removes the player from any team,
    and ``reset: true`` drops earlier manual team/role corrections."""

    label: str | None = Field(default=None, max_length=120)
    team: Literal["A", "B"] | None = None
    role: Literal["jugador", "portero", "arbitro"] | None = None
    reset: bool = False


class CornerSuggestionOut(BaseModel):
    corners: list[PixelCorner]
