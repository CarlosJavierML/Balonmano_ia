"""Automatic time sync between cameras from their audio tracks.

Cameras filming the same session hear the same sharp sounds -- the
referee's whistle, the ball bouncing, a shot hitting the goal, applause.
We turn each camera's audio into an "onset" signal (how suddenly loudness
rises, 100 values per second), then find the time shift that best lines up
the two signals (cross-correlation). Sharp, irregular events make the peak
unambiguous, and the method is insensitive to each camera's volume or
distance to the court.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import numpy as np

ENVELOPE_RATE_HZ = 100
AUDIO_SAMPLE_RATE = 8000
# Only the first minutes are needed to find the offset (and keep it fast).
MAX_AUDIO_SECONDS = 900
# Offsets searched: cameras started within this many seconds of each other.
MAX_OFFSET_S = 300.0
# How far the correlation peak must stand out (in standard deviations) to
# trust the result; below this we fall back to a zero/manual offset.
MIN_CONFIDENCE = 8.0


@dataclass
class SyncEstimate:
    offset_s: float  # add to the camera's time to get the reference camera's time
    confidence: float  # peak height in standard deviations

    @property
    def reliable(self) -> bool:
        return self.confidence >= MIN_CONFIDENCE


def ffmpeg_executable() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - not installed / no binary for this platform
        return None


def onset_envelope(samples: np.ndarray, sample_rate: int = AUDIO_SAMPLE_RATE) -> np.ndarray:
    """Onset strength at ENVELOPE_RATE_HZ: positive jumps of log energy."""
    hop = sample_rate // ENVELOPE_RATE_HZ
    n = len(samples) // hop
    if n < 2:
        return np.zeros(0, dtype=np.float32)
    frames = samples[: n * hop].astype(np.float32).reshape(n, hop)
    log_energy = np.log1p(np.sqrt(np.mean(frames**2, axis=1)))
    onset = np.maximum(np.diff(log_energy, prepend=log_energy[0]), 0.0)
    std = onset.std()
    return ((onset - onset.mean()) / std).astype(np.float32) if std > 0 else onset * 0


def extract_envelope(video_path: str) -> np.ndarray | None:
    """Onset envelope of a video's audio, or None if it has no audio track
    (or ffmpeg isn't available)."""
    ffmpeg = ffmpeg_executable()
    if ffmpeg is None:
        return None
    cmd = [
        ffmpeg, "-v", "error", "-nostdin", "-t", str(MAX_AUDIO_SECONDS), "-i", video_path,
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE), "-f", "s16le", "-",
    ]  # fmt: skip
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    samples = np.frombuffer(proc.stdout, dtype=np.int16)
    if proc.returncode != 0 or len(samples) < AUDIO_SAMPLE_RATE:  # no audio / < 1 s
        return None
    envelope = onset_envelope(samples)
    return envelope if envelope.any() else None


def estimate_offset(reference: np.ndarray, other: np.ndarray, max_offset_s: float = MAX_OFFSET_S) -> SyncEstimate:
    """Offset (seconds) to add to `other`'s timestamps to line it up with
    `reference`: an event heard at time T in `other` is at T + offset in
    `reference`."""
    n = len(reference) + len(other)
    size = 1 << (n - 1).bit_length()
    # corr[k] = sum_n reference[n + k] * other[n]  (circular, negative k wrap around)
    corr = np.fft.irfft(np.fft.rfft(reference, size) * np.conj(np.fft.rfft(other, size)), size)
    max_lag = int(max_offset_s * ENVELOPE_RATE_HZ)
    lags = np.concatenate([np.arange(0, min(max_lag, len(reference)) + 1), -np.arange(1, min(max_lag, len(other)) + 1)])
    values = corr[lags % size]
    best = int(np.argmax(values))
    std = values.std()
    confidence = float((values[best] - values.mean()) / std) if std > 0 else 0.0
    return SyncEstimate(offset_s=float(lags[best]) / ENVELOPE_RATE_HZ, confidence=confidence)
