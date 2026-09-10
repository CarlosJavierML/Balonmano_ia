"""Court geometry for indoor ("piso") and beach ("playa") handball.

The vision pipeline works in pixel coordinates. To turn raw detections into
meaningful physical stats (distance covered in meters, speed in km/h,
zone occupancy, heatmaps) we need to map pixel coordinates to real-world
court coordinates. We do that with a planar homography computed from four
user-provided reference points (the court corners, picked once per fixed
camera setup) mapped to the known real-world court corners.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 is an optional runtime dep for pure analysis tests
    cv2 = None  # type: ignore[assignment]


class CourtType(str, Enum):
    PISO = "piso"
    PLAYA = "playa"


@dataclass(frozen=True)
class CourtConfig:
    """Real-world court dimensions and key zones, in meters.

    Origin (0, 0) is the top-left corner of the playing area as seen from
    above, x growing along the length (goal-to-goal), y along the width.
    """

    name: str
    length_m: float
    width_m: float
    goal_width_m: float
    goal_area_radius_m: float  # 6m line (piso) / equivalent zone (playa)
    free_throw_radius_m: float | None  # 9m line, not applicable in playa

    @property
    def goal_center_y(self) -> float:
        return self.width_m / 2

    def goal_line_x(self, side: str) -> float:
        return 0.0 if side == "left" else self.length_m

    def is_in_goal_area(self, x: float, y: float, side: str) -> bool:
        """Whether a world point (x, y) lies inside a goal's shooting circle."""
        goal_x = self.goal_line_x(side)
        goal_y = self.goal_center_y
        return (x - goal_x) ** 2 + (y - goal_y) ** 2 <= self.goal_area_radius_m**2

    def is_goal(self, x: float, y: float, side: str, tolerance_m: float = 0.15) -> bool:
        """Whether a world point has crossed a goal line within the goal mouth."""
        goal_x = self.goal_line_x(side)
        within_line = abs(x - goal_x) <= tolerance_m
        half_width = self.goal_width_m / 2
        within_mouth = abs(y - self.goal_center_y) <= half_width
        return within_line and within_mouth


COURTS: dict[CourtType, CourtConfig] = {
    # Indoor handball (IHF): 40m x 20m court, 6m goal area, 9m free-throw line,
    # 3m x 2m goal.
    CourtType.PISO: CourtConfig(
        name="Pista de balonmano (indoor)",
        length_m=40.0,
        width_m=20.0,
        goal_width_m=3.0,
        goal_area_radius_m=6.0,
        free_throw_radius_m=9.0,
    ),
    # Beach handball: 27m x 12m playing area, 3m x 2m goal, no 6m/9m lines
    # -- we reuse a shooting-zone radius purely as a tactical heuristic
    # boundary (not an official painted line) to flag "shot" vs "long shot".
    CourtType.PLAYA: CourtConfig(
        name="Pista de balonmano playa",
        length_m=27.0,
        width_m=12.0,
        goal_width_m=3.0,
        goal_area_radius_m=6.0,
        free_throw_radius_m=None,
    ),
}


def get_court_config(court_type: CourtType) -> CourtConfig:
    return COURTS[court_type]


class CourtCalibration:
    """Pixel <-> world coordinate mapping for one fixed-camera setup.

    ``pixel_corners`` are the four court corners as seen by the camera, in
    this order: top-left, top-right, bottom-right, bottom-left (matching a
    bird's-eye view where "top" is one goal line and "bottom" the other).
    """

    def __init__(self, court: CourtConfig, pixel_corners: list[tuple[float, float]]):
        if len(pixel_corners) != 4:
            raise ValueError("Exactly 4 reference corners are required for calibration")
        if cv2 is None:
            raise RuntimeError("opencv-python is required to compute homographies")

        self.court = court
        world_corners = np.array(
            [
                [0.0, 0.0],
                [court.length_m, 0.0],
                [court.length_m, court.width_m],
                [0.0, court.width_m],
            ],
            dtype=np.float32,
        )
        src = np.array(pixel_corners, dtype=np.float32)
        self._homography, _ = cv2.findHomography(src, world_corners)
        if self._homography is None:
            raise ValueError("Could not compute homography from the given corners")

    def pixel_to_world(self, points_px: np.ndarray) -> np.ndarray:
        """Map an (N, 2) array of pixel points to (N, 2) world meters."""
        pts = points_px.reshape(-1, 1, 2).astype(np.float32)
        world = cv2.perspectiveTransform(pts, self._homography)
        return world.reshape(-1, 2)
