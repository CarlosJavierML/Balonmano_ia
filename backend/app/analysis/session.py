"""Turns raw trajectories into the full analysis of a session (physical
stats, team assignment, tactical events and team summary).

Shared by the background worker (uploaded videos / finished live sessions)
and the live snapshot endpoint so both always compute the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.physical import PlayerPhysicalStats, compute_player_stats
from app.analysis.tactical import (
    TacticalEvent,
    build_team_summary,
    detect_events,
    possession_time_by_team,
)
from app.analysis.teams import assign_teams
from app.court import CourtConfig
from app.vision.pipeline import TrackingResult


@dataclass
class SessionAnalysis:
    player_stats: dict[int, PlayerPhysicalStats]
    events: list[TacticalEvent]
    team_by_track: dict[int, str]
    team_summary: dict[str, dict]


def analyze_session(result: TrackingResult, court: CourtConfig) -> SessionAnalysis:
    player_stats = compute_player_stats(result.player_frames, court)
    teams = assign_teams(result.color_samples)
    events = detect_events(result.player_frames, result.ball_frames, court, teams.team_by_track)
    possession = possession_time_by_team(result.player_frames, result.ball_frames, teams.team_by_track)
    summary = build_team_summary(events, teams.team_by_track, teams.team_colors, possession)
    return SessionAnalysis(
        player_stats=player_stats,
        events=events,
        team_by_track=teams.team_by_track,
        team_summary=summary,
    )
