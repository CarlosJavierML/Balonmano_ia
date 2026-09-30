"""Data for the animated 2D recreation of a session (top-down court view).

Built from the saved trajectories plus what the analysis decided about
each person (name, team, role) and the tactical events, in a compact
shape the dashboard can animate and the video renderer can draw.
"""

from __future__ import annotations

from collections import defaultdict

from app.court import CourtConfig
from app.models import Match
from app.vision.pipeline import TrackingResult

NEUTRAL_COLOR = "#9aa5bd"  # people without a team (referees...)
FALLBACK_TEAM_COLORS = {"A": "#e8e8e8", "B": "#3b6fd8"}


def build_replay(match: Match, tracking: TrackingResult, court: CourtConfig) -> dict:
    stats = {s.track_id: s for s in match.player_stats}
    summary = match.team_summary or {}
    names = match.team_names or {}

    def team_color(team: str | None) -> str:
        if team is None:
            return NEUTRAL_COLOR
        return (summary.get(team) or {}).get("color") or FALLBACK_TEAM_COLORS.get(team, NEUTRAL_COLOR)

    tracks: dict[int, list[list[float]]] = defaultdict(list)
    for f in tracking.player_frames:
        tracks[f.track_id].append([round(f.t, 2), round(f.x, 2), round(f.y, 2)])

    players = []
    for track_id, points in sorted(tracks.items()):
        points.sort()
        stat = stats.get(track_id)
        team = stat.team if stat else None
        players.append(
            {
                "track_id": track_id,
                "label": stat.label if stat and stat.label else f"#{track_id}",
                "team": team,
                "role": stat.role if stat else None,
                "color": team_color(team),
                "points": points,  # [t, x, y] in seconds / meters
            }
        )

    return {
        "match_id": match.id,
        "name": match.name,
        "duration_s": round(max(match.duration_s or 0.0, tracking.duration_s or 0.0), 2),
        "fps": tracking.fps,
        "court": {
            "type": match.court_type,
            "length_m": court.length_m,
            "width_m": court.width_m,
            "goal_width_m": court.goal_width_m,
            "goal_area_radius_m": court.goal_area_radius_m,
            "free_throw_radius_m": court.free_throw_radius_m,
        },
        "teams": {
            team: {"name": names.get(team) or f"Equipo {team}", "color": team_color(team)}
            for team in sorted(summary)
        },
        "players": players,
        "ball": sorted([round(b.t, 2), round(b.x, 2), round(b.y, 2)] for b in tracking.ball_frames),
        "events": sorted(
            (
                {
                    "type": e.event_type,
                    "t": round(e.timestamp_s, 2),
                    "from": e.track_id_from,
                    "to": e.track_id_to,
                    "x": e.x,
                    "y": e.y,
                    "team": (e.meta or {}).get("team") or (e.meta or {}).get("team_from"),
                }
                for e in match.events
            ),
            key=lambda e: e["t"],
        ),
    }
