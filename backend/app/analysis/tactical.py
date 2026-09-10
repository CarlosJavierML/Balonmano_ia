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

Known limitation: without team assignment (e.g. jersey-color clustering,
also on the roadmap) we can't tell a pass to a teammate apart from a
turnover to an opponent, so every possession change is reported generically
as "cambio_posesion" with the two track ids involved.
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
    event_type: str  # "cambio_posesion" | "tiro" | "gol"
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


def detect_events(
    player_frames: list[TrackFrame],
    ball_frames: list[BallFrame],
    court: CourtConfig,
) -> list[TacticalEvent]:
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
                events.append(
                    TacticalEvent(
                        event_type="cambio_posesion",
                        t=bf.t,
                        x=bf.x,
                        y=bf.y,
                        track_id_from=current_possessor,
                        track_id_to=holder,
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
        shot_event = TacticalEvent(
            event_type="tiro",
            t=bf.t,
            x=bf.x,
            y=bf.y,
            track_id_from=current_possessor,
            meta={"side": side, "speed_kmh": round(speed_kmh, 1)},
        )
        events.append(shot_event)

        goal_event = _check_goal_after(ball_frames, i, side, court)
        if goal_event is not None:
            goal_event.track_id_from = current_possessor
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
