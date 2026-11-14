"""media.fx_audio.audio_graph: the P3 audio filter_complex (§9), as a string and through real ffmpeg."""

import math
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.edit_fx import assets
from src.edit_fx.contracts import EditStyle, FreezeCue, FxPlan, SfxCue, SpeedCue
from src.edit_fx.warp import Warp, WarpPiece, build_warp, identity_warp
from src.media import fx_audio

SR = 48000
VOICE = "[1:a:0]anull[fxa_voice0]"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _graph(**overrides) -> str:
    kwargs = dict(voice_fragment=VOICE, bleeps_e=[], warp=identity_warp(5.0), out_duration=5.0, has_bus=False,
                  voice_gain=1.0, loudness="none", static_gain_db=None, loop_crossfade=0.0)
    kwargs.update(overrides)
    return fx_audio.audio_graph(**kwargs)


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


def _sine(seconds: float, hz: float, amplitude: float) -> np.ndarray:
    t = np.arange(int(round(seconds * SR))) / SR
    return amplitude * np.sin(2 * math.pi * hz * t)


def _run(graph: str, voice: Path, bus: Path | None = None) -> np.ndarray:
    """P3's audio half: input 0 stands in for the rawvideo pipe, 1 is the voice, 2 the bus. f32le stereo out."""
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(voice), "-i", str(voice)]
    if bus is not None:
        command += ["-i", str(bus)]
    command += ["-filter_complex", graph, "-map", "[fxa_out]", "-f", "f32le", "-c:a", "pcm_f32le", "-ar", str(SR),
                "-ac", "2", "-"]
    proc = subprocess.run(command, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[-2000:]
    return np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, 2).astype(np.float64)


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def _rms_db(x: np.ndarray) -> float:
    return _db(float(np.sqrt(np.mean(np.square(x))))) if x.size else -240.0


def _window(audio: np.ndarray, a: float, b: float) -> np.ndarray:
    return audio[int(round(a * SR)): int(round(b * SR)), 0]


# ----------------------------------------------------------------------------- the string


def test_identity_graph_is_exactly_section_9():
    graph = _graph(bleeps_e=[(1.0, 1.5)], loudness="static", static_gain_db=3.5, has_bus=True)
    assert graph == (
        "[1:a:0]anull[fxa_voice0];"
        "[fxa_voice0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetnsamples=n=48:p=0,"
        "volume=0:enable='between(t,1,1.5)',"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur=5,atrim=0:5[fxa_voice];"
        "[2:a]aformat=sample_fmts=fltp:channel_layouts=stereo[fxa_bus];"
        "[fxa_voice]volume=3.5dB[fxa_v2];"
        "[fxa_v2][fxa_bus]amix=inputs=2:duration=first:normalize=0:dropout_transition=0,"
        "alimiter=limit=0.82:attack=5:release=50:level=disabled:latency=1[fxa_out]"
    )


def test_static_gain_scales_the_voice_only():
    """D8: a quiet source's static gain must not raise the SFX/music bus with it."""
    graph = _graph(loudness="static", static_gain_db=12.0, has_bus=True)
    assert "[fxa_voice]volume=12dB[fxa_v2]" in graph
    assert "[fxa_bus]volume" not in graph and "[fxa_v2][fxa_bus]amix" in graph


def test_every_added_label_is_prefixed():
    warp = build_warp([FreezeCue(id="f", start=1, end=1, hold=0.5), SpeedCue(id="s", start=2, end=3, rate=3.0)], 5)
    graph = _graph(warp=warp, out_duration=warp.out_duration, bleeps_e=[(0.2, 0.3)], has_bus=True,
                   loudness="static", static_gain_db=-2, loop_crossfade=0.4, voice_gain=0.5)
    labels = set(re.findall(r"\[([^\]]+)\]", graph))
    assert labels - {"1:a:0", "2:a"} and all(label.startswith("fxa_") for label in labels - {"1:a:0", "2:a"})
    assert "[fxa_out]" in graph and graph.count("[fxa_out]") == 1


def test_mutes_come_after_one_millisecond_frames_and_round_outwards():
    graph = _graph(bleeps_e=[(1.2345, 1.6001)])
    assert graph.index("asetnsamples=n=48:p=0") < graph.index("volume=0:enable")
    assert "between(t,1.234,1.601)" in graph


def test_more_than_forty_bleeps_chain_volume_filters():
    bleeps = [(i * 0.1, i * 0.1 + 0.05) for i in range(95)]
    graph = _graph(bleeps_e=bleeps)
    filters = re.findall(r"volume=0:enable='([^']*)'", graph)
    assert [f.count("between(") for f in filters] == [40, 40, 15]


def test_voice_gain_is_omitted_at_unity_and_kept_otherwise():
    assert "volume=0.5," not in _graph() and ",volume=1" not in _graph()
    assert "asetnsamples=n=48:p=0,volume=0.5,aformat" in _graph(voice_gain=0.5)
    assert "asetnsamples=n=48:p=0,volume=0,aformat" in _graph(voice_gain=0.0)


def test_loudness_modes():
    static = _graph(loudness="static", static_gain_db=None)
    assert "[fxa_voice]volume=0dB[fxa_v2]" in static and "loudnorm" not in static
    dynamic = _graph(loudness="dynamic", static_gain_db=6.0)
    assert dynamic.endswith("[fxa_voice]alimiter=limit=0.82:attack=5:release=50:level=disabled:latency=1,"
                            "loudnorm=I=-14:TP=-1.5:LRA=11[fxa_out]")
    assert "dB" not in dynamic
    none = _graph(loudness="none", has_bus=True)
    assert "dB" not in none and "loudnorm" not in none
    assert none.endswith("[fxa_voice][fxa_bus]amix=inputs=2:duration=first:normalize=0:dropout_transition=0,"
                         "alimiter=limit=0.82:attack=5:release=50:level=disabled:latency=1[fxa_out]")
    with pytest.raises(ValueError):
        _graph(loudness="loud")


def test_warp_pieces():
    warp = build_warp([FreezeCue(id="f", start=1, end=1, hold=0.5), SpeedCue(id="s", start=2, end=3, rate=3.0)], 5)
    graph = _graph(warp=warp, out_duration=warp.out_duration)
    assert "asplit=4[fxa_ws0][fxa_ws1][fxa_ws2][fxa_ws3]" in graph
    assert "anullsrc=r=48000:cl=stereo,atrim=end_sample=24000," in graph
    assert ("[fxa_ws2]atrim=2:3,asetpts=N/SR/TB,atempo=1.732051,atempo=1.732051,"
            "apad=whole_len=16000,atrim=end_sample=16000[fxa_wp3]") in graph
    assert "[fxa_ws0]atrim=0:1,asetpts=N/SR/TB,apad=whole_len=48000,atrim=end_sample=48000[fxa_wp0]" in graph
    assert "concat=n=5:v=0:a=1,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur=4.833333" in graph
    assert "tpad" not in graph


@pytest.mark.parametrize("rate,expected", [(1.2, ["atempo=1.2"]), (0.5, ["atempo=0.5"]), (2.0, ["atempo=2"]),
                                           (4.0, ["atempo=2", "atempo=2"]), (0.25, ["atempo=0.5", "atempo=0.5"]),
                                           (5.0, ["atempo=1.709976"] * 3), (1.0, [])])
def test_atempo_chains_outside_its_range(rate, expected):
    assert fx_audio._atempo_chain(rate) == expected


def test_quantised_rates_past_the_cue_range_stay_inside_atempo():
    # 5 frames at 4x fit one frame: the piece's actual rate is 5, past two atempo=2 stages.
    warp = build_warp([SpeedCue(id="s", start=1.0, end=1.0 + 5 / 30, rate=4.0)], 3.0)
    assert any(abs(p.rate - 5.0) < 1e-9 for p in warp.pieces)
    for value in re.findall(r"atempo=([0-9.]+)", _graph(warp=warp, out_duration=warp.out_duration)):
        assert 0.5 <= float(value) <= 2.0


def test_a_warp_of_holds_only_still_consumes_the_voice():
    warp = Warp((WarpPiece(0.0, 0.0, 0.0, 0.0, 0.5),), 0.0, 0.5)
    graph = _graph(warp=warp, out_duration=0.5)
    assert "[fxa_vb]anullsink" in graph and "asplit" not in graph


def test_loop_crossfade_section():
    graph = _graph(loop_crossfade=0.4)
    assert "atrim=0:5[fxa_vpre];[fxa_vpre]asplit[fxa_vm][fxa_vh]" in graph
    assert "[fxa_vh]atrim=0:0.4,asetpts=N/SR/TB,afade=t=in:d=0.4,adelay=220800S:all=1[fxa_vhd]" in graph
    assert "[fxa_vm]afade=t=out:st=4.6:d=0.4[fxa_vmt]" in graph
    assert "[fxa_vmt][fxa_vhd]amix=inputs=2:duration=first:normalize=0[fxa_voice]" in graph
    assert "afade" not in _graph(loop_crossfade=0.0)
    assert "d=0.25" in _graph(loop_crossfade=0.4, out_duration=0.5)   # never more than half the clip


def test_output_decision_and_silent_voice():
    assert fx_audio.audio_output(True, False) == "source"
    assert fx_audio.audio_output(True, True) == "source"
    assert fx_audio.audio_output(False, True) == "silent"
    assert fx_audio.audio_output(False, False) == "none"          # -an, as the legacy render (AR-m6)
    assert fx_audio.silent_voice_fragment(12.5) == "anullsrc=r=48000:cl=stereo,atrim=0:12.5[fxa_voice0]"


def test_measurement_fragment():
    assert fx_audio.measurement_fragment(VOICE, []) == VOICE
    fragment = fx_audio.measurement_fragment(VOICE, [(1.0, 2.0)])
    assert fragment.startswith("[1:a:0]anull[fxa_vmeas];[fxa_vmeas]aresample=48000")
    assert fragment.endswith("volume=0:enable='between(t,1,2)'[fxa_voice0]")
    with pytest.raises(ValueError):
        fx_audio.measurement_fragment("[1:a:0]anull[x]", [(1.0, 2.0)])


def test_bleep_spans_come_from_bleep_cues():
    from src.edit_fx.contracts import BleepCue

    plan = FxPlan(style=EditStyle(intensity="bold"), template="default", seed=1, edit_duration=5,
                  cues=(BleepCue(id="b", start=2, end=2.5), BleepCue(id="a", start=1, end=1.2, tone=False),
                        SfxCue(id="s", start=1, end=1, sound="pop")))
    assert fx_audio.bleep_spans_e(plan) == [(1, 1.2), (2, 2.5)]


@pytest.mark.parametrize("value,expected", [(None, 0.0), (-30.0, 16.0), (-50.0, 20.0), (10.0, -20.0),
                                            (-14.0, 0.0), (float("nan"), 0.0)])
def test_static_gain_is_clamped(value, expected):
    assert fx_audio.static_gain_db(value) == pytest.approx(expected)


# ----------------------------------------------------------------------------- through ffmpeg


@needs_ffmpeg
def test_identity_output_has_the_exact_length_and_pads_short_voice(tmp_path):
    voice = _write(tmp_path / "v.wav", _sine(4.9, 440, 0.3))           # P1 two frames short of the plan (AR-M13)
    out = _run(_graph(out_duration=5.0), voice)
    assert out.shape[0] == 5 * SR
    assert _rms_db(_window(out, 4.92, 5.0)) < -90


@needs_ffmpeg
def test_mono_source_is_upmixed(tmp_path):
    voice = _write(tmp_path / "v.wav", _sine(2, 440, 0.3))
    out = _run(_graph(out_duration=2.0), voice)
    assert np.allclose(out[:, 0], out[:, 1]) and _rms_db(out[:, 0]) > -20


@needs_ffmpeg
def test_warped_voice_has_the_warped_length_and_silent_holds(tmp_path):
    cues = [FreezeCue(id="f", start=1.0, end=1.0, hold=0.5), SpeedCue(id="s", start=2.0, end=3.0, rate=2.0)]
    warp = build_warp(cues, 5.0)
    voice = _write(tmp_path / "v.wav", _sine(5, 300, 0.3))
    out = _run(_graph(warp=warp, out_duration=warp.out_duration), voice)
    assert out.shape[0] == int(round(warp.out_duration * SR))
    assert abs(out.shape[0] / SR - 5.0) == pytest.approx(0.0, abs=1e-3)   # +0.5 hold, -0.5 at double speed
    assert _rms_db(_window(out, 1.01, 1.49)) < -90                         # the hold is silence
    # (a mono source is upmixed at -3 dB per channel: the same loudness as the mono original)
    assert _rms_db(_window(out, 0.2, 0.9)) > -20 and _rms_db(_window(out, 1.6, 2.4)) > -20
    sped = _window(out, 2.55, 2.95)
    spectrum = np.abs(np.fft.rfft(sped))
    assert np.argmax(spectrum) * SR / sped.size == pytest.approx(300, abs=10)   # atempo keeps the pitch


@needs_ffmpeg
@pytest.mark.parametrize("rate,span", [(0.25, (1.0, 2.0)), (0.6, (0.5, 2.3)), (1.75, (0.3, 2.4))])
def test_voice_after_a_speed_piece_stays_on_the_picture(tmp_path, rate, span):
    # atempo's own output length is off by up to tens of ms (55 ms for 1 s at 0.25x); each piece is cut to its
    # exact F length, so a click after the ramp lands on its warped sample.
    warp = build_warp([SpeedCue(id="s", start=span[0], end=span[1], rate=rate)], 4.0)
    voice = np.zeros(4 * SR)
    voice[3 * SR] = 0.5
    out = _run(_graph(warp=warp, out_duration=warp.out_duration), _write(tmp_path / "v.wav", voice))
    assert out.shape[0] == int(round(warp.out_duration * SR))
    assert abs(int(np.argmax(np.abs(out[:, 0]))) / SR - warp.to_output(3.0)) <= 0.001


@needs_ffmpeg
def test_bleep_mute_starts_at_the_requested_sample(tmp_path):
    voice = _write(tmp_path / "v.wav", _sine(3, 440, 0.5))
    start, end = 1.2345, 1.6
    out = _run(_graph(bleeps_e=[(start, end)], out_duration=3.0), voice)
    assert _rms_db(_window(out, start + 0.001, end - 0.001)) < -50
    assert _rms_db(_window(out, start - 0.004, start - 0.001)) > -15
    assert _rms_db(_window(out, end + 0.002, end + 0.01)) > -15
    silent = np.abs(out[:, 0]) < 1e-6
    first = next(i for i in range(SR, 2 * SR) if silent[i:i + 96].all())    # first 2 ms run of silence
    assert abs(first / SR - start) <= 0.001


@needs_ffmpeg
@pytest.mark.parametrize("warped", [False, True])
def test_sfx_click_comes_out_on_its_millisecond(tmp_path, monkeypatch, warped):
    click = np.zeros(SR // 10)
    click[0] = 0.25
    click_path = _write(tmp_path / "click.wav", click)
    monkeypatch.setattr(assets, "mix_sfx_file", lambda *_: (click_path, 0.0))
    cues = [SfxCue(id="s", start=1.0, end=1.0, sound="pop")]
    if warped:
        cues.append(FreezeCue(id="f", start=0.4, end=0.4, hold=0.5))
    warp = build_warp(cues, 3.0)
    plan = FxPlan(style=EditStyle(intensity="bold"), template="default", seed=1, edit_duration=3.0,
                  cues=tuple(cues))
    bus = tmp_path / "fxbus.wav"
    assert fx_audio.build_bus(plan, words_e=[], warp=warp, out_duration=warp.out_duration, path=bus)
    voice = _write(tmp_path / "v.wav", _sine(3, 200, 0.01))
    graph = _graph(warp=warp, out_duration=warp.out_duration, has_bus=True, loudness="static",
                   static_gain_db=0.0)
    out = _run(graph, voice, bus)
    expected = 1.5 if warped else 1.0
    assert abs(int(np.argmax(np.abs(out[:, 0]))) / SR - expected) <= 0.001


@needs_ffmpeg
def test_studio_none_mode_tracks_the_volume_slider(tmp_path):
    voice = _write(tmp_path / "v.wav", _sine(3, 440, 0.3))
    bus = _write(tmp_path / "bus.wav", np.stack([_sine(3, 1500, 0.05)] * 2, axis=1))
    full = _run(_graph(out_duration=3.0, has_bus=True), voice, bus)
    half = _run(_graph(out_duration=3.0, has_bus=True, voice_gain=0.5), voice, bus)
    muted = _run(_graph(out_duration=3.0, has_bus=True, voice_gain=0.0), voice, bus)

    def band_db(audio, hz):
        segment = audio[SR // 2: SR // 2 + SR, 0]
        spectrum = np.abs(np.fft.rfft(segment)) / (segment.size / 2)
        return _db(spectrum[int(round(hz * segment.size / SR))])

    assert band_db(half, 440) - band_db(full, 440) == pytest.approx(-6.02, abs=0.5)
    assert band_db(muted, 440) < -90                                       # no voice
    assert band_db(muted, 1500) == pytest.approx(band_db(full, 1500), abs=0.1)   # and no loudness lift
    assert band_db(muted, 1500) == pytest.approx(_db(0.05), abs=0.1)


@needs_ffmpeg
def test_loop_crossfade_brings_the_head_into_the_tail(tmp_path):
    head = np.concatenate([_sine(0.3, 700, 0.4), np.zeros(int(2.7 * SR))])
    voice = _write(tmp_path / "v.wav", head)
    out = _run(_graph(out_duration=3.0, loop_crossfade=0.4), voice)
    assert out.shape[0] == 3 * SR
    assert _rms_db(_window(out, 2.0, 2.55)) < -90
    tail = _window(out, 2.75, 2.9)                                          # the head, fading in
    assert _rms_db(tail) > -20
    assert np.argmax(np.abs(np.fft.rfft(tail))) * SR / tail.size == pytest.approx(700, abs=10)


@needs_ffmpeg
def test_silent_source_with_a_bus(tmp_path):
    bus = _write(tmp_path / "bus.wav", np.stack([_sine(2.5, 1000, 0.1)] * 2, axis=1))
    stand_in = _write(tmp_path / "v.wav", np.zeros(SR))
    graph = _graph(voice_fragment=fx_audio.silent_voice_fragment(2.5), out_duration=2.5, has_bus=True,
                   loudness="static", static_gain_db=0.0)
    out = _run(graph, stand_in, bus)
    assert out.shape[0] == int(2.5 * SR)
    assert _rms_db(out[:, 0]) == pytest.approx(_db(0.1 / math.sqrt(2)), abs=0.2)


@needs_ffmpeg
def test_dynamic_mode_renders(tmp_path):
    voice = _write(tmp_path / "v.wav", _sine(4, 440, 0.05))
    out = _run(_graph(out_duration=4.0, loudness="dynamic"), voice)
    assert abs(out.shape[0] - 4 * SR) <= 2
    assert _rms_db(out[SR: 3 * SR, 0]) > _db(0.05 / math.sqrt(2)) + 3          # loudnorm lifted the quiet voice
