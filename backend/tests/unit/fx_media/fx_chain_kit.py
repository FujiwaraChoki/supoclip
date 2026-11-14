"""Shared helpers for the WP12a chain tests (real ffmpeg + deno; skipped when either is missing).

The audio (WP12b) and analysis (WP12c) stages are stubbed with small, faithful stand-ins so these tests pin the
chain's own behaviour (process wiring, frame counts, colour, matte, failure handling). Set ``FX_CHAIN_REAL_DEPS=1``
to run them against the real ``fx_audio`` / ``fx_analysis`` instead (the coordinator does at the integration gate).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.edit_fx import env as fx_env
from src.edit_fx.contracts import EditStyle, FxPlan, MatteWindow
from src.media import fx_chain

FPS = 30


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


requires_ffmpeg = pytest.mark.skipif(not (_have("ffmpeg") and _have("ffprobe")), reason="needs ffmpeg and ffprobe")
requires_media = pytest.mark.skipif(
    not (_have("ffmpeg") and _have("ffprobe") and _have(fx_env.fx_deno_bin())),
    reason="needs ffmpeg, ffprobe and deno",
)


# ----------------------------------------------------------------------------- sources

def make_source(path: Path, *, w: int = 360, h: int = 640, dur: float = 2.0, video: str = "testsrc2",
                audio: str | None = "sine", color_tagged: bool = True) -> Path:
    """An H.264 yuv420p BT.709-tagged clip. ``video``: "testsrc2" or a colour like "0x3C78B4". ``audio``: "sine"
    (440 Hz speech stand-in), "silence", or None for no audio stream."""
    if video.startswith("0x") or video.startswith("#"):
        vsrc = f"color=c={video}:s={w}x{h}:r={FPS}:d={dur}"
    else:
        vsrc = f"{video}=s={w}x{h}:r={FPS}:d={dur}"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", vsrc]
    if audio == "sine":
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:r=48000:d={dur}"]
    elif audio == "silence":
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={dur}"]
    # Convert with an explicit BT.709 matrix so the stored YUV is what the tags say.
    vf = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p"]
    if color_tagged:
        cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def head_for(w: int, h: int) -> tuple[str, str]:
    """A minimal framing head (the generation head shape: scale to the output, square pixels)."""
    return f"[0:v]scale={w}:{h}:flags=lanczos,setsar=1[base]", "[base]"


def plan(*, duration: float, cues=(), template: str = "marker_circle", intensity: str = "bold", **extra) -> FxPlan:
    return FxPlan(style=EditStyle(intensity=intensity), template=template, seed=7, edit_duration=duration,
                  cues=tuple(cues), **extra)


def cue(kind: str, start: float, end: float | None = None, **fields) -> dict:
    """A cue dict (validated by FxPlan): instants get end == start."""
    data = {"kind": kind, "id": fields.pop("id", f"{kind}{int(start * 1000)}"), "start": start,
            "end": start if end is None else end, "seed": fields.pop("seed", 99)}
    data.update(fields)
    return data


def run_chain(source: Path, out: Path, request: fx_chain.FxRequest, *, w: int = 360, h: int = 640,
              mode: str = "full", **kwargs) -> fx_chain.FxResult:
    head, label = head_for(w, h)
    params = dict(
        input_path=source, output_path=out, head=head, head_label=label, out_w=w, out_h=h,
        broll_overlays=[], broll_inputs=[], subtitle_ass_path=None, fonts_dir=None, request=request,
        mode=mode, scratch_dir=out.parent / "scratch", has_audio=True,
        encode_args=["-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", "30"],
    )
    params.update(kwargs)
    return fx_chain.run_fx_chain(**params)


# ----------------------------------------------------------------------------- probes

def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,nb_read_packets,duration,width,height,color_space,color_primaries,color_transfer,"
         "color_range,pix_fmt,sample_rate,channels", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    streams = json.loads(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)
    return {"video": video, "audio": audio, "frames": int(video["nb_read_packets"])}


def stream_durations(path: Path) -> tuple[float, float | None]:
    """(video seconds from the frame count, audio seconds from decoded samples)."""
    info = probe(path)
    video = info["frames"] / FPS
    if info["audio"] is None:
        return video, None
    samples = decode_audio(path)
    return video, len(samples) / 48000.0


def frames_rgb(path: Path, w: int, h: int) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)


def frames_yuv_means(path: Path) -> np.ndarray:
    """Per-frame mean Y, U, V (8-bit code values) of the decoded yuv420p stream, no conversion."""
    info = probe(path)["video"]
    w, h = int(info["width"]), int(info["height"])
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        check=True, capture_output=True,
    ).stdout
    size = w * h * 3 // 2
    data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, size)
    y = data[:, : w * h].reshape(-1, h, w)
    u = data[:, w * h: w * h + w * h // 4]
    v = data[:, w * h + w * h // 4:]
    # Centre crop for Y to stay clear of edge effects.
    return np.stack([y[:, h // 4: 3 * h // 4, w // 4: 3 * w // 4].reshape(len(y), -1).mean(1), u.mean(1), v.mean(1)], 1)


def decode_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4")


def rms_db(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return -math.inf
    value = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
    return 20 * math.log10(value) if value > 0 else -math.inf


# ----------------------------------------------------------------------------- stubbed WP12b / WP12c stages

def _stub_build_bus(plan: FxPlan, *, words_e, warp, out_duration: float, path: Path, sample_rate: int = 48000) -> bool:
    """A 1 kHz, 150 ms burst at each SFX cue's F time and over each tone bleep: enough to prove the bus is mixed."""
    sfx = [c for c in plan.cues if c.kind == "sfx"]
    tones = [c for c in plan.cues if c.kind == "bleep" and c.tone]
    if not (sfx or tones or plan.music):
        return False
    n = max(1, int(round(out_duration * sample_rate)))
    bus = np.zeros(n, dtype=np.float64)
    spans = [(warp.to_output(c.start), 0.15) for c in sfx]
    spans += [(warp.to_output(c.start), max(0.0, warp.to_output(c.end) - warp.to_output(c.start))) for c in tones]
    for start, length in spans:
        a = int(round(start * sample_rate))
        b = min(n, a + int(round(length * sample_rate)))
        if b > a:
            t = np.arange(b - a) / sample_rate
            bus[a:b] += 0.5 * np.sin(2 * math.pi * 1000.0 * t)
    pcm = (np.clip(bus, -1, 1) * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(stereo.tobytes())
    return True


def _stub_audio_graph(*, voice_fragment: str, bleeps_e, warp, out_duration: float, has_bus: bool, voice_gain: float,
                      loudness: str, static_gain_db, loop_crossfade: float) -> str:
    """The §9 shape without the warp pieces: voice → 48 kHz stereo → apad/atrim to out_duration (+ bus) → [fxa_out]."""
    d = f"{out_duration:.6f}"
    mutes = "+".join(f"between(t,{a:.4f},{b:.4f})" for a, b in bleeps_e)
    mute = f",volume=0:enable='{mutes}'" if mutes else ""
    parts = [voice_fragment,
             f"[fxa_voice0]aresample=48000,asetnsamples=n=48:p=0{mute},aformat=sample_fmts=fltp:channel_layouts=stereo,"
             f"apad=whole_dur={d},atrim=0:{d}[fxa_voice]"]
    if has_bus:
        parts.append("[2:a]aformat=sample_fmts=fltp:channel_layouts=stereo[fxa_bus]")
        parts.append("[fxa_voice][fxa_bus]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[fxa_out]")
    else:
        parts.append("[fxa_voice]anull[fxa_out]")
    return ";".join(parts)


STUB_AUDIO = SimpleNamespace(
    build_bus=_stub_build_bus,
    measure_voice_lufs=lambda input_path, voice_fragment, *, duration: None,
    audio_graph=_stub_audio_graph,
)


class StubAnalysis:
    """Records calls; returns a fixed output-space face track and writes a half-frame matte when asked."""

    def __init__(self, face: dict | None = None, person: bool = True) -> None:
        self.calls: list[dict] = []
        self.face = face
        self.person = person

    def analyse(self, source, head, head_label, *, out_w, out_h, face, matte_windows, matte_dest):
        self.calls.append({"face": face, "matte_windows": list(matte_windows), "matte_dest": matte_dest})
        windows = [MatteWindow(start=a, end=b, person=self.person) for a, b in matte_windows]
        if matte_dest is not None and matte_windows:
            write_half_matte(Path(matte_dest), w=270, h=480, dur=10.0, fps=15, codec="ffv1")
        return (self.face if face else None), windows


def write_half_matte(path: Path, *, w: int, h: int, dur: float, fps: int = 15, codec: str = "libx264") -> Path:
    """Gray matte: left half 0 (background), right half 255 (person)."""
    graph = (f"color=c=black:s={w // 2}x{h}:r={fps}:d={dur}[l];color=c=white:s={w - w // 2}x{h}:r={fps}:d={dur}[r];"
             "[l][r]hstack,format=gray")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", graph]
    if codec == "ffv1":
        cmd += ["-c:v", "ffv1", "-pix_fmt", "gray"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "gray"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def use_core_bundle(monkeypatch, tmp_path_factory) -> None:
    """``FX_CORE_BUNDLE=<path>`` runs P2 with that fx-core bundle (a fresh ``build-fx-core.mjs --out`` build) in a copy
    of ``src/fx_runtime``; otherwise the committed bundle is used."""
    bundle = os.environ.get("FX_CORE_BUNDLE")
    if not bundle:
        return
    runtime = tmp_path_factory.mktemp("fx_runtime")
    for name in ("main.ts", "host.ts", "protocol.ts", "deno.json", "deno.lock"):
        shutil.copy2(fx_chain.FX_RUNTIME_DIR / name, runtime / name)
    shutil.copy2(bundle, runtime / "fx-core.js")
    monkeypatch.setattr(fx_chain, "FX_RUNTIME_DIR", runtime)
    monkeypatch.setattr(fx_chain, "FX_MAIN_TS", runtime / "main.ts")
    monkeypatch.setattr(fx_chain, "FX_DENO_CONFIG", runtime / "deno.json")


@pytest.fixture
def chain_deps(monkeypatch, tmp_path_factory):
    """Stubs fx_audio / fx_analysis unless FX_CHAIN_REAL_DEPS=1. Returns the analysis stub (or None when real)."""
    use_core_bundle(monkeypatch, tmp_path_factory)
    if os.environ.get("FX_CHAIN_REAL_DEPS") == "1":
        return None
    analysis = StubAnalysis()
    monkeypatch.setattr(fx_chain, "_audio_module", lambda: STUB_AUDIO)
    monkeypatch.setattr(fx_chain, "_analysis_module", lambda: analysis)
    return analysis
