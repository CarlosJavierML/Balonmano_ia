"""Heuristic tactical event detection: possession, passes, shots, goals.

This is deliberately rule-based (nearest-player-to-ball for possession,
ball speed + direction + proximity to a goal for shots/goals) rather than
a trained action-recognition model. It works directly off the trajectories
produced by `app.vision.pipeline` with no extra training data, and gives a
concrete, inspectable baseline. See docs/ROADMAP.md for upgrading specific
event types to learned models once labelled clips are available -- the
`TacticalEvent` output shape here is exactly what a learned detector should
also produce, so the rest of the app (DB, API, reports) doesn't need to
change.

When a team assignment is available (see `app.analysis.teams`) a
possession change between teammates is reported as a "pase" and one between
opponents as a "perdida" (turnover). If either player has no team (not
enough color samples, referee, etc.) it falls back to the generic
"cambio_posesion".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.court import CourtConfig
from app.vision.pipeline import BallFrame, TrackFrame

POSSESSION_MAX_DIST_M = 1.6
POSSESSION_MIN_HOLD_S = 0.3
SHOT_MIN_SPEED_KMH = 45.0
SHOT_COOLDOWN_S = 1.5
GOAL_CHECK_WINDOW_S = 1.2


@dataclass
class TacticalEvent:
    event_type: str  # "pase" | "perdida" | "cambio_posesion" | "tiro" | "gol"
    t: float
    x: float
    y: float
    track_id_from: int | None = None
    track_id_to: int | None = None
    meta: dict = field(default_factory=dict)


def _players_by_time(player_frames: list[TrackFrame]) -> dict[float, list[tuple[int, float, float]]]:
    by_time: dict[float, list[tuple[int, float, float]]] = {}
    for f in player_frames:
        by_time.setdefault(f.t, []).append((f.track_id, f.x, f.y))
    return by_time


def _nearest_player(
    bx: float, by: float, players: list[tuple[int, float, float]]
) -> tuple[int | None, float]:
    best_id: int | None = None
    best_dist = float("inf")
    for track_id, px, py in players:
        dist = ((px - bx) ** 2 + (py - by) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best_id = track_id
    return best_id, best_dist


def _goal_side_for_x(x: float, court: CourtConfig) -> str:
    return "left" if x < court.length_m / 2 else "right"


def _possession_change_type(from_id: int, to_id: int, teams: dict[int, str]) -> str:
    from_team = teams.get(from_id)
    to_team = teams.get(to_id)
    if from_team is None or to_team is None:
        return "cambio_posesion"
    return "pase" if from_team == to_team else "perdida"


def detect_events(
    player_frames: list[TrackFrame],
    ball_frames: list[BallFrame],
    court: CourtConfig,
    teams: dict[int, str] | None = None,
) -> list[TacticalEvent]:
    teams = teams or {}
    events: list[TacticalEvent] = []
    if not ball_frames:
        return events

    ball_frames = sorted(ball_frames, key=lambda f: f.t)
    players_by_time = _players_by_time(player_frames)

    current_possessor: int | None = None
    possessor_since_t: float = ball_frames[0].t
    last_shot_t = float("-inf")

    for i, bf in enumerate(ball_frames):
        players_now = players_by_time.get(bf.t, [])
        nearest_id, dist = _nearest_player(bf.x, bf.y, players_now)
        holder = nearest_id if dist <= POSSESSION_MAX_DIST_M else None

        if holder != current_possessor:
            # Require the previous possession to have lasted a minimum time
            # before trusting the switch, to absorb single-frame jitter
            # from two players standing close to each other near the ball.
            held_long_enough = (bf.t - possessor_since_t) >= POSSESSION_MIN_HOLD_S
            if holder is not None and current_possessor is not None and held_long_enough:
                meta = {}
                if current_possessor in teams:
                    meta["team_from"] = teams[current_possessor]
                if holder in teams:
                    meta["team_to"] = teams[holder]
                events.append(
                    TacticalEvent(
                        event_type=_possession_change_type(current_possessor, holder, teams),
                        t=bf.t,
                        x=bf.x,
                        y=bf.y,
                        track_id_from=current_possessor,
                        track_id_to=holder,
                        meta=meta,
                    )
                )
            current_possessor = holder
            possessor_since_t = bf.t

        # Shot detection: needs a previous ball sample to get a velocity vector.
        if i == 0:
            continue
        prev = ball_frames[i - 1]
        dt = bf.t - prev.t
        if dt <= 0:
            continue
        vx = (bf.x - prev.x) / dt
        vy = (bf.y - prev.y) / dt
        speed_kmh = ((vx**2 + vy**2) ** 0.5) * 3.6
        if speed_kmh < SHOT_MIN_SPEED_KMH:
            continue
        if (bf.t - last_shot_t) < SHOT_COOLDOWN_S:
            continue

        side = _goal_side_for_x(bf.x, court)
        goal_x = court.goal_line_x(side)
        moving_toward_goal = vx * (goal_x - bf.x) > 0
        if not (moving_toward_goal and court.is_in_goal_area(bf.x, bf.y, side)):
            continue

        last_shot_t = bf.t
        shot_meta: dict = {"side": side, "speed_kmh": round(speed_kmh, 1)}
        if current_possessor in teams:
            shot_meta["team"] = teams[current_possessor]
        events.append(
            TacticalEvent(
                event_type="tiro",
                t=bf.t,
                x=bf.x,
                y=bf.y,
                track_id_from=current_possessor,
                meta=shot_meta,
            )
        )

        goal_event = _check_goal_after(ball_frames, i, side, court)
        if goal_event is not None:
            goal_event.track_id_from = current_possessor
            if "team" in shot_meta:
                goal_event.meta["team"] = shot_meta["team"]
            events.append(goal_event)

    return events


def _check_goal_after(
    ball_frames: list[BallFrame], from_index: int, side: str, court: CourtConfig
) -> TacticalEvent | None:
    start_t = ball_frames[from_index].t
    for f in ball_frames[from_index + 1 :]:
        if f.t - start_t > GOAL_CHECK_WINDOW_S:
            break
        if court.is_goal(f.x, f.y, side):
            return TacticalEvent(event_type="gol", t=f.t, x=f.x, y=f.y, meta={"side": side})
    return None


def possession_time_by_team(
    player_frames: list[TrackFrame],
    ball_frames: list[BallFrame],
    teams: dict[int, str],
) -> dict[str, float]:
    """Seconds of ball possession per team: each ball sample interval is
    credited to the team of the player holding the ball at its start."""
    totals: dict[str, float] = {}
    if len(ball_frames) < 2 or not teams:
        return totals

    ball_frames = sorted(ball_frames, key=lambda f: f.t)
    players_by_time = _players_by_time(player_frames)
    for prev, curr in zip(ball_frames, ball_frames[1:]):
        dt = curr.t - prev.t
        if dt <= 0:
            continue
        nearest_id, dist = _nearest_player(prev.x, prev.y, players_by_time.get(prev.t, []))
        if nearest_id is None or dist > POSSESSION_MAX_DIST_M:
            continue
        team = teams.get(nearest_id)
        if team is not None:
            totals[team] = totals.get(team, 0.0) + dt
    return totals


def build_team_summary(
    events: list[TacticalEvent],
    teams: dict[int, str],
    team_colors: dict[str, str],
    possession_s: dict[str, float],
) -> dict[str, dict]:
    """Per-team aggregate used by the dashboard and PDF report."""
    summary: dict[str, dict] = {}
    total_possession = sum(possession_s.values())
    for team, color in sorted(team_colors.items()):
        summary[team] = {
            "color": color,
            "players": sum(1 for t in teams.values() if t == team),
            "possession_s": round(possession_s.get(team, 0.0), 1),
            "possession_pct": round(100 * possession_s.get(team, 0.0) / total_possession, 1)
            if total_possession
            else 0.0,
            "pases": 0,
            "perdidas": 0,
            "tiros": 0,
            "goles": 0,
        }

    for ev in events:
        if ev.event_type == "pase":
            team = ev.meta.get("team_from")
            key = "pases"
        elif ev.event_type == "perdida":
            team = ev.meta.get("team_from")
            key = "perdidas"
        elif ev.event_type == "tiro":
            team = ev.meta.get("team")
            key = "tiros"
        elif ev.event_type == "gol":
            team = ev.meta.get("team")
            key = "goles"
        else:
            continue
        if team in summary:
            summary[team][key] += 1
    return summary
