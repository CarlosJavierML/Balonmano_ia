import numpy as np

from app.analysis.teams import assign_teams, jersey_color_feature
from app.court import CourtCalibration, CourtType, get_court_config
from app.vision.detector import COCO_PERSON_CLASS, Detection, FrameDetections
from app.vision.pipeline import FrameProcessor, TrackingResult

RED = (30, 30, 220)  # BGR
BLUE = (220, 60, 30)
YELLOW = (0, 230, 255)


def _frame_with_players(boxes_and_colors, size=(480, 640)):
    frame = np.full((*size, 3), (90, 140, 90), dtype=np.uint8)  # green-ish court
    for (x1, y1, x2, y2), color in boxes_and_colors:
        frame[y1:y2, x1:x2] = color
    return frame


def test_jersey_color_feature_separates_colors():
    frame = _frame_with_players([((10, 10, 60, 110), RED), ((100, 10, 150, 110), BLUE)])
    red = jersey_color_feature(frame, (10, 10, 60, 110))
    blue = jersey_color_feature(frame, (100, 10, 150, 110))
    assert red is not None and blue is not None
    assert np.linalg.norm(red - blue) > 50


def test_jersey_color_feature_ignores_tiny_boxes():
    frame = _frame_with_players([])
    assert jersey_color_feature(frame, (10, 10, 12, 14)) is None


def test_assign_teams_clusters_two_kits_and_leaves_referee_out():
    # Three red players, three blue players and a referee in yellow.
    colors = [RED, RED, RED, BLUE, BLUE, BLUE, YELLOW]
    boxes = [(10 + 60 * i, 10, 60 + 60 * i, 110) for i in range(len(colors))]
    frame = _frame_with_players(list(zip(boxes, colors)))
    samples = {i: [jersey_color_feature(frame, b)] * 5 for i, b in enumerate(boxes)}

    assignment = assign_teams(samples)
    teams = assignment.team_by_track

    assert teams[0] == teams[1] == teams[2]
    assert teams[3] == teams[4] == teams[5]
    assert teams[0] != teams[3]
    assert 6 not in teams
    assert set(assignment.team_colors) == {"A", "B"}
    assert all(c.startswith("#") and len(c) == 7 for c in assignment.team_colors.values())


def test_assign_teams_needs_enough_samples():
    feature = np.array([50.0, 150.0, 150.0], dtype=np.float32)
    assignment = assign_teams({1: [feature], 2: [feature]})
    assert assignment.team_by_track == {}


class FakeDetector:
    def __init__(self, persons):
        self.persons = persons

    def detect(self, frame):
        return FrameDetections(persons=self.persons, balls=[])


def test_frame_processor_collects_trajectories_and_colors():
    court = get_court_config(CourtType.PISO)
    calib = CourtCalibration(court, [(0, 0), (640, 0), (640, 480), (0, 480)])
    boxes = [((100, 100, 150, 220), RED), ((400, 100, 450, 220), BLUE)]
    frame = _frame_with_players(boxes)
    detector = FakeDetector(
        [Detection(xyxy=tuple(map(float, b)), confidence=0.9, class_id=COCO_PERSON_CLASS) for b, _ in boxes]
    )

    processor = FrameProcessor(detector, calib)
    result = TrackingResult(fps=6, duration_s=0, calibrated=True)
    for i in range(5):
        processor.process(frame, i / 6, result)

    track_ids = {f.track_id for f in result.player_frames}
    assert len(track_ids) == 2
    assert all(len(result.color_samples[t]) >= 3 for t in track_ids)
    assert len(assign_teams(result.color_samples).team_by_track) == 2


def test_loud_referee_color_does_not_hijack_a_team():
    # Four players per team plus a referee whose color is the most different
    # from everything: seeding with "farthest colors" would pick the referee.
    red = np.array([50.0, 200.0, 190.0])
    blue = np.array([35.0, 150.0, 60.0])
    samples = {i: [red + i] * 4 for i in range(4)}
    samples.update({10 + i: [blue - i] * 4 for i in range(4)})
    samples[99] = [np.array([100.0, 40.0, 250.0])] * 4

    assignment = assign_teams(samples)

    assert len({assignment.team_by_track[i] for i in range(4)}) == 1
    assert len({assignment.team_by_track[10 + i] for i in range(4)}) == 1
    assert assignment.team_by_track[0] != assignment.team_by_track[10]
    assert assignment.outliers == {99}
