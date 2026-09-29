"""End-to-end vision pipeline: video frames -> world-space trajectories.

This module only produces *trajectories* (who was where, when, and where
the ball was). Turning that into physical stats and tactical events is
handled by `app.analysis`, so this stays swappable (e.g. replacing YOLO+
ByteTrack with a different detector/tracker later touches only this file).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from app.analysis.teams import MAX_SAMPLES_PER_TRACK, jersey_color_feature

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
    # Jersey color samples (Lab) per track id, for team assignment.
    color_samples: dict[int, list[np.ndarray]] = field(default_factory=dict)


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


class FrameProcessor:
    """Runs detection + tracking on one frame and appends world-space
    trajectories and jersey color samples to a `TrackingResult`.

    Shared by the uploaded-video pipeline and the live stream session so
    both produce exactly the same data.
    """

    def __init__(self, detector: Detector, calibration: CourtCalibration) -> None:
        self.detector = detector
        self.calibration = calibration
        self.player_tracker = PlayerTracker()
        self.ball_tracker = BallTracker()

    def process(self, frame: np.ndarray, t: float, result: TrackingResult) -> None:
        detections = self.detector.detect(frame)

        tracked_players = self.player_tracker.update(detections.persons)
        if tracked_players:
            anchors_px = np.array([det.anchor_point for _, det in tracked_players], dtype=np.float32)
            world_pts = self.calibration.pixel_to_world(anchors_px)
            for (track_id, det), (wx, wy) in zip(tracked_players, world_pts):
                result.player_frames.append(TrackFrame(t=t, track_id=track_id, x=float(wx), y=float(wy)))
                samples = result.color_samples.setdefault(track_id, [])
                if len(samples) < MAX_SAMPLES_PER_TRACK:
                    feature = jersey_color_feature(frame, det.xyxy)
                    if feature is not None:
                        samples.append(feature)

        ball_point_px = self.ball_tracker.update(detections.balls)
        if ball_point_px is not None:
            world_pt = self.calibration.pixel_to_world(np.array([ball_point_px], dtype=np.float32))[0]
            result.ball_frames.append(BallFrame(t=t, x=float(world_pt[0]), y=float(world_pt[1])))


class VideoAnalysisPipeline:
    def __init__(self, target_fps: float | None = None):
        self.target_fps = target_fps or settings.analysis_target_fps
        self.detector = Detector()

    def analyze(
        self,
        video_path: str,
        court: CourtConfig,
        calibration: CourtCalibration | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> TrackingResult:
        """Analyzes a video file. ``on_progress`` (if given) is called from
        this thread with the fraction of the video processed, in [0, 1]."""
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

        processor = FrameProcessor(self.detector, calib)
        result = TrackingResult(fps=effective_fps, duration_s=duration_s, calibrated=calibrated)

        frame_idx = 0
        try:
            while True:
                if frame_idx % frame_stride != 0:
                    # grab() skips decoding frames we won't analyze, which is
                    # much cheaper than read() on long high-FPS footage.
                    if not cap.grab():
                        break
                    frame_idx += 1
                    continue

                ok, frame = cap.read()
                if not ok:
                    break

                processor.process(frame, frame_idx / source_fps, result)

                if on_progress is not None and total_frames > 0:
                    on_progress(min(1.0, frame_idx / total_frames))
                frame_idx += 1
        finally:
            cap.release()

        return result
