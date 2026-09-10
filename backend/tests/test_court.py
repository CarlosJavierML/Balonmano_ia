import numpy as np
import pytest

from app.court import CourtCalibration, CourtType, get_court_config


def test_pixel_to_world_roundtrip_on_corners():
    court = get_court_config(CourtType.PISO)
    pixel_corners = [(100, 50), (1180, 50), (1180, 670), (100, 670)]
    calib = CourtCalibration(court, pixel_corners)

    world = calib.pixel_to_world(np.array(pixel_corners, dtype=np.float32))

    expected = np.array(
        [[0, 0], [court.length_m, 0], [court.length_m, court.width_m], [0, court.width_m]]
    )
    assert np.allclose(world, expected, atol=1e-3)


def test_pixel_to_world_center_point():
    court = get_court_config(CourtType.PISO)
    pixel_corners = [(0, 0), (1000, 0), (1000, 500), (0, 500)]
    calib = CourtCalibration(court, pixel_corners)

    center_world = calib.pixel_to_world(np.array([[500, 250]], dtype=np.float32))[0]
    assert center_world[0] == pytest.approx(court.length_m / 2, abs=0.05)
    assert center_world[1] == pytest.approx(court.width_m / 2, abs=0.05)


def test_calibration_requires_exactly_four_corners():
    court = get_court_config(CourtType.PISO)
    with pytest.raises(ValueError):
        CourtCalibration(court, [(0, 0), (1, 1)])


def test_is_in_goal_area():
    court = get_court_config(CourtType.PISO)
    # Right in front of the left goal, well within the 6m arc.
    assert court.is_in_goal_area(2.0, court.goal_center_y, "left")
    # Center of the court, far from either goal.
    assert not court.is_in_goal_area(court.length_m / 2, court.goal_center_y, "left")


def test_is_goal_within_goal_mouth():
    court = get_court_config(CourtType.PISO)
    assert court.is_goal(0.0, court.goal_center_y, "left")
    # Same x (on the goal line) but far outside the goal posts width-wise.
    assert not court.is_goal(0.0, court.width_m - 0.1, "left")
