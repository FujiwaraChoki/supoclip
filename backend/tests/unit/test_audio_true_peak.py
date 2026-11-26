"""The final encode keeps true peak under -1 dBTP, even on a scream after a quiet stretch.

One-pass ``loudnorm`` only aims at its true-peak target, and the AAC encoder rings on clipped, full-band audio:
a rendered clip measured +2.2 dBFS. The synthetic clip here is quiet talk with sudden, clipped bursts (one
right at the start, where loudnorm has nothing to go on), encoded exactly as the final render encodes it.
"""

import re
import subprocess
import wave

import numpy as np
import pytest

from src.media.common import FINAL_AUDIO_FILTER, LOUDNORM_FILTER, TRUE_PEAK_GUARD_FILTER
from src.media.ffmpeg import build_audio_output_args

SR = 48000


def _write_screams(path, seed=1, quiet=0.01):
    rng = np.random.default_rng(seed)
    t = np.arange(SR * 20) / SR
    spectrum = np.fft.rfft(rng.normal(0, 1, t.size))
    freqs = np.fft.rfftfreq(t.size, 1 / SR)
    spectrum[(freqs < 150) | (freqs > 5000)] = 0
    talk = np.fft.irfft(spectrum, t.size) * (0.4 + 0.6 * np.abs(np.sin(2 * np.pi * 2.3 * t)))
    signal = talk / np.max(np.abs(talk)) * quiet
    for start in (0.0, 8.0):
        a, b = int(start * SR), int((start + 0.25) * SR)
        burst = rng.normal(0, 1, b - a)
        signal[a:b] = np.clip(0.6 * np.sign(np.sin(2 * np.pi * 900 * t[a:b])) + 0.6 * burst / np.max(np.abs(burst)),
                              -0.999, 0.999)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SR)
        out.writeframes((np.clip(signal, -0.999, 0.999) * 32767).astype("<i2").tobytes())


def _true_peak_and_loudness(path):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, check=True)
    summary = result.stderr[result.stderr.rindex("Summary:"):]
    peak = float(re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf)", summary).group(1))
    loudness = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))
    return peak, loudness


def _encode(source, target, audio_args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), *audio_args, str(target)], check=True)


def test_the_final_audio_chain_is_loudnorm_then_the_guard():
    assert FINAL_AUDIO_FILTER == f"{LOUDNORM_FILTER},{TRUE_PEAK_GUARD_FILTER}"
    assert build_audio_output_args(True) == ["-af", FINAL_AUDIO_FILTER, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    assert build_audio_output_args(True, loudnorm=False) == ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    assert build_audio_output_args(False) == ["-an"]


@pytest.mark.parametrize("seed", [1, 2])
def test_a_scream_after_quiet_talk_stays_under_minus_one_dbtp_after_aac(tmp_path, seed):
    source = tmp_path / "screams.wav"
    _write_screams(source, seed=seed)
    guarded = tmp_path / "guarded.m4a"
    _encode(source, guarded, build_audio_output_args(True))
    peak, loudness = _true_peak_and_loudness(guarded)
    assert peak <= -1.0, f"true peak {peak} dBTP"
    # The guard must not cost loudness: the same integrated level as loudnorm alone, within half a LU.
    plain = tmp_path / "plain.m4a"
    _encode(source, plain, ["-af", LOUDNORM_FILTER, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"])
    _plain_peak, plain_loudness = _true_peak_and_loudness(plain)
    assert abs(loudness - plain_loudness) <= 0.5
