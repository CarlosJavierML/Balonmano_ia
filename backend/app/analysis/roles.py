"""Player roles: field player, goalkeeper or referee/other.

Goalkeepers usually wear a different kit from their field players, so the
jersey-color clustering in `app.analysis.teams` tends to leave them without
a team. We recover them by *where* they are rather than by color: a track
that spends most of its time inside one goal area is a goalkeeper. Its team
is then inferred from who it passes the ball to (after a save, the
goalkeeper restarts play with a teammate).

Tracks left without a team that are *not* goalkeepers are treated as
referees (or other people on court) and are excluded from possession, so a
referee standing next to the ball never "steals" it.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from app.court import CourtConfig
from app.vision.pipeline import TrackFrame

ROLE_PLAYER = "jugador"
ROLE_GOALKEEPER = "portero"
ROLE_REFEREE = "arbitro"
ROLES = (ROLE_PLAYER, ROLE_GOALKEEPER, ROLE_REFEREE)

# Fraction of a track's samples that must fall inside a goal area (with a
# small margin, the goalkeeper steps out a bit) to be called a goalkeeper.
GOALKEEPER_MIN_FRACTION = 0.7
GOALKEEPER_AREA_MARGIN_M = 1.0
# Very short tracks (someone crossing the goal area once) are not enough.
GOALKEEPER_MIN_SAMPLES = 10


def detect_goalkeepers(player_frames: list[TrackFrame], court: CourtConfig) -> dict[int, str]:
    """Returns {track_id: goal side} for tracks that stay in one goal area."""
    by_track: dict[int, list[TrackFrame]] = defaultdict(list)
    for f in player_frames:
        by_track[f.track_id].append(f)

    radius_sq = (court.goal_area_radius_m + GOALKEEPER_AREA_MARGIN_M) ** 2
    goalkeepers: dict[int, str] = {}
    for track_id, frames in by_track.items():
        if len(frames) < GOALKEEPER_MIN_SAMPLES:
            continue
        for side in ("left", "right"):
            goal_x = court.goal_line_x(side)
            inside = sum(
                1 for f in frames if (f.x - goal_x) ** 2 + (f.y - court.goal_center_y) ** 2 <= radius_sq
            )
            if inside / len(frames) >= GOALKEEPER_MIN_FRACTION:
                goalkeepers[track_id] = side
                break
    return goalkeepers


def infer_team_from_passes(track_id: int, events, teams: dict[int, str]) -> str | None:
    """Majority team among the players this track handed the ball to."""
    receivers = Counter(
        teams[ev.track_id_to]
        for ev in events
        if ev.track_id_from == track_id and ev.track_id_to in teams
    )
    if not receivers:
        return None
    (best, best_count), *rest = receivers.most_common()
    if rest and rest[0][1] == best_count:
        return None  # tie: not enough evidence either way
    return best
