"""Team assignment by jersey-color clustering.

For every tracked player we sample the dominant color of the upper torso
(the jersey) over several frames, take a robust per-track median, and
cluster all tracks into two teams: the two largest groups of similar
colors seed the teams, refined with a small deterministic 2-means.
Tracks whose color is far from both team centroids (typically referees,
goalkeepers in a different kit, or people on the bench) are left without a
team instead of being forced into one.

Colors are compared in CIE Lab space, where Euclidean distance roughly
matches perceived color difference, so "white vs. dark blue" and "red vs.
orange" are both separated sensibly without hand-tuned hue thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
# Two tracks this close in Lab are considered the same kit when grouping.
COLOR_LINK_DISTANCE_LAB = 20.0


@dataclass
class TeamAssignment:
    team_by_track: dict[int, str]
    # Representative jersey color per team, as "#rrggbb", for UI/report chips.
    team_colors: dict[str, str]
    # Tracks with a reliable color that matches neither team (referees,
    # goalkeepers in a different kit, bench...).
    outliers: set[int] = field(default_factory=set)


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


def _color_groups(points: np.ndarray) -> list[np.ndarray]:
    """Single-linkage grouping: tracks whose colors are within
    COLOR_LINK_DISTANCE_LAB of each other (directly or through a chain of
    similar tracks) end up in the same group. Returns index arrays,
    largest group first."""
    n = len(points)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    dists = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)
    for i in range(n):
        for j in range(i + 1, n):
            if dists[i, j] <= COLOR_LINK_DISTANCE_LAB:
                parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    # Largest first; ties broken by first index for determinism.
    return [np.array(g) for g in sorted(groups.values(), key=lambda g: (-len(g), g[0]))]


def _initial_centroids(points: np.ndarray) -> np.ndarray:
    """Seeds the two team colors.

    The two largest groups of similar colors are the two teams: each team
    has several players in the same kit, while referees and goalkeepers in
    a different kit form small groups of their own. Seeding with "the two
    most different colors" instead would let a single referee in a loud
    color hijack one of the two teams.
    """
    groups = _color_groups(points)
    if len(groups) >= 2 and len(groups[1]) >= 2:
        return np.stack([points[groups[0]].mean(axis=0), points[groups[1]].mean(axis=0)])
    # Kits too similar to separate by grouping (or too few players): fall
    # back to the two mutually farthest colors.
    dists = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)
    i, j = np.unravel_index(np.argmax(dists), dists.shape)
    return np.stack([points[i], points[j]]).astype(np.float64)


def _two_team_centroids(points: np.ndarray, iterations: int = 20) -> np.ndarray:
    """2-means refined on inliers only, so outliers don't drag the team colors."""
    centroids = _initial_centroids(points)
    for _ in range(iterations):
        d = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=-1)
        labels = np.argmin(d, axis=1)
        inlier = d[np.arange(len(points)), labels] <= OUTLIER_DISTANCE_LAB
        updated = centroids.copy()
        for k in range(2):
            members = points[(labels == k) & inlier]
            if len(members):
                updated[k] = members.mean(axis=0)
        if np.allclose(updated, centroids):
            break
        centroids = updated
    return centroids


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
    centroids = _two_team_centroids(points)
    labels = np.argmin(np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=-1), axis=1)

    # Order teams deterministically (lighter jersey = "A") so labels are
    # stable across re-analyses of the same footage.
    order = np.argsort(-centroids[:, 0])
    rank = {int(k): TEAM_LABELS[pos] for pos, k in enumerate(order)}

    team_by_track: dict[int, str] = {}
    outliers: set[int] = set()
    for track_id, point, label in zip(track_ids, points, labels):
        if np.linalg.norm(point - centroids[label]) > OUTLIER_DISTANCE_LAB:
            outliers.add(track_id)
            continue
        team_by_track[track_id] = rank[int(label)]

    team_colors = {rank[k]: _lab_to_hex(centroids[k]) for k in range(2)}
    return TeamAssignment(team_by_track=team_by_track, team_colors=team_colors, outliers=outliers)
