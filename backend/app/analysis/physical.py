"""Physical performance stats derived from world-space player trajectories.

Pure functions over plain data (no cv2/YOLO dependency) so they're cheap to
unit test with synthetic trajectories.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise

from app.court import CourtConfig
from app.vision.pipeline import TrackFrame

# A human sprinting flat-out over a handball court rarely sustains much more
# than ~30 km/h in short bursts. Anything faster between two samples is
# almost certainly a tracking glitch (ID switch, calibration jitter) rather
# than real movement, so we exclude it from distance/speed accumulation.
MAX_PLAUSIBLE_SPEED_KMH = 30.0
SPRINT_THRESHOLD_KMH = 18.0
SPRINT_MIN_DURATION_S = 0.8


@dataclass
class PlayerPhysicalStats:
    track_id: int
    distance_m: float = 0.0
    avg_speed_kmh: float = 0.0
    max_speed_kmh: float = 0.0
    sprint_count: int = 0
    time_in_zones: dict[str, float] = field(default_factory=dict)
    heatmap_grid: list[list[int]] = field(default_factory=list)


def _zone_for_x(x: float, court: CourtConfig) -> str:
    third = court.length_m / 3
    if x < third:
        return "tercio_defensivo"
    if x < 2 * third:
        return "tercio_medio"
    return "tercio_ofensivo"


def _heatmap_grid(points: list[tuple[float, float]], court: CourtConfig, cols: int = 20, rows: int = 10) -> list[list[int]]:
    grid = [[0] * cols for _ in range(rows)]
    if not points:
        return grid
    cell_w = court.length_m / cols
    cell_h = court.width_m / rows
    for x, y in points:
        col = min(cols - 1, max(0, int(x / cell_w))) if cell_w else 0
        row = min(rows - 1, max(0, int(y / cell_h))) if cell_h else 0
        grid[row][col] += 1
    return grid


def compute_player_stats(
    player_frames: list[TrackFrame], court: CourtConfig
) -> dict[int, PlayerPhysicalStats]:
    by_track: dict[int, list[TrackFrame]] = defaultdict(list)
    for f in player_frames:
        by_track[f.track_id].append(f)

    stats: dict[int, PlayerPhysicalStats] = {}
    for track_id, frames in by_track.items():
        frames.sort(key=lambda f: f.t)
        s = PlayerPhysicalStats(track_id=track_id)
        zone_time: dict[str, float] = defaultdict(float)
        speeds_kmh: list[float] = []

        for prev, curr in pairwise(frames):
            dt = curr.t - prev.t
            if dt <= 0:
                continue
            dist = ((curr.x - prev.x) ** 2 + (curr.y - prev.y) ** 2) ** 0.5
            speed_kmh = (dist / dt) * 3.6

            zone_time[_zone_for_x(prev.x, court)] += dt

            if speed_kmh > MAX_PLAUSIBLE_SPEED_KMH:
                continue  # likely a tracking glitch; skip this segment
            s.distance_m += dist
            speeds_kmh.append(speed_kmh)

        if frames:
            zone_time[_zone_for_x(frames[-1].x, court)] += 0.0  # ensure key exists

        s.time_in_zones = dict(zone_time)
        s.heatmap_grid = _heatmap_grid([(f.x, f.y) for f in frames], court)

        if speeds_kmh:
            s.avg_speed_kmh = sum(speeds_kmh) / len(speeds_kmh)
            s.max_speed_kmh = max(speeds_kmh)
            s.sprint_count = _count_sprints(frames, speeds_kmh)

        stats[track_id] = s

    return stats


def _count_sprints(frames: list[TrackFrame], speeds_kmh: list[float]) -> int:
    """Counts distinct bouts where speed stays above SPRINT_THRESHOLD_KMH
    for at least SPRINT_MIN_DURATION_S, merging consecutive fast segments."""
    sprints = 0
    bout_duration = 0.0
    for (prev, curr), speed in zip(pairwise(frames), speeds_kmh):
        dt = curr.t - prev.t
        if speed >= SPRINT_THRESHOLD_KMH:
            bout_duration += dt
        else:
            if bout_duration >= SPRINT_MIN_DURATION_S:
                sprints += 1
            bout_duration = 0.0
    if bout_duration >= SPRINT_MIN_DURATION_S:
        sprints += 1
    return sprints
