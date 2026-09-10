"""End-to-end vision pipeline: video frames -> world-space trajectories.

This module only produces *trajectories* (who was where, when, and where
the ball was). Turning that into physical stats and tactical events is
handled by `app.analysis`, so this stays swappable (e.g. replacing YOLO+
ByteTrack with a different detector/tracker later touches only this file).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import settings
from app.court import CourtCalibration, CourtConfig
from app.vision.detector import Detector
from app.vision.tracker import BallTracker, PlayerTracker

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


@dataclass
class TrackFrame:
    t: float
    track_id: int
    x: float
    y: float


@dataclass
class BallFrame:
    t: float
    x: float
    y: float


@dataclass
class TrackingResult:
    fps: float
    duration_s: float
    calibrated: bool
    player_frames: list[TrackFrame] = field(default_factory=list)
    ball_frames: list[BallFrame] = field(default_factory=list)


def _fallback_calibration(court: CourtConfig, frame_width: int, frame_height: int) -> CourtCalibration:
    """Best-effort pixel->world mapping when no manual calibration was given:
    assumes the fixed camera frames the whole court edge-to-edge. Good enough
    for a quick first look; real sessions should calibrate the 4 corners for
    accurate distances/speeds (perspective distortion otherwise skews far-side
    measurements)."""

    corners = [
        (0, 0),
        (frame_width, 0),
        (frame_width, frame_height),
        (0, frame_height),
    ]
    return CourtCalibration(court, corners)


class VideoAnalysisPipeline:
    def __init__(self, target_fps: float | None = None):
        self.target_fps = target_fps or settings.analysis_target_fps
        self.detector = Detector()

    def analyze(
        self,
        video_path: str,
        court: CourtConfig,
        calibration: CourtCalibration | None = None,
    ) -> TrackingResult:
        if cv2 is None:
            raise RuntimeError("opencv-python is required to analyze video files")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {video_path}")

        source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_s = total_frames / source_fps if source_fps else 0.0

        calibrated = calibration is not None
        calib = calibration or _fallback_calibration(court, frame_width, frame_height)

        frame_stride = max(1, round(source_fps / self.target_fps))
        effective_fps = source_fps / frame_stride

        player_tracker = PlayerTracker()
        ball_tracker = BallTracker()

        result = TrackingResult(fps=effective_fps, duration_s=duration_s, calibrated=calibrated)

        frame_idx = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_idx % frame_stride != 0:
                    frame_idx += 1
                    continue

                t = frame_idx / source_fps
                detections = self.detector.detect(frame)

                tracked_players = player_tracker.update(detections.persons)
                if tracked_players:
                    anchors_px = np.array(
                        [det.anchor_point for _, det in tracked_players], dtype=np.float32
                    )
                    world_pts = calib.pixel_to_world(anchors_px)
                    for (track_id, _), (wx, wy) in zip(tracked_players, world_pts):
                        result.player_frames.append(TrackFrame(t=t, track_id=track_id, x=float(wx), y=float(wy)))

                ball_point_px = ball_tracker.update(detections.balls)
                if ball_point_px is not None:
                    world_pt = calib.pixel_to_world(np.array([ball_point_px], dtype=np.float32))[0]
                    result.ball_frames.append(BallFrame(t=t, x=float(world_pt[0]), y=float(world_pt[1])))

                frame_idx += 1
        finally:
            cap.release()

        return result
