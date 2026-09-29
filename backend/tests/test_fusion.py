import numpy as np
import pytest

from app.court import CourtRegion, CourtType, get_court_config
from app.vision.fusion import CameraTracking, fuse_cameras
from app.vision.pipeline import BallFrame, TrackFrame, TrackingResult

COURT = get_court_config(CourtType.PISO)  # 40 x 20 m
FPS = 6.0
RED = np.array([50.0, 200.0, 190.0], dtype=np.float32)
BLUE = np.array([35.0, 150.0, 60.0], dtype=np.float32)
CAM1_OFFSET = 2.0  # camera 1 started recording 2 s after camera 0


def _times(duration=6.0):
    return [round(i / FPS, 4) for i in range(int(duration * FPS) + 1)]


def _cams():
    """Camera 0 films the left half, camera 1 the right half (and each sees
    a few meters past the center line)."""
    cam0 = TrackingResult(fps=FPS, duration_s=6.0, calibrated=True)
    cam1 = TrackingResult(fps=FPS, duration_s=4.0, calibrated=True)
    for t in _times():
        # P1 runs along the court at 5 m/s: x = 5 -> 35.
        x = 5.0 + 5.0 * t
        if x <= 24:
            cam0.player_frames.append(TrackFrame(t=t, track_id=1, x=x, y=10.0))
        if x >= 16 and t >= CAM1_OFFSET:
            cam1.player_frames.append(TrackFrame(t=round(t - CAM1_OFFSET, 4), track_id=7, x=x + 0.2, y=10.1))
        # P2 stays in the left half; the tracker switches its id at t=3.
        if t < 3.0:
            cam0.player_frames.append(TrackFrame(t=t, track_id=2, x=8.0, y=4.0))
        elif t > 3.2:
            cam0.player_frames.append(TrackFrame(t=t, track_id=5, x=8.3, y=4.0))
        # P3 stands in the right half (only camera 1 records after t=2).
        if t >= CAM1_OFFSET:
            cam1.player_frames.append(TrackFrame(t=round(t - CAM1_OFFSET, 4), track_id=8, x=32.0, y=15.0))
        # Ball follows P1.
        if x <= 20:
            cam0.ball_frames.append(BallFrame(t=t, x=x + 0.3, y=10.0))
        if x >= 20 and t >= CAM1_OFFSET:
            cam1.ball_frames.append(BallFrame(t=round(t - CAM1_OFFSET, 4), x=x + 0.3, y=10.0))
    cam0.color_samples = {1: [RED] * 5, 2: [BLUE] * 5, 5: [BLUE] * 5}
    cam1.color_samples = {7: [RED] * 5, 8: [BLUE] * 5}
    return [
        CameraTracking(0, CourtRegion.LEFT, 0.0, cam0),
        CameraTracking(1, CourtRegion.RIGHT, CAM1_OFFSET, cam1),
    ]


def _ids_by_position(result, predicate):
    return {f.track_id for f in result.player_frames if predicate(f)}


def test_player_crossing_cameras_keeps_one_identity():
    fused, report = fuse_cameras(_cams(), COURT)

    runner = _ids_by_position(fused, lambda f: abs(f.y - 10.0) < 0.5)
    assert len(runner) == 1
    frames = sorted((f for f in fused.player_frames if f.track_id in runner), key=lambda f: f.t)
    assert frames[0].x < 6 and frames[-1].x > 34  # whole run, both halves
    assert len({f.t for f in frames}) == len(frames)  # one position per instant
    # Session time: camera 1's samples were shifted by its offset.
    assert abs(frames[-1].t - 6.0) < 1e-6
    assert report.overlap_merges >= 1


def test_tracker_id_switch_is_repaired_by_handoff():
    fused, report = fuse_cameras(_cams(), COURT)

    assert len(_ids_by_position(fused, lambda f: abs(f.y - 4.0) < 0.5)) == 1
    assert report.handoffs >= 1
    assert report.people == 3
    assert {c["index"] for c in report.cameras} == {0, 1}


def test_ball_is_fused_on_the_session_timeline():
    fused, _ = fuse_cameras(_cams(), COURT)

    ts = [b.t for b in fused.ball_frames]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)
    assert ts[0] == 0.0 and abs(ts[-1] - 6.0) < 1e-6
    assert max(b.x for b in fused.ball_frames) > 34


def test_two_close_players_in_the_overlap_are_not_merged_into_one():
    cam0 = TrackingResult(fps=FPS, duration_s=2.0, calibrated=True)
    cam1 = TrackingResult(fps=FPS, duration_s=2.0, calibrated=True)
    for t in _times(2.0):
        for track_id, y in ((1, 10.0), (2, 11.0)):  # 1 m apart, both at x=19
            cam0.player_frames.append(TrackFrame(t=t, track_id=track_id, x=19.0, y=y))
            cam1.player_frames.append(TrackFrame(t=t, track_id=10 + track_id, x=19.1, y=y + 0.05))
    cams = [CameraTracking(0, CourtRegion.LEFT, 0.0, cam0), CameraTracking(1, CourtRegion.RIGHT, 0.0, cam1)]

    fused, report = fuse_cameras(cams, COURT)

    assert report.people == 2
    assert report.overlap_merges == 2


def test_negative_offset_is_normalized_to_start_at_zero():
    cams = _cams()
    cams[1].offset_s = -1.0  # camera 1 started 1 s *before* camera 0

    fused, _ = fuse_cameras(cams, COURT)

    assert min(f.t for f in fused.player_frames) == 0.0
    assert fused.duration_s == max(6.0 + 1.0, 4.0)


def test_detections_far_outside_a_cameras_half_are_ignored():
    cam0 = TrackingResult(fps=FPS, duration_s=1.0, calibrated=True)
    for t in _times(1.0):
        cam0.player_frames.append(TrackFrame(t=t, track_id=1, x=35.0, y=5.0))  # 15 m into the other half
    fused, report = fuse_cameras([CameraTracking(0, CourtRegion.LEFT, 0.0, cam0)], COURT)

    assert fused.player_frames == [] and report.people == 0


def test_cameras_sampling_at_different_instants_give_a_smooth_speed():
    # Camera 1 samples half a step later than camera 0 (e.g. a 2.08 s sync
    # offset): positions must be interpolated, not snapped, or the runner
    # appears to jump back and forth when the cameras take over.
    cam0 = TrackingResult(fps=FPS, duration_s=6.0, calibrated=True)
    cam1 = TrackingResult(fps=FPS, duration_s=6.0, calibrated=True)
    half_step = 0.5 / FPS
    for t in _times():
        x = 5.0 + 5.0 * t
        if x <= 24:
            cam0.player_frames.append(TrackFrame(t=t, track_id=1, x=x, y=10.0))
        t1 = t + half_step
        x1 = 5.0 + 5.0 * t1
        if x1 >= 16:
            cam1.player_frames.append(TrackFrame(t=t1, track_id=2, x=x1, y=10.0))
    cams = [CameraTracking(0, CourtRegion.LEFT, 0.0, cam0), CameraTracking(1, CourtRegion.RIGHT, 0.0, cam1)]

    fused, report = fuse_cameras(cams, COURT)

    assert report.people == 1
    frames = sorted(fused.player_frames, key=lambda f: f.t)
    speeds = [(b.x - a.x) / (b.t - a.t) for a, b in zip(frames, frames[1:])]
    assert max(speeds) == pytest.approx(5.0, abs=0.05) and min(speeds) == pytest.approx(5.0, abs=0.05)


def test_interpolation_does_not_bridge_long_gaps():
    cam0 = TrackingResult(fps=FPS, duration_s=4.0, calibrated=True)
    for t in (0.0, 1 / FPS, 2 / FPS, 3.0, 3 + 1 / FPS):  # unseen for almost 3 s
        cam0.player_frames.append(TrackFrame(t=t, track_id=1, x=5.0, y=5.0))
    fused, _ = fuse_cameras([CameraTracking(0, CourtRegion.FULL, 0.0, cam0)], COURT)

    assert len(fused.player_frames) == 5
