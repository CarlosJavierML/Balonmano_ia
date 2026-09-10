from app.analysis.tactical import detect_events
from app.court import CourtType, get_court_config
from app.vision.pipeline import BallFrame, TrackFrame

COURT = get_court_config(CourtType.PISO)


def test_possession_change_is_detected_as_pass():
    times = [0.0, 0.5, 1.0, 1.5, 2.0]
    player_frames = []
    for t in times:
        player_frames.append(TrackFrame(t=t, track_id=10, x=5.0, y=5.0))
        player_frames.append(TrackFrame(t=t, track_id=20, x=10.0, y=5.0))

    ball_frames = [
        BallFrame(t=0.0, x=5.2, y=5.0),  # next to player 10
        BallFrame(t=0.5, x=5.1, y=5.0),  # still with player 10 (held >= 0.3s)
        BallFrame(t=1.0, x=5.05, y=5.0),
        BallFrame(t=1.5, x=9.9, y=5.0),  # now next to player 20
        BallFrame(t=2.0, x=10.1, y=5.0),
    ]

    events = detect_events(player_frames, ball_frames, COURT)

    possession_events = [e for e in events if e.event_type == "cambio_posesion"]
    assert len(possession_events) == 1
    assert possession_events[0].track_id_from == 10
    assert possession_events[0].track_id_to == 20


def test_flickering_possession_is_ignored():
    # Ball bounces between being "closest" to two players within the same
    # instant repeatedly -- should not be treated as a real possession
    # change because it doesn't hold for POSSESSION_MIN_HOLD_S.
    player_frames = [
        TrackFrame(t=0.0, track_id=1, x=5.0, y=5.0),
        TrackFrame(t=0.05, track_id=1, x=5.0, y=5.0),
        TrackFrame(t=0.1, track_id=1, x=5.0, y=5.0),
        TrackFrame(t=0.0, track_id=2, x=5.3, y=5.0),
        TrackFrame(t=0.05, track_id=2, x=5.3, y=5.0),
        TrackFrame(t=0.1, track_id=2, x=5.3, y=5.0),
    ]
    ball_frames = [
        BallFrame(t=0.0, x=5.0, y=5.0),
        BallFrame(t=0.05, x=5.3, y=5.0),
        BallFrame(t=0.1, x=5.0, y=5.0),
    ]

    events = detect_events(player_frames, ball_frames, COURT)

    assert [e for e in events if e.event_type == "cambio_posesion"] == []


def test_fast_shot_toward_goal_area_is_a_shot_and_goal_event():
    ball_frames = [
        BallFrame(t=0.0, x=30.0, y=10.0),  # outside the 6m goal area
        BallFrame(t=0.2, x=36.0, y=10.0),  # fast, inside goal area, heading to goal
        BallFrame(t=0.4, x=40.05, y=10.0),  # crosses the goal line inside the goal mouth
    ]

    events = detect_events([], ball_frames, COURT)

    shots = [e for e in events if e.event_type == "tiro"]
    goals = [e for e in events if e.event_type == "gol"]
    assert len(shots) == 1
    assert shots[0].meta["side"] == "right"
    assert len(goals) == 1
    assert goals[0].meta["side"] == "right"


def test_slow_ball_movement_is_not_a_shot():
    ball_frames = [
        BallFrame(t=0.0, x=36.0, y=10.0),
        BallFrame(t=1.0, x=37.0, y=10.0),  # 1 m/s, far too slow to be a shot
    ]

    events = detect_events([], ball_frames, COURT)

    assert [e for e in events if e.event_type in ("tiro", "gol")] == []


def test_no_ball_frames_returns_no_events():
    assert detect_events([], [], COURT) == []
