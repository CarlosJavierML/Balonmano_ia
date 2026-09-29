import numpy as np

from app.analysis.roles import detect_goalkeepers
from app.analysis.session import analyze_session
from app.court import CourtType, get_court_config
from app.vision.pipeline import BallFrame, TrackFrame, TrackingResult

COURT = get_court_config(CourtType.PISO)
# Lab-ish color features (OpenCV 8-bit convention, a/b centered on 128).
RED = np.array([50.0, 200.0, 190.0], dtype=np.float32)
BLUE = np.array([35.0, 150.0, 60.0], dtype=np.float32)
YELLOW = np.array([90.0, 110.0, 220.0], dtype=np.float32)


def test_track_that_stays_in_goal_area_is_goalkeeper():
    frames = [TrackFrame(t=i * 0.5, track_id=1, x=1.5 + 0.1 * (i % 3), y=10.0) for i in range(20)]
    frames += [TrackFrame(t=i * 0.5, track_id=2, x=15.0 + i, y=8.0) for i in range(20)]
    assert detect_goalkeepers(frames, COURT) == {1: "left"}


def test_short_track_in_goal_area_is_not_goalkeeper():
    frames = [TrackFrame(t=i * 0.5, track_id=1, x=2.0, y=10.0) for i in range(4)]
    assert detect_goalkeepers(frames, COURT) == {}


def _scenario() -> TrackingResult:
    """Goalkeeper 9 (odd kit) passes to red 1; referee 7 (yellow) stands next
    to the ball carrier; red 1 passes to red 2. The other red (4, 5) and
    blue (3, 6, 8) players stand far from the ball."""
    result = TrackingResult(fps=2, duration_s=5.0, calibrated=True)
    positions = {
        9: (2.0, 10.0),
        1: (10.0, 10.0),
        2: (16.0, 10.0),
        7: (16.8, 10.0),
        4: (24.0, 2.0),
        5: (26.0, 18.0),
        3: (30.0, 5.0),
        6: (32.0, 15.0),
        8: (28.0, 10.0),
    }
    times = [i * 0.5 for i in range(12)]
    for t in times:
        for track_id, (x, y) in positions.items():
            result.player_frames.append(TrackFrame(t=t, track_id=track_id, x=x, y=y))
    for t in times:
        holder = 9 if t < 2.0 else (1 if t < 4.0 else 2)
        x, y = positions[holder]
        result.ball_frames.append(BallFrame(t=t, x=x + 0.1, y=y))
    result.color_samples = {
        **{t: [RED + np.float32(t % 3)] * 5 for t in (1, 2, 4, 5)},
        **{t: [BLUE - np.float32(t % 3)] * 5 for t in (3, 6, 8)},
        9: [YELLOW] * 5,  # goalkeeper in a different kit -> color outlier
        7: [YELLOW + np.float32(5)] * 5,  # referee -> color outlier
    }
    return result


def test_goalkeeper_team_is_inferred_from_passes_and_referee_is_excluded():
    analysis = analyze_session(_scenario(), COURT)

    assert analysis.role_by_track[9] == "portero"
    assert analysis.role_by_track[7] == "arbitro"
    assert analysis.role_by_track[1] == "jugador"
    red_team = analysis.team_by_track[1]
    assert analysis.team_by_track[9] == red_team
    assert 7 not in analysis.team_by_track

    # The referee stands closer to the ball than player 2 would ever let him
    # be "holder"; the only events are the two passes within the red team.
    assert [(e.event_type, e.track_id_from, e.track_id_to) for e in analysis.events] == [
        ("pase", 9, 1),
        ("pase", 1, 2),
    ]


def test_manual_overrides_win_over_automatic_assignment():
    red_team = analyze_session(_scenario(), COURT).team_by_track[1]
    blue_team = "B" if red_team == "A" else "A"

    analysis = analyze_session(_scenario(), COURT, {"2": {"team": blue_team}, "7": {"role": "jugador"}})

    assert analysis.team_by_track[2] == blue_team
    assert analysis.role_by_track[7] == "jugador"
    assert ("perdida", 1, 2) in [(e.event_type, e.track_id_from, e.track_id_to) for e in analysis.events]
