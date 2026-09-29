"""Turns raw trajectories into the full analysis of a session (physical
stats, team + role assignment, tactical events and team summary).

Shared by the background worker (uploaded videos / finished live sessions),
the live snapshot endpoint and manual corrections (re-analysis after the
user fixes a player's team or role), so all of them compute the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis.physical import PlayerPhysicalStats, compute_player_stats
from app.analysis.roles import (
    ROLE_GOALKEEPER,
    ROLE_PLAYER,
    ROLE_REFEREE,
    detect_goalkeepers,
    infer_team_from_passes,
)
from app.analysis.tactical import (
    TacticalEvent,
    build_team_summary,
    detect_events,
    possession_time_by_team,
)
from app.analysis.teams import assign_teams
from app.court import CourtConfig
from app.vision.pipeline import TrackingResult

# Manual corrections, as stored on the match: {"<track_id>": {"team": "A" | "B" | None,
# "role": "jugador" | "portero" | "arbitro"}}. A missing key means "keep the automatic value".
Overrides = dict[str, dict[str, Any]]


@dataclass
class SessionAnalysis:
    player_stats: dict[int, PlayerPhysicalStats]
    events: list[TacticalEvent]
    team_by_track: dict[int, str]
    role_by_track: dict[int, str]
    team_summary: dict[str, dict]


def _assign_roles_and_teams(
    result: TrackingResult, court: CourtConfig, overrides: Overrides
) -> tuple[dict[int, str], dict[int, str], dict[str, str]]:
    assignment = assign_teams(result.color_samples)
    teams = dict(assignment.team_by_track)
    goalkeepers = detect_goalkeepers(result.player_frames, court)

    roles: dict[int, str] = {}
    for track_id in {f.track_id for f in result.player_frames}:
        if track_id in goalkeepers:
            roles[track_id] = ROLE_GOALKEEPER
        elif track_id in assignment.outliers:
            roles[track_id] = ROLE_REFEREE
        else:
            roles[track_id] = ROLE_PLAYER

    for key, fix in overrides.items():
        track_id = int(key)
        if "role" in fix:
            roles[track_id] = fix["role"]
        if "team" in fix:
            if fix["team"] is None:
                teams.pop(track_id, None)
            else:
                teams[track_id] = fix["team"]

    # Goalkeepers without a (color or manual) team: infer it from who they
    # pass to. Needs a first pass of event detection with the field teams.
    pending_gk = [
        t for t, role in roles.items() if role == ROLE_GOALKEEPER and t not in teams
    ]
    if pending_gk:
        preliminary = detect_events(
            _without_referees(result, roles).player_frames, result.ball_frames, court, teams
        )
        for track_id in pending_gk:
            team = infer_team_from_passes(track_id, preliminary, teams)
            if team is not None:
                teams[track_id] = team

    # Referees never belong to a team, whatever the color clustering said.
    for track_id, role in roles.items():
        if role == ROLE_REFEREE:
            teams.pop(track_id, None)

    return teams, roles, assignment.team_colors


def _without_referees(result: TrackingResult, roles: dict[int, str]) -> TrackingResult:
    return TrackingResult(
        fps=result.fps,
        duration_s=result.duration_s,
        calibrated=result.calibrated,
        player_frames=[f for f in result.player_frames if roles.get(f.track_id) != ROLE_REFEREE],
        ball_frames=result.ball_frames,
        color_samples=result.color_samples,
    )


def analyze_session(
    result: TrackingResult, court: CourtConfig, overrides: Overrides | None = None
) -> SessionAnalysis:
    player_stats = compute_player_stats(result.player_frames, court)
    teams, roles, team_colors = _assign_roles_and_teams(result, court, overrides or {})

    # Referees are excluded from possession/event detection so that one
    # standing next to the ball never appears to "have" it.
    in_play = _without_referees(result, roles)
    events = detect_events(in_play.player_frames, in_play.ball_frames, court, teams)
    possession = possession_time_by_team(in_play.player_frames, in_play.ball_frames, teams)
    summary = build_team_summary(events, teams, team_colors, possession)
    return SessionAnalysis(
        player_stats=player_stats,
        events=events,
        team_by_track=teams,
        role_by_track=roles,
        team_summary=summary,
    )
