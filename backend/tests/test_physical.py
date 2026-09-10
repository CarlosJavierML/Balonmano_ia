import pytest

from app.analysis.physical import (
    MAX_PLAUSIBLE_SPEED_KMH,
    SPRINT_THRESHOLD_KMH,
    compute_player_stats,
)
from app.court import CourtType, get_court_config
from app.vision.pipeline import TrackFrame

COURT = get_court_config(CourtType.PISO)


def test_distance_and_speed_for_constant_velocity_walk():
    # Track id 1 walks 1 m/s along x for 10 seconds -> 10 m at 3.6 km/h.
    frames = [TrackFrame(t=float(i), track_id=1, x=float(i), y=5.0) for i in range(11)]

    stats = compute_player_stats(frames, COURT)

    assert stats[1].distance_m == pytest.approx(10.0, abs=0.01)
    assert stats[1].avg_speed_kmh == pytest.approx(3.6, abs=0.05)


def test_implausible_jump_is_excluded_from_distance():
    # A believable 1m/s walk, then one frame teleports 100m in 0.1s (a
    # tracking glitch), then resumes the believable walk.
    frames = [
        TrackFrame(t=0.0, track_id=2, x=0.0, y=5.0),
        TrackFrame(t=1.0, track_id=2, x=1.0, y=5.0),
        TrackFrame(t=1.1, track_id=2, x=101.0, y=5.0),
        TrackFrame(t=2.1, track_id=2, x=102.0, y=5.0),
    ]

    stats = compute_player_stats(frames, COURT)

    # Only the two believable 1m segments should count; the glitch segment
    # (100m in 0.1s => implausibly fast) and the segment right after it must
    # not distort the total distance.
    assert stats[2].distance_m < 5.0
    assert stats[2].max_speed_kmh <= MAX_PLAUSIBLE_SPEED_KMH


def test_zone_classification_thirds_of_court():
    frames = [
        TrackFrame(t=0.0, track_id=3, x=1.0, y=5.0),  # defensive third
        TrackFrame(t=1.0, track_id=3, x=1.0, y=5.0),
        TrackFrame(t=2.0, track_id=3, x=35.0, y=5.0),  # offensive third
    ]

    stats = compute_player_stats(frames, COURT)

    assert "tercio_defensivo" in stats[3].time_in_zones
    assert stats[3].time_in_zones["tercio_defensivo"] > 0


def test_sprint_is_counted_when_sustained_above_threshold():
    # ~7 m/s (~25 km/h, above the sprint threshold) sustained for 2 seconds.
    fast_speed_mps = (SPRINT_THRESHOLD_KMH + 5) / 3.6
    frames = [TrackFrame(t=float(i) * 0.5, track_id=4, x=fast_speed_mps * i * 0.5, y=5.0) for i in range(5)]

    stats = compute_player_stats(frames, COURT)

    assert stats[4].sprint_count == 1


def test_empty_input_returns_empty_stats():
    assert compute_player_stats([], COURT) == {}
