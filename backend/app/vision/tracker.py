"""Multi-object tracking for players, built on ByteTrack (via `supervision`).

The ball is intentionally NOT run through this tracker: a fast, frequently
occluded single object is tracked far more reliably with a simple
"closest to last known position, else highest confidence" association plus
smoothing (see `pipeline.BallTracker`) than with a general-purpose
multi-object tracker tuned for many similar-looking targets.
"""

from __future__ import annotations

import numpy as np
import supervision as sv

from app.vision.detector import Detection


class PlayerTracker:
    def __init__(self) -> None:
        self._tracker = sv.ByteTrack()

    def update(self, persons: list[Detection]) -> list[tuple[int, Detection]]:
        """Returns (track_id, detection) pairs for this frame."""
        if not persons:
            empty = sv.Detections.empty()
            self._tracker.update_with_detections(empty)
            return []

        xyxy = np.array([d.xyxy for d in persons], dtype=np.float32)
        confidence = np.array([d.confidence for d in persons], dtype=np.float32)
        class_id = np.array([d.class_id for d in persons], dtype=int)

        detections = sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)
        tracked = self._tracker.update_with_detections(detections)

        results: list[tuple[int, Detection]] = []
        for i in range(len(tracked)):
            track_id = tracked.tracker_id[i]
            if track_id is None:
                continue
            det = Detection(
                xyxy=tuple(float(v) for v in tracked.xyxy[i].tolist()),
                confidence=float(tracked.confidence[i]),
                class_id=int(tracked.class_id[i]),
            )
            results.append((int(track_id), det))
        return results

    def reset(self) -> None:
        self._tracker = sv.ByteTrack()


class BallTracker:
    """Keeps a single smoothed ball position across frames.

    Handball is played fast; the ball detector will miss frames (motion
    blur, occlusion by a player's hand/body). We keep the last known
    position and only "jump" to a new detection close to it, or to the
    single best detection when we have no recent history -- this avoids
    the trajectory snapping to a spurious round object in the crowd/background.
    """

    def __init__(self, max_jump_px: float = 250.0, max_missed_frames: int = 15) -> None:
        self.max_jump_px = max_jump_px
        self.max_missed_frames = max_missed_frames
        self._last_point: tuple[float, float] | None = None
        self._missed = 0

    def update(self, balls: list[Detection]) -> tuple[float, float] | None:
        if not balls:
            self._missed += 1
            if self._missed > self.max_missed_frames:
                self._last_point = None
            return self._last_point

        if self._last_point is None:
            best = max(balls, key=lambda d: d.confidence)
            self._last_point = best.anchor_point
            self._missed = 0
            return self._last_point

        lx, ly = self._last_point
        best_det = min(
            balls,
            key=lambda d: (d.anchor_point[0] - lx) ** 2 + (d.anchor_point[1] - ly) ** 2,
        )
        bx, by = best_det.anchor_point
        dist = ((bx - lx) ** 2 + (by - ly) ** 2) ** 0.5
        if dist <= self.max_jump_px or self._missed > self.max_missed_frames:
            self._last_point = (bx, by)
            self._missed = 0
        else:
            self._missed += 1
        return self._last_point
