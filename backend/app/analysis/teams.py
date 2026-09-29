"""Team assignment by jersey-color clustering.

For every tracked player we sample the dominant color of the upper torso
(the jersey) over several frames, take a robust per-track median, and
cluster all tracks into two groups with a tiny deterministic k-means.
Tracks whose color is far from both team centroids (typically referees,
goalkeepers in a different kit, or people on the bench) are left without a
team instead of being forced into one.

Colors are compared in CIE Lab space, where Euclidean distance roughly
matches perceived color difference, so "white vs. dark blue" and "red vs.
orange" are both separated sensibly without hand-tuned hue thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

TEAM_LABELS = ("A", "B")

# Minimum color samples a track needs before we trust its jersey color.
MIN_SAMPLES_PER_TRACK = 3
# Max samples kept per track (older samples are enough; caps memory in
# long live sessions).
MAX_SAMPLES_PER_TRACK = 40
# A track farther than this (in Lab units) from its nearest team centroid
# is considered "no team" (referee, different-kit goalkeeper, bench...).
OUTLIER_DISTANCE_LAB = 45.0


@dataclass
class TeamAssignment:
    team_by_track: dict[int, str]
    # Representative jersey color per team, as "#rrggbb", for UI/report chips.
    team_colors: dict[str, str]


def jersey_color_feature(frame_bgr: np.ndarray, xyxy: tuple[float, float, float, float]) -> np.ndarray | None:
    """Median Lab color of the jersey region of a person bounding box.

    Uses the torso band (roughly 15%-50% of the box height, central 60% of
    its width) to avoid the head, the shorts and the court floor behind
    the player's legs.
    """
    if cv2 is None:
        return None
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = xyxy
    bw, bh = x2 - x1, y2 - y1
    if bw < 4 or bh < 8:
        return None

    cx1 = int(max(0, x1 + 0.2 * bw))
    cx2 = int(min(w, x2 - 0.2 * bw))
    cy1 = int(max(0, y1 + 0.15 * bh))
    cy2 = int(min(h, y1 + 0.5 * bh))
    if cx2 - cx1 < 2 or cy2 - cy1 < 2:
        return None

    crop = frame_bgr[cy1:cy2, cx1:cx2]
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    # OpenCV 8-bit Lab stores L in [0, 255]; rescale to the usual [0, 100]
    # so lightness doesn't dominate the distance over the a/b chroma axes.
    lab[:, 0] *= 100.0 / 255.0
    return np.median(lab, axis=0)


def _lab_to_hex(lab: np.ndarray) -> str:
    if cv2 is None:
        return "#888888"
    pixel = np.array([[[lab[0] * 255.0 / 100.0, lab[1], lab[2]]]], dtype=np.float32)
    pixel = np.clip(pixel, 0, 255).astype(np.uint8)
    b, g, r = cv2.cvtColor(pixel, cv2.COLOR_LAB2BGR)[0, 0]
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def _kmeans_two(points: np.ndarray, iterations: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic 2-means: seeds with the two mutually farthest points."""
    dists = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)
    i, j = np.unravel_index(np.argmax(dists), dists.shape)
    centroids = np.stack([points[i], points[j]]).astype(np.float64)

    labels: np.ndarray | None = None
    for _ in range(iterations):
        d = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=-1)
        new_labels = np.argmin(d, axis=1)
        if labels is not None and np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for k in range(2):
            members = points[labels == k]
            if len(members):
                centroids[k] = members.mean(axis=0)
    assert labels is not None
    return centroids, labels


def assign_teams(color_samples: dict[int, list[np.ndarray]]) -> TeamAssignment:
    """Clusters tracks into two teams from their jersey color samples."""
    track_ids: list[int] = []
    features: list[np.ndarray] = []
    for track_id, samples in color_samples.items():
        if len(samples) < MIN_SAMPLES_PER_TRACK:
            continue
        track_ids.append(track_id)
        features.append(np.median(np.asarray(samples, dtype=np.float32), axis=0))

    if len(features) < 2:
        return TeamAssignment(team_by_track={}, team_colors={})

    points = np.asarray(features, dtype=np.float64)
    centroids, labels = _kmeans_two(points)

    # Order teams deterministically (lighter jersey = "A") so labels are
    # stable across re-analyses of the same footage.
    order = np.argsort(-centroids[:, 0])
    rank = {int(k): TEAM_LABELS[pos] for pos, k in enumerate(order)}

    team_by_track: dict[int, str] = {}
    for track_id, point, label in zip(track_ids, points, labels):
        if np.linalg.norm(point - centroids[label]) > OUTLIER_DISTANCE_LAB:
            continue
        team_by_track[track_id] = rank[int(label)]

    team_colors = {rank[k]: _lab_to_hex(centroids[k]) for k in range(2)}
    return TeamAssignment(team_by_track=team_by_track, team_colors=team_colors)
