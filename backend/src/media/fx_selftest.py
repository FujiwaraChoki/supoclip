"""Dynamic-edits renderer self-test: ``python -m src.media.fx_selftest``.

Run inside the built backend image (CI ``image-security.yml`` runs it with ``--network none``) and on any render
machine after a deploy. It checks, offline:

1. ffmpeg, ffprobe and deno are on PATH (deno at the pinned version);
2. ``fx_runtime/main.ts selftest`` loads ``@napi-rs/canvas`` from the baked cache with the production permissions,
   draws every cue kind and passes the colour checks;
3. a real chain render (P1 → P2 → P3) of a short synthetic clip with captions, picture FX, a freeze, SFX and a
   progress bar: frame count, A/V alignment, bt709/tv tags and an audio track;
4. the same clip at level ``safe`` (P1 → P3, no deno).

Prints one JSON line and exits 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DENO_VERSION = "2.9.8"
WIDTH, HEIGHT = 540, 960
DURATION = 1.5
FPS = 30


def _version(binary: str, *args: str) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    try:
        out = subprocess.run([path, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    first = (out.stdout or out.stderr).strip().splitlines()
    return first[0] if first else ""


def check_tools() -> dict:
    from ..edit_fx import env as fx_env

    deno = _version(fx_env.fx_deno_bin(), "--version")
    tools = {
        "ffmpeg": _version("ffmpeg", "-version"),
        "ffprobe": _version("ffprobe", "-version"),
        "deno": deno,
    }
    tools["ok"] = bool(tools["ffmpeg"] and tools["ffprobe"] and deno and f"deno {DENO_VERSION}" in deno)
    return tools


def check_deno_selftest() -> dict:
    """``main.ts selftest`` with the render argv's permissions (read: fonts + fx_assets only, no network)."""
    from ..edit_fx.assets import builtin_dir
    from ..font_registry import FONTS_DIR
    from . import fx_chain

    argv = fx_chain.build_p2_command(Path("selftest-job.json"), [FONTS_DIR, builtin_dir()])
    argv[-2:] = []                       # "render <job>" → "selftest"
    argv.append("selftest")
    started = time.monotonic()
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=180, cwd=str(fx_chain.FX_RUNTIME_DIR),
                             env={**os.environ, "NO_COLOR": "1"}, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}
    report: dict = {}
    for line in (out.stderr or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("ev") == "selftest":
            report = event
    return {
        "ok": out.returncode == 0 and bool(report.get("ok")),
        "exit": out.returncode,
        "ms": round((time.monotonic() - started) * 1000),
        "warnings": report.get("warnings", []),
        "error": None if out.returncode == 0 else (out.stderr or "")[-1500:],
    }


def _make_source(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d={DURATION}",
         "-f", "lavfi", "-i", f"sine=f=220:r=48000:d={DURATION}",
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, timeout=120,
    )


def _selftest_request():
    from ..edit_fx.contracts import EditStyle, FxCaptionStyle, FxPlan
    from .fx_chain import FxRequest

    cues = [
        {"kind": "accent", "id": "b0.accent", "start": 0.1, "end": 0.6, "seed": 11, "variant": "circle", "text": "this"},
        {"kind": "flash", "id": "b0.flash", "start": 0.1, "end": 0.25, "seed": 12},
        {"kind": "zoom", "id": "b1.zoom", "start": 0.3, "end": 0.9, "seed": 13, "mode": "punch", "scale": 1.15,
         "anchor": "center"},
        {"kind": "sfx", "id": "b0.sfx", "start": 0.1, "end": 0.1, "seed": 14, "sound": "whoosh"},
        {"kind": "freeze", "id": "b1.freeze", "start": 0.8, "end": 0.8, "seed": 15, "hold": 0.3, "label": "WAIT"},
    ]
    plan = FxPlan(style=EditStyle(intensity="bold"), template="marker_circle", seed=4242, edit_duration=DURATION,
                  cues=tuple(cues), grade="punchy", progress_bar=True)
    words = [{"id": f"w{i}", "start": round(i * 0.3, 3), "end": round(i * 0.3 + 0.25, 3), "text": text}
             for i, text in enumerate(["watch", "this", "wild", "edit", "work"])]
    caption = FxCaptionStyle(template="marker_circle", font="TikTokSans-Bold", size_px=64, color="#FFFFFF",
                             accent="#FFD23F", stroke_color="#000000", stroke_px=6, y=0.72)
    return FxRequest(plan=plan, words=words, caption=caption, hook_window=None)


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,nb_read_packets,duration,color_space,color_range", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout
    streams = json.loads(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)
    return {
        "frames": int(video["nb_read_packets"]),
        "color": [video.get("color_space"), video.get("color_range")],
        "audio": audio is not None,
        "audio_seconds": float(audio["duration"]) if audio and audio.get("duration") else None,
    }


def check_chain(mode: str) -> dict:
    from .fx_chain import run_fx_chain

    with tempfile.TemporaryDirectory(prefix="fx-selftest-") as tmp:
        root = Path(tmp)
        source = root / "source.mp4"
        _make_source(source)
        out = root / f"{mode}.mp4"
        started = time.monotonic()
        result = run_fx_chain(
            input_path=source, output_path=out,
            head=f"[0:v]scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1[base]", head_label="[base]",
            out_w=WIDTH, out_h=HEIGHT, broll_overlays=[], broll_inputs=[], subtitle_ass_path=None, fonts_dir=None,
            request=_selftest_request(), mode=mode, scratch_dir=root / "scratch",
        )
        report = {"ok": result.ok, "level": result.level, "error": result.error,
                  "ms": round((time.monotonic() - started) * 1000), "timings": result.timings}
        if not result.ok:
            return report
        info = _probe(out)
        expected = round(result.out_duration * FPS)
        av = abs(info["frames"] / FPS - (info["audio_seconds"] or 0.0))
        report.update(info, expected_frames=expected, av_seconds=round(av, 4))
        report["ok"] = (info["frames"] == expected and info["audio"] and av < 1.0 / FPS
                        and info["color"] == ["bt709", "tv"]
                        and (mode == "safe" or bool(result.applied_warp)))
        return report


def run() -> dict:
    report: dict = {"ev": "fx_selftest"}
    report["tools"] = check_tools()
    if not report["tools"]["ok"]:
        report["ok"] = False
        return report
    report["deno"] = check_deno_selftest()
    for mode in ("full", "safe"):
        try:
            report[mode] = check_chain(mode)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report[mode] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    report["ok"] = all(report[key]["ok"] for key in ("tools", "deno", "full", "safe"))
    return report


def main() -> int:
    report = run()
    print(json.dumps(report, default=str), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
