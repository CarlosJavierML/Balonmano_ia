import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.vision.court_lines import suggest_court_corners  # noqa: E402

TRAPEZOID = [(240, 140), (1040, 140), (1230, 650), (50, 650)]


def _court_frame(with_players: bool = True) -> np.ndarray:
    frame = np.full((720, 1280, 3), (50, 110, 170), dtype=np.uint8)  # orange-ish floor
    frame[:100] = (40, 40, 40)  # dark stands above the court
    cv2.polylines(frame, [np.array(TRAPEZOID)], True, (245, 245, 245), 4)
    cv2.line(frame, (640, 140), (640, 650), (245, 245, 245), 4)  # center line
    cv2.ellipse(frame, (145, 395), (120, 150), 0, -90, 90, (245, 245, 245), 3)  # 6m arc
    if with_players:
        # Players occluding part of the far sideline and a goal line.
        cv2.rectangle(frame, (500, 100), (540, 220), (30, 30, 200), -1)
        cv2.rectangle(frame, (1100, 380), (1150, 520), (200, 60, 30), -1)
    return frame


def test_suggests_the_outer_court_corners_in_calibration_order():
    corners = suggest_court_corners(_court_frame())

    assert corners is not None
    for (x, y), (ex, ey) in zip(corners, TRAPEZOID):
        assert abs(x - ex) <= 12 and abs(y - ey) <= 12


def test_returns_none_without_court_lines():
    frame = np.full((480, 640, 3), (50, 110, 170), dtype=np.uint8)
    assert suggest_court_corners(frame) is None
