"""media.fx_audio.build_bus: the premixed fxbus.wav (§9), on the F clock."""

import math
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from src.edit_fx import assets
from src.edit_fx.contracts import AccentCue, BleepCue, EditStyle, FreezeCue, FxPlan, MusicBed, SfxCue
from src.edit_fx.warp import build_warp, identity_warp
from src.media import fx_audio

SR = 48000
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _plan(*cues, intensity="bold", music=None, loop="none", sensitivity="normal") -> FxPlan:
    return FxPlan(style=EditStyle(intensity=intensity), template="default", seed=7, edit_duration=10.0,
                  cues=tuple(cues), music=music, loop_ending=loop, sensitivity=sensitivity)


def _write(path: Path, samples: np.ndarray, channels: int = 1) -> Path:
    pcm = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())
    return path


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == SR and handle.getnchannels() == 2 and handle.getsampwidth() == 2
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float64) / 32767
    return data.reshape(-1, 2)


def _click(tmp_path: Path, amplitude=0.25, length=0.2) -> Path:
    samples = np.zeros(int(length * SR))
    samples[0] = amplitude
    return _write(tmp_path / "click.wav", samples)


@pytest.fixture
def click_sfx(tmp_path, monkeypatch):
    """Every SFX resolves to a one-sample click (0.25) with 0 dB normalisation; records the calls."""
    path = _click(tmp_path)
    calls = []

    def fake(sound, variant, seed):
        calls.append((sound, variant, seed))
        return path, 0.0

    monkeypatch.setattr(assets, "mix_sfx_file", fake)
    return calls


def _onsets(bus: np.ndarray, threshold=0.01) -> list[int]:
    hits = np.flatnonzero(np.abs(bus[:, 0]) > threshold)
    return [int(i) for i in hits]


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


# ----------------------------------------------------------------------------- nothing to mix


def test_nothing_to_mix_returns_false_and_writes_nothing(tmp_path):
    out = tmp_path / "fxbus.wav"
    plan = _plan(AccentCue(id="a", start=1, end=2, variant="circle"), BleepCue(id="b", start=1, end=1.5, tone=False))
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(10), out_duration=10, path=out) is False
    assert not out.exists()
    assert fx_audio.build_bus(_plan(), words_e=[], warp=identity_warp(10), out_duration=0, path=out) is False


# ----------------------------------------------------------------------------- SFX


def test_sfx_lands_on_its_sample_at_the_cue_gain(tmp_path, click_sfx):
    out = tmp_path / "fxbus.wav"
    plan = _plan(SfxCue(id="s", start=1.0, end=1.0, sound="pop", variant=2, seed=11, gain_db=-6))
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(3), out_duration=3, path=out)
    bus = _read(out)
    assert bus.shape == (3 * SR, 2)
    assert _onsets(bus) == [SR]
    assert bus[SR, 0] == pytest.approx(0.25 * 10 ** (-6 / 20), abs=1e-4)
    assert bus[SR, 1] == bus[SR, 0]
    assert click_sfx == [("pop", 2, 11)]


REAL_BUS_GAIN = ("test_sfx_get_the_intensity_bus_gain", "test_serious_mode_uses_the_subtle_bus_gain")


@pytest.fixture(autouse=True)
def unit_bus_gain(request, monkeypatch):
    """The mixing tests below read amplitudes at 0 dB bus gain (bold used to be 0 dB); the level table itself is pinned
    by test_resolver.test_sfx_bus_gain_row_and_cue_gains_stay_relative and the two tests in REAL_BUS_GAIN."""
    if request.node.originalname in REAL_BUS_GAIN:
        return
    from src.edit_fx import resolver

    monkeypatch.setattr(resolver, "SFX_BUS_GAIN_DB", {"style": 0.0, "subtle": -4.0, "bold": 0.0, "max": 2.0})


@pytest.mark.parametrize("intensity,bus_db", [("subtle", -20.0), ("bold", -16.0), ("max", -10.0), ("off", -16.0)])
def test_sfx_get_the_intensity_bus_gain(tmp_path, click_sfx, intensity, bus_db):
    out = tmp_path / "fxbus.wav"
    plan = _plan(SfxCue(id="s", start=0.5, end=0.5, sound="pop"), intensity=intensity)
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(1), out_duration=1, path=out)
    assert _read(out)[SR // 2, 0] == pytest.approx(0.25 * 10 ** (bus_db / 20), abs=1e-4)


def test_serious_mode_uses_the_subtle_bus_gain(tmp_path, click_sfx):
    out = tmp_path / "fxbus.wav"
    plan = _plan(SfxCue(id="s", start=0.5, end=0.5, sound="whoosh"), intensity="max", sensitivity="serious")
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(1), out_duration=1, path=out)
    assert _read(out)[SR // 2, 0] == pytest.approx(0.25 * 10 ** (-20 / 20), abs=1e-4)


def test_normalisation_gain_of_a_drop_in_is_applied(tmp_path, monkeypatch):
    path = _click(tmp_path)
    monkeypatch.setattr(assets, "mix_sfx_file", lambda *_: (path, -12.0))
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(SfxCue(id="s", start=0.5, end=0.5, sound="pop")), words_e=[],
                              warp=identity_warp(1), out_duration=1, path=out)
    assert _read(out)[SR // 2, 0] == pytest.approx(0.25 * 10 ** (-12 / 20), abs=1e-4)


def test_sfx_times_go_through_the_warp(tmp_path, click_sfx):
    cues = [FreezeCue(id="f", start=1.0, end=1.0, hold=0.5), SfxCue(id="s", start=2.0, end=2.0, sound="pop"),
            SfxCue(id="r", start=1.0, end=1.0, sound="record_scratch")]
    warp = build_warp(cues, 4.0)
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(*cues), words_e=[], warp=warp, out_duration=warp.out_duration, path=out)
    # The scratch starts with the hold (to_output of the freeze instant), the pop half a second late.
    assert _onsets(_read(out)) == [SR, int(2.5 * SR)]


def test_overlapping_sfx_sum_and_edges_are_cut(tmp_path, click_sfx):
    cues = [SfxCue(id="a", start=0.5, end=0.5, sound="pop"), SfxCue(id="b", start=0.5, end=0.5, sound="slap"),
            SfxCue(id="c", start=0.99, end=0.99, sound="pop"), SfxCue(id="d", start=5.0, end=5.0, sound="pop")]
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(*cues), words_e=[], warp=identity_warp(1), out_duration=1, path=out)
    bus = _read(out)
    assert bus[SR // 2, 0] == pytest.approx(0.5, abs=1e-4)
    assert _onsets(bus) == [SR // 2, int(round(0.99 * SR))]


def test_a_missing_sound_is_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    def broken(sound, variant, seed):
        raise KeyError(sound)

    monkeypatch.setattr(assets, "mix_sfx_file", broken)
    out = tmp_path / "fxbus.wav"
    plan = _plan(SfxCue(id="s", start=0.5, end=0.5, sound="pop"))
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(1), out_duration=1, path=out) is False
    assert "fx_bus_sfx_skipped" in caplog.text


def test_builtin_sfx_decode_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("FX_LIBRARY_DIR", str(tmp_path / "empty-library"))
    monkeypatch.setattr(fx_audio.subprocess, "run", lambda *a, **k: pytest.fail("ffmpeg must not run"))
    out = tmp_path / "fxbus.wav"
    plan = _plan(SfxCue(id="s", start=0.1, end=0.1, sound="impact", variant=1))
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(2), out_duration=2, path=out)
    bus = _read(out)
    assert _db(np.max(np.abs(bus))) == pytest.approx(-6.0, abs=0.3)   # built-ins peak at -6 dBFS, bold bus 0 dB
    assert np.abs(bus[: int(0.1 * SR) - 1]).max() == 0


def test_ticker_is_trimmed_to_the_count_span_with_a_fade(tmp_path, monkeypatch):
    path = _write(tmp_path / "tick.wav", np.full(int(1.2 * SR), 0.2))
    monkeypatch.setattr(assets, "mix_sfx_file", lambda *_: (path, 0.0))
    counter = AccentCue(id="b3.acc", start=2.0, end=3.6, variant="counter", value=100)
    cues = [counter, SfxCue(id="b3.acc.sfx", start=2.0, end=2.0, sound="ticker", gain_db=0),
            SfxCue(id="b3.acc.sfx2", start=2.7, end=2.7, sound="stamp", gain_db=-30)]
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(*cues), words_e=[], warp=identity_warp(5), out_duration=5, path=out)
    bus = _read(out)[:, 0]
    lock = int(2.7 * SR)
    fade = int(0.03 * SR)
    assert bus[int(2.1 * SR)] == pytest.approx(0.2, abs=1e-3)
    assert bus[lock - fade - 10] == pytest.approx(0.2, abs=1e-3)
    assert 0 < bus[lock - fade // 2] < 0.15                       # inside the 30 ms fade
    assert np.abs(bus[lock + 2: int(3.2 * SR)]).max() < 0.01      # the untrimmed file would run to 3.2 s


def test_ticker_without_a_lock_sound_ends_at_the_counter_lock(tmp_path, monkeypatch):
    path = _write(tmp_path / "tick.wav", np.full(int(1.2 * SR), 0.2))
    monkeypatch.setattr(assets, "mix_sfx_file", lambda *_: (path, 0.0))
    cues = [AccentCue(id="b1.acc@1", start=1.0, end=2.5, variant="counter", value=5),
            SfxCue(id="b1.acc.sfx@1", start=1.0, end=1.0, sound="ticker")]
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(*cues), words_e=[], warp=identity_warp(3), out_duration=3, path=out)
    bus = _read(out)[:, 0]
    assert np.abs(bus[int(1.6 * SR) + 2:]).max() < 0.01           # lock = accent end - 0.9 s
    assert bus[int(1.5 * SR)] == pytest.approx(0.2, abs=1e-3)


# ----------------------------------------------------------------------------- bleep tone


def test_bleep_tone_is_a_1khz_sine_at_minus_12_dbfs_with_ramps(tmp_path):
    out = tmp_path / "fxbus.wav"
    cues = [BleepCue(id="w.bleep", start=1.0, end=1.5), BleepCue(id="m.bleep", start=2.0, end=2.4, tone=False)]
    assert fx_audio.build_bus(_plan(*cues), words_e=[], warp=identity_warp(3), out_duration=3, path=out)
    bus = _read(out)[:, 0]
    span = bus[SR: int(1.5 * SR)]
    assert _db(_rms(span[480:-480]) * math.sqrt(2)) == pytest.approx(-12.0, abs=0.1)
    spectrum = np.abs(np.fft.rfft(span))
    assert np.argmax(spectrum) * SR / span.size == pytest.approx(1000, abs=3)
    assert abs(span[0]) < 1e-3 and abs(span[-1]) < 0.01            # 5 ms ramps, no clicks
    assert np.abs(bus[: SR - 1]).max() == 0 and np.abs(bus[int(1.5 * SR) + 1:]).max() == 0   # mute-only: no tone


def test_bleep_tone_spans_map_to_final_time(tmp_path):
    cues = [FreezeCue(id="f", start=0.5, end=0.5, hold=1.0), BleepCue(id="b", start=1.0, end=1.2)]
    warp = build_warp(cues, 3.0)
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(*cues), words_e=[], warp=warp, out_duration=warp.out_duration, path=out)
    hits = _onsets(_read(out), threshold=1e-3)
    assert hits[0] / SR == pytest.approx(2.0, abs=0.002) and hits[-1] / SR == pytest.approx(2.2, abs=0.002)


# ----------------------------------------------------------------------------- music


def _bed_file(tmp_path: Path, seconds=2.0, hz=220.0) -> Path:
    t = np.arange(int(seconds * SR)) / SR
    return _write(tmp_path / "bed.wav", 0.5 * np.sin(2 * math.pi * hz * t))


def _music(**kw) -> MusicBed:
    base = dict(mood="hype", bed="hype-tent", gain_db=-20, duck_db=-12, attack=0.08, release=0.35, offset=0,
                fade_in=0.5, fade_out=0.8)
    base.update(kw)
    return MusicBed(**base)


def _window_db(bus: np.ndarray, a: float, b: float) -> float:
    return _db(_rms(bus[int(a * SR): int(b * SR), 0]))


def test_music_is_ducked_under_speech_with_exact_depth(tmp_path, monkeypatch):
    bed = _bed_file(tmp_path)
    monkeypatch.setattr(assets, "mix_bed_file", lambda *_: (bed, 0.0))
    out = tmp_path / "fxbus.wav"
    words = [{"id": "w0", "start": 3.0, "end": 3.4, "text": "a"}, {"id": "w1", "start": 3.5, "end": 5.0, "text": "b"}]
    assert fx_audio.build_bus(_plan(music=_music()), words_e=words, warp=identity_warp(8), out_duration=8, path=out)
    bus = _read(out)
    open_db = _window_db(bus, 1.0, 2.5)
    ducked_db = _window_db(bus, 3.5, 4.8)
    sine_rms_db = _db(0.5 / math.sqrt(2))
    assert open_db == pytest.approx(sine_rms_db - 20, abs=0.2)
    assert ducked_db - open_db == pytest.approx(-12, abs=0.3)
    assert _window_db(bus, 0.0, 0.05) < open_db - 20          # fade in
    assert _window_db(bus, 7.95, 8.0) < open_db - 20          # fade out


def test_music_ducks_under_warped_speech(tmp_path, monkeypatch):
    bed = _bed_file(tmp_path)
    monkeypatch.setattr(assets, "mix_bed_file", lambda *_: (bed, 0.0))
    cues = [FreezeCue(id="f", start=1.0, end=1.0, hold=2.0)]
    warp = build_warp(cues, 6.0)
    out = tmp_path / "fxbus.wav"
    words = [{"id": "w0", "start": 2.0, "end": 3.0, "text": "a"}]          # E 2..3 → F 4..5
    assert fx_audio.build_bus(_plan(*cues, music=_music()), words_e=words, warp=warp,
                              out_duration=warp.out_duration, path=out)
    bus = _read(out)
    assert _window_db(bus, 4.2, 4.9) - _window_db(bus, 1.5, 2.5) == pytest.approx(-12, abs=0.3)


def test_music_normalisation_gain_and_offset(tmp_path, monkeypatch):
    t = np.arange(2 * SR) / SR
    ramp = _write(tmp_path / "ramp.wav", (t / 2.0) * 0.5)                     # level encodes the position
    monkeypatch.setattr(assets, "mix_bed_file", lambda *_: (ramp, 6.0))
    out = tmp_path / "fxbus.wav"
    music = _music(gain_db=-6, offset=0.5, fade_in=0, fade_out=0)
    assert fx_audio.build_bus(_plan(music=music), words_e=[], warp=identity_warp(1), out_duration=1, path=out)
    bus = _read(out)[:, 0]
    assert bus[0] == pytest.approx(0.5 * 0.25, abs=2e-3)              # 0.5 s into the bed, -6 + 6 dB
    assert bus[int(0.5 * SR)] == pytest.approx(0.5 * 0.5, abs=2e-3)


def test_drop_in_bed_loops_with_an_equal_power_seam(tmp_path, monkeypatch):
    bed = _write(tmp_path / "dc.wav", np.full(SR, 0.4))                     # 1 s constant
    monkeypatch.setattr(assets, "mix_bed_file", lambda *_: (bed, 0.0))
    out = tmp_path / "fxbus.wav"
    music = _music(gain_db=0, fade_in=0, fade_out=0)
    assert fx_audio.build_bus(_plan(music=music), words_e=[], warp=identity_warp(3.5), out_duration=3.5, path=out)
    bus = _read(out)[:, 0]
    xf = int(0.05 * SR)
    seam = bus[SR - xf: SR]
    # cos + sin of a constant peaks at sqrt(2) in the middle of the seam (equal power) and never drops out.
    assert seam.min() >= 0.4 - 1e-3 and seam.max() == pytest.approx(0.4 * math.sqrt(2), abs=0.01)
    assert bus.min() > 0.39                                                 # no gap anywhere


@needs_ffmpeg
def test_builtin_bed_tiles_exactly_at_its_loop_length(tmp_path, monkeypatch):
    monkeypatch.setenv("FX_LIBRARY_DIR", str(tmp_path / "empty-library"))
    path, _ = assets.mix_bed_file("funny-ukulele", "funny", 0)
    index = assets.load_index()
    start, samples = fx_audio._builtin_loop(path, SR)
    bed_id = "funny-ukulele"
    assert (start, samples) == (round(index.bed_starts[bed_id] * SR), round(index.bed_lengths[bed_id] * SR))
    assert start == round(0.1 * SR)
    decoded = fx_audio._decode(path, SR)
    # the file holds the loop with 100 ms of its own audio on each side (and at most one MP3 frame of end padding)
    assert 0 <= decoded.shape[0] - (2 * start + samples) < 1152
    loop = decoded[start:start + samples]
    tail = loop.shape[0] - 1000
    looped = fx_audio._looped(loop, tail, 3000, seamless=True, sample_rate=SR)
    assert np.array_equal(looped[:1000], loop[-1000:]) and np.array_equal(looped[1000:], loop[:2000])


@needs_ffmpeg
def test_builtin_bed_mix_starts_at_its_loop_not_at_the_file_padding(tmp_path, monkeypatch):
    monkeypatch.setenv("FX_LIBRARY_DIR", str(tmp_path / "empty-library"))
    path, _ = assets.mix_bed_file("funny-ukulele", "funny", 0)
    start, samples = fx_audio._builtin_loop(path, SR)
    loop = fx_audio._decode(path, SR)[start:start + samples]
    out = tmp_path / "fxbus.wav"
    music = _music(mood="funny", bed="funny-ukulele", gain_db=0, duck_db=0, fade_in=0, fade_out=0, offset=1.0)
    assert fx_audio.build_bus(_plan(music=music), words_e=[], warp=identity_warp(2), out_duration=2, path=out)
    bus = _read(out)
    expected = loop[SR:SR + bus.shape[0]]                                  # offset 1 s into the loop, not the file
    assert np.max(np.abs(bus - expected)) < 2e-3


@needs_ffmpeg
def test_real_builtin_bed_mixes(tmp_path, monkeypatch):
    monkeypatch.setenv("FX_LIBRARY_DIR", str(tmp_path / "empty-library"))
    out = tmp_path / "fxbus.wav"
    plan = _plan(music=_music(mood="dramatic", bed="dramatic-war-room"))
    assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(4), out_duration=4, path=out)
    bus = _read(out)
    assert -45 < _window_db(bus, 1, 3) < -15


def test_drop_in_beds_are_not_treated_as_builtin(tmp_path):
    assert fx_audio._builtin_loop(_bed_file(tmp_path), SR) is None


def test_undecodable_bed_is_skipped(tmp_path, monkeypatch, caplog):
    junk = tmp_path / "junk.m4a"
    junk.write_bytes(b"not audio")
    monkeypatch.setattr(assets, "mix_bed_file", lambda *_: (junk, 0.0))
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(music=_music()), words_e=[], warp=identity_warp(2), out_duration=2,
                              path=out) is False
    assert "fx_bus_music_skipped" in caplog.text


# ----------------------------------------------------------------------------- seam and clipping


def test_loop_crossfade_blends_the_tail_into_the_head(tmp_path, click_sfx):
    cues = [SfxCue(id="h", start=0.1, end=0.1, sound="pop")]
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(*cues, loop="crossfade"), words_e=[], warp=identity_warp(2), out_duration=2,
                              path=out)
    bus = _read(out)[:, 0]
    x = int(0.4 * SR)
    # The head's click appears again inside the last 0.4 s, scaled by sin of its position in the seam.
    echo = 2 * SR - x + int(0.1 * SR)
    assert bus[int(0.1 * SR)] == pytest.approx(0.25, abs=1e-4)
    assert bus[echo] == pytest.approx(0.25 * math.sin(math.pi / 2 * (int(0.1 * SR) / (x - 1))), abs=2e-3)


def test_loop_seam_fades_the_tail_out(tmp_path, monkeypatch):
    bed = _write(tmp_path / "dc.wav", np.full(4 * SR, 0.3))
    monkeypatch.setattr(assets, "mix_bed_file", lambda *_: (bed, 0.0))
    out = tmp_path / "fxbus.wav"
    music = _music(gain_db=0, fade_in=0.0, fade_out=0.0)
    assert fx_audio.build_bus(_plan(music=music, loop="crossfade"), words_e=[], warp=identity_warp(3),
                              out_duration=3, path=out)
    bus = _read(out)[:, 0]
    assert bus[-1] == pytest.approx(0.3, abs=2e-3)          # the tail is now the (constant) head at full weight
    assert bus[int(2.8 * SR)] == pytest.approx(0.3 * (math.cos(math.pi / 4) + math.sin(math.pi / 4)), abs=0.01)


def test_soft_clip_bounds_overlaps(tmp_path, monkeypatch):
    loud = _write(tmp_path / "loud.wav", np.full(SR // 10, 0.45))
    monkeypatch.setattr(assets, "mix_sfx_file", lambda *_: (loud, 0.0))
    cues = [SfxCue(id=f"s{i}", start=0.5, end=0.5, sound="pop") for i in range(2)]
    out = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(_plan(*cues), words_e=[], warp=identity_warp(1), out_duration=1, path=out)
    bus = _read(out)
    expected = 0.8 + 0.2 * math.tanh((0.9 - 0.8) / 0.2)
    assert bus.max() == pytest.approx(expected, abs=1e-3) and bus.max() < 1.0
    small = np.array([[0.5, -0.79]], dtype=np.float32)
    fx_audio._soft_clip(small)
    assert small[0].tolist() == pytest.approx([0.5, -0.79])


# ----------------------------------------------------------------------------- drop-ins at mix time


@needs_ffmpeg
def test_a_drop_in_sfx_is_used_at_mix_time(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "sfx").mkdir(parents=True)
    t = np.arange(SR // 5) / SR
    _write(library / "sfx" / "pop-mine.wav", 0.1 * np.sin(2 * math.pi * 3000 * t))
    monkeypatch.setenv("FX_LIBRARY_DIR", str(library))
    assets.load_library(refresh=True)
    try:
        out = tmp_path / "fxbus.wav"
        plan = _plan(SfxCue(id="s", start=0.2, end=0.2, sound="pop", seed=3))
        assert fx_audio.build_bus(plan, words_e=[], warp=identity_warp(1), out_duration=1, path=out)
        sound = _read(out)[int(0.2 * SR): int(0.4 * SR), 0]
        spectrum = np.abs(np.fft.rfft(sound))
        assert np.argmax(spectrum) * SR / sound.size == pytest.approx(3000, abs=10)
        # Normalised to -6 dBFS peak (from -20 dBFS) at mix time.
        assert _db(np.abs(sound).max()) == pytest.approx(-6.0, abs=0.3)
    finally:
        monkeypatch.delenv("FX_LIBRARY_DIR")
        assets.load_library(refresh=True)
