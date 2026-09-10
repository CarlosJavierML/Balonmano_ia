"""Live camera / RTSP stream ingestion.

Runs the same detector+tracker used for uploaded videos, but incrementally
in a background thread, so the API can expose a "current stats" snapshot
while training is still happening. Frame reading blocks on network/camera
I/O, so this intentionally uses a plain `threading.Thread` rather than
asyncio -- FastAPI's event loop stays free to serve snapshot requests.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

from app.config import settings
from app.court import CourtCalibration, CourtConfig
from app.vision.detector import Detector
from app.vision.pipeline import BallFrame, TrackFrame, TrackingResult
from app.vision.tracker import BallTracker, PlayerTracker

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


@dataclass
class LiveState:
    result: TrackingResult
    started_at: float
    connected: bool = True
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class LiveSession:
    """Owns one background capture+analysis thread for one live match."""

    def __init__(
        self,
        stream_url: str,
        court: CourtConfig,
        calibration: CourtCalibration | None,
        target_fps: float | None = None,
    ):
        self.stream_url = stream_url
        self.court = court
        self.calibration = calibration
        self.target_fps = target_fps or settings.analysis_target_fps

        self.state = LiveState(
            result=TrackingResult(fps=self.target_fps, duration_s=0.0, calibrated=calibration is not None),
            started_at=time.time(),
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def snapshot(self) -> TrackingResult:
        with self.state.lock:
            # Shallow copies of the lists so the caller can read safely
            # without holding the lock while formatting a response.
            return TrackingResult(
                fps=self.state.result.fps,
                duration_s=time.time() - self.state.started_at,
                calibrated=self.state.result.calibrated,
                player_frames=list(self.state.result.player_frames),
                ball_frames=list(self.state.result.ball_frames),
            )

    def _run(self) -> None:
        if cv2 is None:
            self.state.error = "opencv-python is required for live streaming"
            self.state.connected = False
            return

        cap = cv2.VideoCapture(self.stream_url)
        if not cap.isOpened():
            self.state.error = f"Could not open stream: {self.stream_url}"
            self.state.connected = False
            return

        detector = Detector()
        player_tracker = PlayerTracker()
        ball_tracker = BallTracker()
        calib = self.calibration

        min_frame_interval = 1.0 / self.target_fps
        next_due = 0.0
        start = time.time()

        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    # Transient read failure or stream ended; back off briefly
                    # and retry rather than tearing down the session.
                    time.sleep(0.5)
                    continue

                now = time.time() - start
                if now < next_due:
                    continue
                next_due = now + min_frame_interval

                if calib is None:
                    h, w = frame.shape[:2]
                    from app.vision.pipeline import _fallback_calibration

                    calib = _fallback_calibration(self.court, w, h)

                detections = detector.detect(frame)
                tracked_players = player_tracker.update(detections.persons)
                ball_point_px = ball_tracker.update(detections.balls)

                with self.state.lock:
                    if tracked_players:
                        anchors_px = np.array(
                            [d.anchor_point for _, d in tracked_players], dtype=np.float32
                        )
                        world_pts = calib.pixel_to_world(anchors_px)
                        for (track_id, _), (wx, wy) in zip(tracked_players, world_pts):
                            self.state.result.player_frames.append(
                                TrackFrame(t=now, track_id=track_id, x=float(wx), y=float(wy))
                            )
                    if ball_point_px is not None:
                        world_pt = calib.pixel_to_world(np.array([ball_point_px], dtype=np.float32))[0]
                        self.state.result.ball_frames.append(
                            BallFrame(t=now, x=float(world_pt[0]), y=float(world_pt[1]))
                        )
        finally:
            cap.release()
            self.state.connected = False


class LiveSessionRegistry:
    """Process-wide registry so API routes can find the running session for
    a given match id."""

    def __init__(self) -> None:
        self._sessions: dict[int, LiveSession] = {}
        self._lock = threading.Lock()

    def add(self, match_id: int, session: LiveSession) -> None:
        with self._lock:
            self._sessions[match_id] = session

    def get(self, match_id: int) -> LiveSession | None:
        with self._lock:
            return self._sessions.get(match_id)

    def remove(self, match_id: int) -> None:
        with self._lock:
            self._sessions.pop(match_id, None)


live_registry = LiveSessionRegistry()
