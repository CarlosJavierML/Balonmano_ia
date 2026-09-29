import subprocess
import wave

import numpy as np
import pytest

from app.vision.audio_sync import (
    AUDIO_SAMPLE_RATE,
    ENVELOPE_RATE_HZ,
    estimate_offset,
    extract_envelope,
    ffmpeg_executable,
    onset_envelope,
)

RNG = np.random.default_rng(7)
EVENT_TIMES = np.sort(RNG.uniform(1, 55, size=25))  # whistles, bounces...


def _audio(start_s: float, duration_s: float, gain: float) -> np.ndarray:
    """What a camera that started recording at session time `start_s` hears."""
    t = np.arange(int(duration_s * AUDIO_SAMPLE_RATE)) / AUDIO_SAMPLE_RATE
    signal = RNG.normal(0, 300, size=t.size)  # crowd/background noise
    for ev in EVENT_TIMES - start_s:
        mask = (t >= ev) & (t < ev + 0.08)
        signal[mask] += 12000 * np.sin(2 * np.pi * 2500 * t[mask])  # short loud burst
    return np.clip(signal * gain, -32768, 32767).astype(np.int16)


@pytest.mark.parametrize("start_other", [3.25, -4.5, 0.0])
def test_estimates_offset_between_two_recordings(start_other):
    reference = onset_envelope(_audio(0.0, 60, gain=1.0))
    other = onset_envelope(_audio(start_other, 50, gain=0.4))  # quieter, farther camera

    estimate = estimate_offset(reference, other)

    # Event at `other` time T happened at session (= reference) time T + start_other.
    assert estimate.offset_s == pytest.approx(start_other, abs=1.0 / ENVELOPE_RATE_HZ + 1e-9)
    assert estimate.reliable


def test_unrelated_recordings_are_not_reliable():
    reference = onset_envelope(_audio(0.0, 60, gain=1.0))
    noise = onset_envelope(RNG.normal(0, 300, size=50 * AUDIO_SAMPLE_RATE).astype(np.int16))

    assert not estimate_offset(reference, noise).reliable


@pytest.mark.skipif(ffmpeg_executable() is None, reason="ffmpeg not available")
def test_extracts_envelope_from_a_real_video_file(tmp_path):
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(AUDIO_SAMPLE_RATE)
        w.writeframes(_audio(0.0, 20, gain=1.0).tobytes())
    video = tmp_path / "cam.mp4"
    subprocess.run(
        [ffmpeg_executable(), "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=64x48:d=20",
         "-i", str(wav), "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(video)],
        check=True,
    )  # fmt: skip
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [ffmpeg_executable(), "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=64x48:d=3",
         "-c:v", "mpeg4", str(silent)],
        check=True,
    )  # fmt: skip

    envelope = extract_envelope(str(video))
    assert envelope is not None and abs(len(envelope) - 20 * ENVELOPE_RATE_HZ) < 10
    assert extract_envelope(str(silent)) is None
    assert extract_envelope(str(tmp_path / "missing.mp4")) is None
