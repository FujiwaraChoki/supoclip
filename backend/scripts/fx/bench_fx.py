"""Dynamic edits render bench (FINAL §17, WP21): stage-2 wall time with FX against the legacy render.

Renders one synthetic 60 s 1080x1920 clip (lavfi ``testsrc2`` plus an amplitude-modulated tone, with a cached
transcript of ~150 words that has numbers, swears and punchlines) through ``VideoService.create_single_clip``:

- ``legacy``: the ``default`` caption template, no ``fx`` (the code path every non-FX task takes);
- ``style_only``: an FX template (``marker_circle``) with intensity off;
- ``subtle``, ``bold``, ``max``: ``EditStyle(intensity=…)`` with the ``slam`` template;
- ``behind_head``: intensity bold with the ``behind_head`` template (adds the person matte to the analysis).

Beats come from ``heuristic_beats`` (``_llm_beats`` is replaced), so nothing touches the network and the LLM time
(§17: ≤6 s, concurrent with B-roll planning) is not in these numbers. Stage-2 wall time is the whole
``create_single_clip`` call, so it includes face/matte analysis, the audio bus and the voice loudness pass. The chain's
own timings (``FxResult.timings``: analysis, bus, lufs, p1, p2, p3, p2_draw, total) are captured by wrapping
``media.fx_chain.run_fx_chain``; they are what the worker logs as ``fx_chain … timings=``. P1, P2 and P3 run as one
pipe, so their elapsed times overlap (each is the time until that process exited); ``p2_draw`` is the compositor's
own drawing time, the part of the pipeline the FX add.

The process is pinned to ``--cpus`` cores (default 4, the per-chain budget of §17) with ``sched_setaffinity``; ffmpeg
and deno inherit it. Ratios over 2.0x are over budget, over 3.0x a release blocker (§17).

Usage (from ``backend/``, ffmpeg + deno 2.9.8 on PATH)::

    .venv/bin/python scripts/fx/bench_fx.py [--runs 2] [--cpus 4] [--modes legacy,subtle,...] [--json out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

WIDTH, HEIGHT, FPS, DURATION = 1080, 1920, 30, 60.0
HOOK = "We made one hundred grand in a day"
LINES = (
    "So this is honestly the wildest thing that ever happened to me.",
    "We made one hundred thousand dollars in a single day.",
    "And then everything just fell apart in one hour, what the hell.",
    "Here is the secret nobody tells you about growth.",
    "Ninety percent of people quit right before it works.",
    "I was shocked, absolutely shocked, when I saw the numbers.",
    "Wait for it, because the ending is the best part.",
    "That one change tripled our revenue in three weeks.",
)
MODES = ("legacy", "style_only", "subtle", "bold", "max", "behind_head")
BUDGET, BLOCKER = 2.0, 3.0


def make_source(directory: Path) -> tuple[Path, list[dict]]:
    video = directory / "source.mp4"
    audio = "aevalsrc=0.4*sin(2*PI*180*t)*(0.55+0.45*sin(2*PI*3.1*t)):s=48000:d=%s" % DURATION
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d={DURATION}",
        "-f", "lavfi", "-i", audio,
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(video),
    ], check=True, capture_output=True)
    words, t, step = [], 0.25, 0.36
    text_parts = []
    while t < DURATION - 1.0:
        for line in LINES:
            for token in line.split():
                if t >= DURATION - 1.0:
                    break
                words.append({"text": token, "start": int(t * 1000), "end": int((t + 0.28) * 1000),
                              "confidence": 0.99, "speaker": "A"})
                text_parts.append(token)
                t += step
            t += 0.3  # sentence pause
    text = " ".join(text_parts)
    video.with_suffix(".transcript_cache.json").write_text(json.dumps(
        {"version": 2, "words": words, "utterances": [], "text": text, "language_code": "en"}))
    return video, words


def segment(text: str) -> dict:
    return {"start_time": "00:00", "end_time": "01:00", "text": text, "relevance_score": 0.9, "reasoning": "bench",
            "hook_title": HOOK, "keep_ranges": [[0.0, DURATION]]}


def mode_args(mode: str):
    from src.edit_fx.contracts import EditStyle
    from src.edit_fx.jobs import FxJob

    if mode == "legacy":
        return "default", None
    if mode == "style_only":
        return "marker_circle", FxJob(style=EditStyle(), task_id="bench", clip_index=0)
    if mode == "behind_head":
        return "behind_head", FxJob(style=EditStyle(intensity="bold"), task_id="bench", clip_index=0)
    return "slam", FxJob(style=EditStyle(intensity=mode), task_id="bench", clip_index=0)


async def run_once(mode: str, video: Path, text: str, out_dir: Path, captured: list) -> dict:
    from src.services.video_service import VideoService

    template, job = mode_args(mode)
    captured.clear()
    started = time.perf_counter()
    info = await VideoService.create_single_clip(
        video, segment(text), 0, out_dir, caption_template=template, output_format="vertical",
        add_subtitles=True, max_clip_seconds=DURATION, fx=job,
    )
    wall = time.perf_counter() - started
    if info is None:
        raise RuntimeError(f"{mode}: render failed")
    row: dict = {"mode": mode, "template": template, "wall_s": round(wall, 2), "fx_meta": info.get("fx_meta")}
    if captured:
        result, cues = captured[-1]
        row.update({"level": result.level, "cues": cues, "timings_ms": dict(result.timings)})
    Path(info["path"]).unlink(missing_ok=True)
    return row


def summarise(rows: list[dict]) -> list[dict]:
    by_mode: dict[str, list[dict]] = {}
    for row in rows:
        by_mode.setdefault(row["mode"], []).append(row)
    legacy = statistics.median(r["wall_s"] for r in by_mode.get("legacy", [{"wall_s": float("nan")}]))
    out = []
    for mode, runs in by_mode.items():
        wall = statistics.median(r["wall_s"] for r in runs)
        timings: dict[str, float] = {}
        for key in ("analysis", "bus", "lufs", "p1", "p2", "p3", "p2_draw", "total"):
            values = [r["timings_ms"][key] for r in runs if key in r.get("timings_ms", {})]
            if values:
                timings[key] = round(statistics.median(values), 1)
        ratio = wall / legacy if legacy == legacy and legacy > 0 else None
        verdict = "" if ratio is None or mode == "legacy" else (
            "BLOCKER" if ratio > BLOCKER else "over budget" if ratio > BUDGET else "ok")
        out.append({"mode": mode, "template": runs[0]["template"], "runs": len(runs), "wall_s": round(wall, 2),
                    "ratio": round(ratio, 2) if ratio is not None else None, "verdict": verdict,
                    "level": runs[0].get("level"), "cues": runs[0].get("cues"), "timings_ms": timings,
                    "p2_ms_per_frame": round(timings["p2_draw"] / (DURATION * FPS), 1) if "p2_draw" in timings
                    else None})
    return out


def markdown(summary: list[dict]) -> str:
    def sec(value):
        return "–" if value is None else f"{value / 1000:.1f}"

    lines = ["| Mode | Template | Cues | Stage 2 wall (s) | × legacy | FX chain (s) | Analysis (s) | Bus+LUFS (s) "
             "| P1‖P2‖P3 pipeline (s) | P2 draw (s) | P2 draw ms/frame | Verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in summary:
        t = row["timings_ms"]
        bus = (t.get("bus", 0) + t.get("lufs", 0)) if ("bus" in t or "lufs" in t) else None
        pipeline = max((t[k] for k in ("p1", "p2", "p3") if k in t), default=None)
        lines.append(
            f"| {row['mode']} | {row['template']} | {row['cues'] if row['cues'] is not None else '–'} "
            f"| {row['wall_s']:.1f} | {row['ratio'] if row['ratio'] is not None else '–'} | {sec(t.get('total'))} "
            f"| {sec(t.get('analysis'))} | {sec(bus)} | {sec(pipeline)} | {sec(t.get('p2_draw'))} "
            f"| {row['p2_ms_per_frame'] if row['p2_ms_per_frame'] is not None else '–'} | {row['verdict']} |")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--cpus", type=int, default=4, help="pin to this many cores (0 = no pinning)")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--keep", type=Path, default=None, help="work directory to keep (default: a temp dir)")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"{tool} not on PATH", file=sys.stderr)
            return 2
    if args.cpus and hasattr(os, "sched_setaffinity"):
        cores = sorted(os.sched_getaffinity(0))[: args.cpus]
        os.sched_setaffinity(0, cores)
    logging.basicConfig(level=logging.WARNING)

    from src.edit_fx import env as fx_env
    from src.edit_fx.beats import heuristic_beats
    from src.media import fx_chain
    from src.services import video_service

    if not shutil.which(fx_env.fx_deno_bin()):
        print("deno not on PATH (FX would render at level safe)", file=sys.stderr)
        return 2

    async def heuristic(words, clip_seconds, timing, language):
        return heuristic_beats(words, timing=timing)

    video_service._llm_beats = heuristic
    captured: list = []
    real_chain = fx_chain.run_fx_chain

    def chain(**kwargs):
        result = real_chain(**kwargs)
        request = kwargs.get("request")
        captured.append((result, len(request.plan.cues) if request is not None else None))
        return result

    fx_chain.run_fx_chain = chain

    work_ctx = tempfile.TemporaryDirectory(prefix="bench_fx_") if args.keep is None else None
    work = Path(work_ctx.name) if work_ctx else args.keep
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    video, words = make_source(work)
    text = " ".join(w["text"] for w in words)
    print(f"source: {video} ({len(words)} words, built in {time.perf_counter() - started:.1f}s)", file=sys.stderr)
    out_dir = work / "clips"
    out_dir.mkdir(exist_ok=True)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    rows = []
    for run in range(args.runs):
        for mode in modes:
            row = await run_once(mode, video, text, out_dir, captured)
            row["run"] = run
            print(json.dumps(row), file=sys.stderr)
            rows.append(row)

    summary = summarise(rows)
    deno_version = subprocess.run([fx_env.fx_deno_bin(), "--version"], capture_output=True, text=True).stdout
    ffmpeg_version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    report = {
        "clip": {"width": WIDTH, "height": HEIGHT, "fps": FPS, "seconds": DURATION, "words": len(words)},
        "host": {"machine": platform.machine(), "cpu_count": os.cpu_count(),
                 "pinned_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                 "ffmpeg": ffmpeg_version, "deno": deno_version.split("\n")[0]},
        "beats": "heuristic (no LLM)",
        "rows": rows,
        "summary": summary,
    }
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    print(markdown(summary))
    if work_ctx:
        work_ctx.cleanup()
    return 1 if any(r["verdict"] == "BLOCKER" for r in summary) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
