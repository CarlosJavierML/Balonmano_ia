"""Automatic suggestion of the 4 court corners from the painted lines.

Used as a starting point for calibration: the user still sees the result
and can drag any corner that is off before saving. The approach is plain
image processing, no model needed:

1. Keep bright, low-saturation pixels (white court lines).
2. Close small gaps so the outer boundary becomes one connected shape.
3. Take the connected shape with the largest convex hull -- the outer
   court boundary encloses every other line (center line, 6m/9m arcs).
4. Reduce that hull to a quadrilateral and order its corners as the
   calibration expects: top-left, top-right, bottom-right, bottom-left.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

# White line: low saturation, high brightness (HSV, OpenCV 8-bit ranges).
LINE_MAX_SATURATION = 70
LINE_MIN_VALUE = 170
# The court must cover a meaningful part of the frame to be trusted.
MIN_HULL_AREA_FRACTION = 0.08


def _order_corners(points: np.ndarray) -> list[tuple[float, float]]:
    """Orders 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = points.reshape(4, 2).astype(np.float64)
    by_y = pts[np.argsort(pts[:, 1])]
    top = by_y[:2][np.argsort(by_y[:2, 0])]
    bottom = by_y[2:][np.argsort(by_y[2:, 0])]
    ordered = [top[0], top[1], bottom[1], bottom[0]]
    return [(float(x), float(y)) for x, y in ordered]


def _hull_to_quad(hull: np.ndarray) -> np.ndarray:
    perimeter = cv2.arcLength(hull, closed=True)
    for fraction in np.linspace(0.01, 0.1, 19):
        approx = cv2.approxPolyDP(hull, fraction * perimeter, closed=True)
        if len(approx) == 4:
            return approx.reshape(4, 2)
    # Fallback: the extreme points along the two diagonals, which for a
    # court seen in perspective (trapezoid) are its four corners.
    pts = hull.reshape(-1, 2).astype(np.float64)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    return np.array([pts[np.argmin(s)], pts[np.argmax(d)], pts[np.argmax(s)], pts[np.argmin(d)]])


def suggest_court_corners(frame_bgr: np.ndarray) -> list[tuple[float, float]] | None:
    """Returns the 4 suggested court corners in pixels, or None if no
    convincing court boundary was found."""
    if cv2 is None:
        raise RuntimeError("opencv-python is required for court line detection")

    h, w = frame_bgr.shape[:2]
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, LINE_MIN_VALUE), (180, LINE_MAX_SATURATION, 255))

    # Close gaps (occluding players, worn paint) proportional to image size.
    k = max(3, int(round(min(h, w) / 120)) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    hulls = [cv2.convexHull(c) for c in contours]
    hull = max(hulls, key=cv2.contourArea)
    if cv2.contourArea(hull) < MIN_HULL_AREA_FRACTION * h * w:
        return None

    quad = _hull_to_quad(hull)
    return _order_corners(quad)
