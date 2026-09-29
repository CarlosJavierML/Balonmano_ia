from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))

    players: Mapped[list["Player"]] = relationship(back_populates="team")


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(120))
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    team: Mapped["Team"] = relationship(back_populates="players")


class Match(Base):
    """A single analysis session: one uploaded video or one live stream run."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    court_type: Mapped[str] = mapped_column(String(20))  # "piso" | "playa"
    source_mode: Mapped[str] = mapped_column(String(20))  # "upload" | "live"
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending -> processing -> done | failed
    video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stream_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    calibration: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Fraction of the video analyzed so far, in [0, 1] (uploads only).
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    # Per-team aggregates (possession, passes, turnovers, shots, goals,
    # jersey color) -- see app.analysis.tactical.build_team_summary.
    team_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player_stats: Mapped[list["PlayerMatchStat"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(back_populates="match", cascade="all, delete-orphan")


class PlayerMatchStat(Base):
    """Aggregated physical stats for one tracked person within one match."""

    __tablename__ = "player_match_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    track_id: Mapped[int] = mapped_column(Integer)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    team: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "A" | "B" | None

    distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    avg_speed_kmh: Mapped[float] = mapped_column(Float, default=0.0)
    max_speed_kmh: Mapped[float] = mapped_column(Float, default=0.0)
    sprint_count: Mapped[int] = mapped_column(Integer, default=0)
    time_in_zones: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    heatmap_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    match: Mapped["Match"] = relationship(back_populates="player_stats")


class Event(Base):
    """A tactical event detected on the timeline: pass, shot, goal, turnover..."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    event_type: Mapped[str] = mapped_column(String(30))
    timestamp_s: Mapped[float] = mapped_column(Float)
    track_id_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_id_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    match: Mapped["Match"] = relationship(back_populates="events")
