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
from app.court import CourtCalibration, CourtConfig, CourtRegion
from app.vision.detector import Detector
from app.vision.fusion import CameraTracking, fuse_cameras
from app.vision.pipeline import FrameProcessor, TrackingResult, _fallback_calibration

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
        region: CourtRegion = CourtRegion.FULL,
        clock_start: float | None = None,
    ):
        """``clock_start`` (a `time.time()` value) lets several cameras of the
        same session share one clock, so their timestamps are already in sync."""
        self.stream_url = stream_url
        self.court = court
        self.calibration = calibration
        self.region = CourtRegion(region)
        self.target_fps = target_fps or settings.analysis_target_fps

        self.state = LiveState(
            result=TrackingResult(fps=self.target_fps, duration_s=0.0, calibrated=calibration is not None),
            started_at=clock_start if clock_start is not None else time.time(),
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Signals the capture thread to exit without waiting for it."""
        self._stop_event.set()

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
                color_samples={k: list(v) for k, v in self.state.result.color_samples.items()},
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
        processor: FrameProcessor | None = None
        # Owned by this thread; copied into the shared state under the lock.
        color_samples: dict[int, list[np.ndarray]] = {}

        min_frame_interval = 1.0 / self.target_fps
        next_due = 0.0
        start = self.state.started_at

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

                if processor is None:
                    calib = self.calibration
                    if calib is None:
                        h, w = frame.shape[:2]
                        calib = _fallback_calibration(self.court, w, h, self.region)
                    processor = FrameProcessor(detector, calib)

                # Detection runs outside the lock (it's the slow part); only
                # appending to the shared result is done while holding it.
                frame_result = TrackingResult(fps=self.target_fps, duration_s=0.0, calibrated=True)
                frame_result.color_samples = color_samples
                processor.process(frame, now, frame_result)
                with self.state.lock:
                    self.state.result.player_frames.extend(frame_result.player_frames)
                    self.state.result.ball_frames.extend(frame_result.ball_frames)
                    for f in frame_result.player_frames:
                        self.state.result.color_samples[f.track_id] = list(color_samples.get(f.track_id, []))
        finally:
            cap.release()
            self.state.connected = False


def grab_preview_frame(stream_url: str, max_attempts: int = 25) -> bytes:
    """Reads a single frame from a stream/camera and returns it JPEG-encoded."""
    if cv2 is None:
        raise RuntimeError("opencv-python is required for live streaming")
    cap = cv2.VideoCapture(stream_url)
    try:
        if not cap.isOpened():
            raise ValueError(f"No se pudo abrir el stream: {stream_url}")
        # The first reads of some IP cameras return empty/grey frames while
        # the decoder syncs to a keyframe; retry a few times.
        for _ in range(max_attempts):
            ok, frame = cap.read()
            if ok and frame is not None and frame.size:
                encoded_ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if encoded_ok:
                    return buf.tobytes()
        raise ValueError("El stream no devolvió ninguna imagen")
    finally:
        cap.release()


class MultiCameraLiveSession:
    """Several cameras of one live session: one capture thread per camera on
    a shared clock. `snapshot()` returns the fused view (same interface as
    `LiveSession`), `camera_snapshots()` each camera's own trajectories."""

    def __init__(self, cameras: list[tuple[int, LiveSession]], court: CourtConfig) -> None:
        self.cameras = cameras  # (camera index, session), index 0 first
        self.court = court

    def start(self) -> None:
        for _, cam in self.cameras:
            cam.start()

    def stop(self) -> None:
        # Signal every camera first so they all stop at (about) the same time.
        for _, cam in self.cameras:
            cam.request_stop()
        for _, cam in self.cameras:
            cam.stop()

    def camera_snapshots(self) -> list[tuple[int, TrackingResult]]:
        return [(index, cam.snapshot()) for index, cam in self.cameras]

    def snapshot(self) -> TrackingResult:
        fused, _ = fuse_cameras(
            [
                CameraTracking(index, cam.region, 0.0, result)
                for (index, cam), (_, result) in zip(self.cameras, self.camera_snapshots())
            ],
            self.court,
        )
        return fused


class LiveSessionRegistry:
    """Process-wide registry so API routes can find the running session for
    a given match id."""

    def __init__(self) -> None:
        self._sessions: dict[int, LiveSession] = {}
        self._lock = threading.Lock()

    def add(self, match_id: int, session: LiveSession | MultiCameraLiveSession) -> None:
        with self._lock:
            self._sessions[match_id] = session

    def get(self, match_id: int) -> LiveSession | MultiCameraLiveSession | None:
        with self._lock:
            return self._sessions.get(match_id)

    def remove(self, match_id: int) -> None:
        with self._lock:
            self._sessions.pop(match_id, None)


live_registry = LiveSessionRegistry()
