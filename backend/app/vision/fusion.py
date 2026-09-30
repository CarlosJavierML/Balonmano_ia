"""Multi-camera fusion: several per-camera trajectories -> one session.

Every camera is calibrated to the same court coordinate system (meters),
so after the per-camera vision pipeline all positions are already
comparable. What's left is deciding which camera-local tracks are the same
person and producing one set of trajectories the rest of the app (stats,
teams, events, reports) can use unchanged:

1. **Time alignment**: each camera's timestamps are shifted by its sync
   offset and snapped to a common time grid.
2. **Overlap merge**: where two cameras see the same area (typically around
   the center line), tracks from different cameras that stay within ~1 m of
   each other while both visible are the same person.
3. **Hand-off linking**: a track that ends (player leaves a camera's view, or
   the tracker lost them) and another that starts shortly after, close
   enough to be reachable at running speed and with a similar jersey color,
   are chained into one identity. This also repairs tracker ID switches.
4. **Output**: one position per person per time step (weighted towards the
   camera whose calibrated half contains the point, where the homography is
   most accurate) and one ball position per time step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.analysis.teams import MAX_SAMPLES_PER_TRACK
from app.court import CourtConfig, CourtRegion, region_contains
from app.vision.pipeline import BallFrame, TrackFrame, TrackingResult

# Detections this far outside a camera's calibrated region are dropped: the
# homography is extrapolating there and the player is at the image edge.
REGION_MARGIN_M = 4.0
# Weight of a detection outside its camera's own region (but within margin).
OUTSIDE_REGION_WEIGHT = 0.3

# Overlap merge: same person seen by two cameras at the same time.
MERGE_MIN_COOCCURRENCES = 3
MERGE_MAX_MEDIAN_DIST_M = 1.2
MERGE_MIN_AGREEMENT = 0.7  # fraction of shared instants within 2x the distance

# Hand-off linking: one track ends, another starts shortly after.
HANDOFF_MAX_GAP_S = 1.5
HANDOFF_MAX_SPEED_MS = 7.0  # fast handball sprint
HANDOFF_SLACK_M = 1.5  # calibration/tracking noise
HANDOFF_MAX_COLOR_DIST = 30.0  # Lab units

BALL_AGREE_DIST_M = 2.0
# Positions are interpolated onto the common time grid, but never across a
# gap longer than this (the person/ball was really not seen in between).
MAX_INTERP_GAP_S = 0.6


@dataclass
class CameraTracking:
    index: int
    region: CourtRegion
    offset_s: float  # added to this camera's timestamps to get session time
    result: TrackingResult


@dataclass
class FusionReport:
    cameras: list[dict] = field(default_factory=list)
    overlap_merges: int = 0
    handoffs: int = 0
    people: int = 0

    def as_dict(self) -> dict:
        return {
            "cameras": self.cameras,
            "overlap_merges": self.overlap_merges,
            "handoffs": self.handoffs,
            "people": self.people,
        }


TrackKey = tuple[int, int]  # (camera index, camera-local track id)


@dataclass
class _Track:
    key: TrackKey
    samples: dict[int, tuple[float, float, float]]  # bin -> (x, y, weight)
    colors: list[np.ndarray]

    @property
    def start(self) -> int:
        return min(self.samples)

    @property
    def end(self) -> int:
        return max(self.samples)


class _UnionFind:
    def __init__(self, keys) -> None:
        self.parent = {k: k for k in keys}

    def find(self, k):
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a, b) -> None:
        """Attaches a's root under b's root."""
        self.parent[self.find(a)] = self.find(b)


def _weight(court: CourtConfig, region: CourtRegion, x: float) -> float | None:
    if not region_contains(court, region, x, REGION_MARGIN_M):
        return None
    return 1.0 if region_contains(court, region, x) else OUTSIDE_REGION_WEIGHT


def _resample(times: np.ndarray, xs: np.ndarray, ys: np.ndarray, step: float) -> list[tuple[int, float, float]]:
    """Positions at the common grid instants (bin * step), linearly
    interpolated between a trajectory's own samples. Cameras sample at
    different instants (frame rates, sync offsets); snapping each sample to
    the nearest instant instead would shift it by up to half a step and
    show up as fake accelerations when switching cameras."""
    order = np.argsort(times)
    times, xs, ys = times[order], xs[order], ys[order]
    if len(times) == 1:
        return [(round(times[0] / step), float(xs[0]), float(ys[0]))]
    out = []
    first, last = int(np.ceil(times[0] / step - 1e-9)), int(np.floor(times[-1] / step + 1e-9))
    for b in range(first, last + 1):
        tb = b * step
        j = int(np.searchsorted(times, tb))
        if j < len(times) and abs(times[j] - tb) < 1e-9:
            out.append((b, float(xs[j]), float(ys[j])))
            continue
        lo, hi = j - 1, j
        if lo < 0 or hi >= len(times) or times[hi] - times[lo] > MAX_INTERP_GAP_S:
            continue
        a = (tb - times[lo]) / (times[hi] - times[lo])
        out.append((b, float(xs[lo] + a * (xs[hi] - xs[lo])), float(ys[lo] + a * (ys[hi] - ys[lo]))))
    return out


def _collect_tracks(
    cameras: list[CameraTracking], court: CourtConfig, step: float, base: float
) -> dict[TrackKey, _Track]:
    tracks: dict[TrackKey, _Track] = {}
    for cam in cameras:
        by_id: dict[int, list] = {}
        for f in cam.result.player_frames:
            by_id.setdefault(f.track_id, []).append((f.t + cam.offset_s - base, f.x, f.y))
        for track_id, samples in by_id.items():
            arr = np.array(samples, dtype=np.float64)
            resampled = {}
            for b, x, y in _resample(arr[:, 0], arr[:, 1], arr[:, 2], step):
                w = _weight(court, cam.region, x)
                if w is not None:
                    resampled[b] = (x, y, w)
            if resampled:
                key = (cam.index, track_id)
                tracks[key] = _Track(key, resampled, list(cam.result.color_samples.get(track_id, [])))
    return tracks


def _co_occurrence_cost(a: _Track, b: _Track) -> float | None:
    shared = a.samples.keys() & b.samples.keys()
    if len(shared) < MERGE_MIN_COOCCURRENCES:
        return None
    dists = np.array([np.hypot(a.samples[t][0] - b.samples[t][0], a.samples[t][1] - b.samples[t][1]) for t in shared])
    median = float(np.median(dists))
    if median > MERGE_MAX_MEDIAN_DIST_M:
        return None
    if np.mean(dists <= 2 * MERGE_MAX_MEDIAN_DIST_M) < MERGE_MIN_AGREEMENT:
        return None
    return median


def _conflicts(members_a: list[_Track], members_b: list[_Track]) -> bool:
    """Two tracks from the same camera visible at the same instant are two
    different people: their groups can never be merged."""
    for a in members_a:
        for b in members_b:
            if a.key[0] == b.key[0] and a.samples.keys() & b.samples.keys():
                return True
    return False


def _median_color(members: list[_Track]) -> np.ndarray | None:
    colors = [c for m in members for c in m.colors]
    return np.median(np.asarray(colors, dtype=np.float32), axis=0) if colors else None


def fuse_cameras(
    cameras: list[CameraTracking], court: CourtConfig, fps: float | None = None
) -> tuple[TrackingResult, FusionReport]:
    if not cameras:
        raise ValueError("At least one camera is required")
    fps = fps or min(c.result.fps for c in cameras)
    step = 1.0 / fps
    # Offsets are relative to camera 0 and may be negative (a camera that
    # started recording earlier): shift everything so session time >= 0.
    base = min(0.0, min(c.offset_s for c in cameras))

    report = FusionReport(
        cameras=[
            {
                "index": c.index,
                "region": CourtRegion(c.region).value,
                "offset_s": round(c.offset_s, 3),
                "tracks": len({f.track_id for f in c.result.player_frames}),
            }
            for c in cameras
        ]
    )

    tracks = _collect_tracks(cameras, court, step, base)
    uf = _UnionFind(tracks)
    members: dict[TrackKey, list[_Track]] = {k: [t] for k, t in tracks.items()}

    def merge(a: TrackKey, b: TrackKey) -> bool:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb or _conflicts(members[ra], members[rb]):
            return False
        uf.union(ra, rb)  # rb becomes the root
        members[rb] = members[rb] + members.pop(ra)
        return True

    # 1. Overlap merge between cameras, most convincing pairs first.
    keys = sorted(tracks, key=lambda k: tracks[k].start)
    candidates: list[tuple[float, TrackKey, TrackKey]] = []
    for i, ka in enumerate(keys):
        a = tracks[ka]
        for kb in keys[i + 1 :]:
            b = tracks[kb]
            if b.start > a.end:
                break  # sorted by start: no later track overlaps `a` in time
            if ka[0] == kb[0]:
                continue
            cost = _co_occurrence_cost(a, b)
            if cost is not None:
                candidates.append((cost, ka, kb))
    for _, ka, kb in sorted(candidates):
        if merge(ka, kb):
            report.overlap_merges += 1

    # 2. Hand-off linking between the resulting groups: chain a group that
    #    ends to one that starts shortly after, nearby, with a similar kit.
    def group_span(root):
        samples = {}
        for m in members[root]:
            for t, s in m.samples.items():
                if t not in samples or s[2] > samples[t][2]:
                    samples[t] = s
        first, last = min(samples), max(samples)
        return first, last, samples[first], samples[last]

    roots = list(members)
    spans = {r: group_span(r) for r in roots}
    colors = {r: _median_color(members[r]) for r in roots}
    max_gap_bins = HANDOFF_MAX_GAP_S / step
    links: list[tuple[float, object, object]] = []
    for r1 in roots:
        _, end1, _, last1 = spans[r1]
        for r2 in roots:
            if r1 == r2:
                continue
            start2, _, first2, _ = spans[r2]
            gap = start2 - end1
            if not 0 < gap <= max_gap_bins:
                continue
            dist = float(np.hypot(first2[0] - last1[0], first2[1] - last1[1]))
            if dist > HANDOFF_MAX_SPEED_MS * gap * step + HANDOFF_SLACK_M:
                continue
            color_dist = 0.0
            if colors[r1] is not None and colors[r2] is not None:
                color_dist = float(np.linalg.norm(colors[r1] - colors[r2]))
                if color_dist > HANDOFF_MAX_COLOR_DIST:
                    continue
            links.append((dist + 0.05 * color_dist + 0.2 * gap * step, r1, r2))

    has_next: set = set()
    has_prev: set = set()
    for _, r1, r2 in sorted(links, key=lambda link: link[0]):
        if r1 in has_next or r2 in has_prev:
            continue
        if merge(r1, r2):
            has_next.add(r1)
            has_prev.add(r2)
            report.handoffs += 1

    # 3. One trajectory per person, ids by order of first appearance.
    fused = TrackingResult(
        fps=fps,
        duration_s=max(c.result.duration_s + c.offset_s - base for c in cameras),
        calibrated=all(c.result.calibrated for c in cameras),
    )
    groups = sorted(members.values(), key=lambda ms: min(m.start for m in ms))
    for person_id, group in enumerate(groups, start=1):
        by_bin: dict[int, list[tuple[float, float, float]]] = {}
        for m in group:
            for t, s in m.samples.items():
                by_bin.setdefault(t, []).append(s)
        for t in sorted(by_bin):
            pts = np.array(by_bin[t])
            w = pts[:, 2]
            x = float(np.average(pts[:, 0], weights=w))
            y = float(np.average(pts[:, 1], weights=w))
            fused.player_frames.append(TrackFrame(t=round(t * step, 3), track_id=person_id, x=x, y=y))
        colors_all = [c for m in group for c in m.colors]
        if colors_all:
            fused.color_samples[person_id] = colors_all[:MAX_SAMPLES_PER_TRACK]
    fused.player_frames.sort(key=lambda f: (f.t, f.track_id))
    report.people = len(groups)

    fused.ball_frames = _fuse_ball(cameras, court, step, base)
    return fused, report


def _fuse_ball(cameras: list[CameraTracking], court: CourtConfig, step: float, base: float) -> list[BallFrame]:
    by_bin: dict[int, list[tuple[float, float, float]]] = {}
    for cam in cameras:
        if not cam.result.ball_frames:
            continue
        arr = np.array([(f.t + cam.offset_s - base, f.x, f.y) for f in cam.result.ball_frames], dtype=np.float64)
        for b, x, y in _resample(arr[:, 0], arr[:, 1], arr[:, 2], step):
            w = _weight(court, cam.region, x)
            if w is not None:
                by_bin.setdefault(b, []).append((x, y, w))

    balls: list[BallFrame] = []
    for t in sorted(by_bin):
        cands = sorted(by_bin[t], key=lambda c: -c[2])
        best = cands[0]
        # Average with other cameras that agree; ignore ones that don't (a
        # camera seeing a different round object, or a stale position).
        agreeing = [c for c in cands if np.hypot(c[0] - best[0], c[1] - best[1]) <= BALL_AGREE_DIST_M]
        pts = np.array(agreeing)
        x = float(np.average(pts[:, 0], weights=pts[:, 2]))
        y = float(np.average(pts[:, 1], weights=pts[:, 2]))
        balls.append(BallFrame(t=round(t * step, 3), x=x, y=y))
    return balls
