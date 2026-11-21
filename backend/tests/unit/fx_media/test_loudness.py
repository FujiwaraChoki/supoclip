"""Loudness of the FX mix (§16, AR-M4): static voice-referenced gain, music under speech, bleeps, studio volume.

Runs the real P3 audio graph through ffmpeg on synthetic inputs (no deno, no video).
"""

import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.edit_fx.contracts import BleepCue, EditStyle, FxPlan, MusicBed, SfxCue
from src.edit_fx.warp import identity_warp
from src.media import fx_audio

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

SR = 48000
VOICE = "[1:a:0]anull[fxa_voice0]"
DURATION = 12.0


def _write(path: Path, samples: np.ndarray) -> Path:
    data = np.asarray(samples, dtype=np.float64)
    channels = 1 if data.ndim == 1 else data.shape[1]
    pcm = np.clip(np.round(data * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())
    return path


def _speech(seconds: float, peak: float, *, talk=((0.5, 4.0), (4.6, 8.5), (9.0, 11.5))) -> np.ndarray:
    """Speech-like: a 140 Hz harmonic voice with a 4 Hz syllable envelope, only inside ``talk`` spans."""
    t = np.arange(int(round(seconds * SR))) / SR
    voice = sum(np.sin(2 * math.pi * 140 * k * t + k) / k for k in range(1, 16))
    syllables = np.clip(np.sin(2 * math.pi * 4 * t), 0, None) ** 0.5
    gate = np.zeros_like(t)
    for a, b in talk:
        gate[(t >= a) & (t < b)] = 1.0
    signal = voice * syllables * gate
    return peak * signal / np.abs(signal).max()


def _words(talk=((0.5, 4.0), (4.6, 8.5), (9.0, 11.5))) -> list[dict]:
    words, index = [], 0
    for a, b in talk:
        t = a
        while t + 0.25 <= b:
            words.append({"id": f"w{index}", "start": t, "end": t + 0.25, "text": "la"})
            index, t = index + 1, t + 0.3
    return words


def _render(graph: str, voice: Path, bus: Path | None, out: Path) -> Path:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(voice), "-i", str(voice)]
    if bus is not None:
        command += ["-i", str(bus)]
    command += ["-filter_complex", graph, "-map", "[fxa_out]", "-c:a", "pcm_f32le", "-ar", str(SR), str(out)]
    proc = subprocess.run(command, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[-2000:]
    return out


def _samples(path: Path) -> np.ndarray:
    proc = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "2",
                           "-ar", str(SR), "-"], capture_output=True, timeout=60, check=True)
    return np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, 2).astype(np.float64)


def _lufs(path: Path) -> float:
    value = fx_audio.measure_voice_lufs(path, VOICE, duration=DURATION)
    assert value is not None
    return value


def _graph(**overrides) -> str:
    kwargs = dict(voice_fragment=VOICE, bleeps_e=[], warp=identity_warp(DURATION), out_duration=DURATION,
                  has_bus=False, voice_gain=1.0, loudness="static", static_gain_db=None, loop_crossfade=0.0)
    kwargs.update(overrides)
    return fx_audio.audio_graph(**kwargs)


def _plan(*cues, music=None) -> FxPlan:
    return FxPlan(style=EditStyle(intensity="bold"), template="default", seed=3, edit_duration=DURATION,
                  cues=tuple(cues), music=music)


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def _rms_db(x: np.ndarray) -> float:
    return _db(float(np.sqrt(np.mean(np.square(x))))) if x.size else -240.0


def _tone_db(x: np.ndarray, hz: float) -> float:
    """Amplitude (dBFS) of the ``hz`` component of ``x`` (Hann-windowed DFT bin)."""
    window = np.hanning(x.size)
    spectrum = np.abs(np.fft.rfft(x * window)) / (window.sum() / 2)
    bin_ = int(round(hz * x.size / SR))
    return _db(float(spectrum[max(0, bin_ - 2): bin_ + 3].max()))


# ----------------------------------------------------------------------------- measurement


def test_measure_voice_lufs_reads_ebur128_and_ignores_silence(tmp_path):
    loud = _write(tmp_path / "loud.wav", _speech(DURATION, 0.5))
    quiet = _write(tmp_path / "quiet.wav", _speech(DURATION, 0.05))
    silent = _write(tmp_path / "silent.wav", np.zeros(int(DURATION * SR)))
    loud_lufs, quiet_lufs = _lufs(loud), _lufs(quiet)
    assert -40 < loud_lufs < -5
    assert loud_lufs - quiet_lufs == pytest.approx(20.0, abs=0.3)
    assert fx_audio.measure_voice_lufs(silent, VOICE, duration=DURATION) is None
    assert fx_audio.measure_voice_lufs(loud, VOICE, duration=0) is None
    assert fx_audio.measure_voice_lufs(tmp_path / "missing.wav", VOICE, duration=DURATION) is None


def test_measurement_after_bleeps_excludes_the_muted_word(tmp_path):
    voice = _write(tmp_path / "v.wav", _speech(DURATION, 0.5, talk=((0.5, 2.0), (3.0, 11.5))))
    quiet_part = _speech(DURATION, 0.5, talk=((0.5, 2.0),))
    louder = _speech(DURATION, 0.9, talk=((3.0, 11.5),))
    mixed = _write(tmp_path / "m.wav", quiet_part * 0.2 + louder)
    plain = _lufs(mixed)
    muted = fx_audio.measure_voice_lufs(mixed, fx_audio.measurement_fragment(VOICE, [(3.0, 11.5)]),
                                        duration=DURATION)
    assert muted is not None and muted < plain - 10
    assert _lufs(voice) is not None


# ----------------------------------------------------------------------------- static mode (generation)


@pytest.mark.parametrize("peak", [0.12, 0.3, 0.95])     # ≈ -30.5, -22.5 and -12.5 LUFS sources
def test_voice_only_static_render_is_minus_14(tmp_path, peak):
    voice = _write(tmp_path / "v.wav", _speech(DURATION, peak))
    lufs = fx_audio.measure_voice_lufs(voice, VOICE, duration=DURATION)
    out = _render(_graph(static_gain_db=fx_audio.static_gain_db(lufs)), voice, None, tmp_path / "out.wav")
    assert _lufs(out) == pytest.approx(-14.0, abs=1.0)


def test_static_gain_is_clamped_at_plus_20_db(tmp_path):
    voice = _write(tmp_path / "v.wav", _speech(DURATION, 0.06))                  # ≈ -36.5 LUFS
    lufs = fx_audio.measure_voice_lufs(voice, VOICE, duration=DURATION)
    assert fx_audio.static_gain_db(lufs) == 20.0
    out = _render(_graph(static_gain_db=20.0), voice, None, tmp_path / "out.wav")
    assert _lufs(out) == pytest.approx(lufs + 20.0, abs=0.3)


@pytest.mark.parametrize("peak", [0.6, 0.95])           # ≈ -16.5 and -12.5 LUFS sources (typical speech)
def test_full_mix_static_render_is_minus_14(tmp_path, peak):
    voice = _write(tmp_path / "v.wav", _speech(DURATION, peak))
    cues = [SfxCue(id=f"s{i}", start=1.0 + i * 2.0, end=1.0 + i * 2.0, sound=sound)
            for i, sound in enumerate(("whoosh", "impact", "pop", "boom", "stamp"))]
    plan = _plan(*cues, music=MusicBed(mood="hype", bed="hype-tent", gain_db=-20, duck_db=-12))
    bus = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(plan, words_e=_words(), warp=identity_warp(DURATION), out_duration=DURATION, path=bus)
    gain = fx_audio.static_gain_db(fx_audio.measure_voice_lufs(voice, VOICE, duration=DURATION))
    out = _render(_graph(has_bus=True, static_gain_db=gain), voice, bus, tmp_path / "mix.wav")
    assert _lufs(out) == pytest.approx(-14.0, abs=1.5)
    assert np.abs(_samples(out)).max() <= 0.891                                  # alimiter ceiling


def test_music_under_speech_is_at_least_10_db_down_on_the_mix(tmp_path):
    """Static gain: the music level is measured directly with a music-only render (same gain). A real bed's level moves
    with the music, so the ducked render is compared with the same render undiminished, window for window."""
    talk = ((3.0, 7.0),)
    voice = _write(tmp_path / "v.wav", _speech(DURATION, 0.1, talk=talk))
    gain = fx_audio.static_gain_db(fx_audio.measure_voice_lufs(voice, VOICE, duration=DURATION))
    assert gain != 0
    music = {}
    for duck in (-12.0, 0.0):
        plan = _plan(music=MusicBed(mood="chill", bed="chill-something-in-the-air", gain_db=-20, duck_db=duck))
        bus = tmp_path / f"fxbus{duck}.wav"
        assert fx_audio.build_bus(plan, words_e=_words(talk), warp=identity_warp(DURATION), out_duration=DURATION,
                                  path=bus)
        music_only = _render(
            _graph(voice_fragment=fx_audio.silent_voice_fragment(DURATION), has_bus=True, static_gain_db=gain),
            voice, bus, tmp_path / f"music{duck}.wav",
        )
        music[duck] = _samples(music_only)[:, 0]
    during = slice(int(3.5 * SR), int(6.5 * SR))
    open_ = np.r_[int(1.0 * SR): int(2.5 * SR), int(8.0 * SR): int(11.0 * SR)]
    down = _rms_db(music[0.0][during]) - _rms_db(music[-12.0][during])
    assert down >= 10.0
    assert down == pytest.approx(12.0, abs=1.5)
    assert _rms_db(music[0.0][open_]) - _rms_db(music[-12.0][open_]) == pytest.approx(0.0, abs=0.5)


def test_bleep_silences_the_voice_and_carries_the_tone(tmp_path):
    t = np.arange(int(DURATION * SR)) / SR
    voice = _write(tmp_path / "v.wav", 0.3 * np.sin(2 * math.pi * 440 * t))
    start, end = 4.2003, 4.9
    plan = _plan(BleepCue(id="b5.bleep", start=start, end=end))
    bus = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(DURATION), out_duration=DURATION, path=bus)
    gain = fx_audio.static_gain_db(fx_audio.measure_voice_lufs(
        voice, fx_audio.measurement_fragment(VOICE, fx_audio.bleep_spans_e(plan)), duration=DURATION))
    graph = _graph(has_bus=True, static_gain_db=gain, bleeps_e=fx_audio.bleep_spans_e(plan))
    mix = _samples(_render(graph, voice, bus, tmp_path / "mix.wav"))[:, 0]
    span = mix[int(round((start + 0.001) * SR)): int(round((end - 0.001) * SR))]
    assert _tone_db(span, 440) < -50                                     # voice gone from the requested start
    assert _tone_db(span, 1000) > -20                                    # the 1 kHz bleep is there
    before = mix[int((start - 0.5) * SR): int((start - 0.01) * SR)]
    assert _tone_db(before, 440) > -20 and _tone_db(before, 1000) < -60

    voice_only = _samples(_render(_graph(static_gain_db=gain, bleeps_e=[(start, end)]), voice, None,
                                  tmp_path / "voice.wav"))[:, 0]
    assert _rms_db(voice_only[int(round((start + 0.001) * SR)): int(round((end - 0.001) * SR))]) < -50
    assert _rms_db(voice_only[int(round((start - 0.004) * SR)): int(round((start - 0.001) * SR))]) > -30


# ----------------------------------------------------------------------------- studio ("none")


def test_studio_volume_and_mute_have_no_loudness_stage(tmp_path):
    voice = _write(tmp_path / "v.wav", _speech(DURATION, 0.3))
    bus = _write(tmp_path / "bus.wav", np.zeros((int(DURATION * SR), 2)))
    full = _render(_graph(loudness="none", has_bus=True), voice, bus, tmp_path / "full.wav")
    half = _render(_graph(loudness="none", has_bus=True, voice_gain=0.5), voice, bus, tmp_path / "half.wav")
    muted = _render(_graph(loudness="none", has_bus=True, voice_gain=0.0), voice, bus, tmp_path / "muted.wav")
    assert _lufs(half) - _lufs(full) == pytest.approx(-6.0, abs=0.5)
    assert _lufs(full) == pytest.approx(_lufs(voice), abs=0.3)                   # no gain applied
    assert np.abs(_samples(muted)).max() == 0.0                                  # no voice, nothing lifted
