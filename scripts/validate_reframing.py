"""Download a varied video corpus with local yt-dlp and inspect real renders.

Run from the repository root with backend dependencies and ffmpeg/ffprobe on
PATH: python scripts/validate_reframing.py --download --render
Add --input /path/to/private.mp4 for local footage. Private URLs are never saved.
Downloads, contact sheets, videos and JSON evidence stay in ignored temp/.
The corpus is opt-in; ordinary unit tests never need a network connection.
"""

import argparse
from dataclasses import asdict
from html import escape
import json
from pathlib import Path
import subprocess
import sys
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from src.media.ffmpeg import ffprobe_duration, ffprobe_video_size
from src.media.reframing import (
    analyze_vertical_clip, build_layout_plan, build_vertical_filter_plan,
    render_reframed_clip_ffmpeg,
)
from src.media.scene_context import build_context_layout_plan


# Labels describe the selected excerpt, not a semantic class inferred by code.
# Full source attribution/license details live on the Wikimedia file pages.
CORPUS = [
    ("basketball", "20130307_Steal_small_sample_(1280).ogv", 0, "fit"),
    ("basketball-practice", "2012_Team_USA_basketball_practice.ogv", 0, "fit"),
    ("presenter", "20090124_WeeklyAddress.ogv", 30, "face"),
    ("group-interview", "STS-107_crew_media_downlink_interview,_18_January_2003_(NASA_sts107fd03a).webm", 5, "fit"),
    ("desktop", "Firefox_136_on_Fedora_41,_browsing_the_English_Wikipedia_site.webm", 5, "fit"),
    ("diagram", "3DScapeCS-application-of-three-dimensional-parallel-dynamic-network-visualization-in-Cytoscape-1471-2105-14-322-S5.ogv", 0, "fit"),
    ("game", "A_box_game_written_in_Python.ogv", 10, "fit"),
    ("pedestrians", "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi", 0, "fit"),
    ("animation", "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/Megamind.avi", 0, None),
]


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])


def fit_fraction(plan, duration):
    return sum(p["end"] - p["start"] for p in plan if p["kind"] == "fit") / max(0.001, duration)


def create_variants(directory):
    """Controlled edits of real footage exercise transitions and webcam overlays."""
    presenter = directory / "presenter-excerpt.mp4"
    desktop = directory / "desktop-excerpt.mp4"
    sports = directory / "basketball-practice-excerpt.mp4"
    variants = []
    for name, background in [("desktop-webcam", desktop), ("sports-webcam", sports)]:
        path = directory / f"{name}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(background), "-i", str(presenter),
             "-filter_complex", "[0:v]scale=960:540[bg];[1:v]crop=400:400:440:60,scale=180:180[cam];"
             "[bg][cam]overlay=W-w-10:H-h-10[v]", "-map", "[v]", "-t", "10", "-an",
             "-c:v", "libx264", "-preset", "fast", str(path)])
        variants.append((name, path, "fit", "controlled webcam overlay of corpus clips"))
    for name, graph in [("portrait", "crop=404:720:438:0"),
                        ("camera-pan", "crop=960:540:x='160+100*sin(t)':y=90")]:
        path = directory / f"{name}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(presenter), "-vf", graph,
             "-t", "10", "-an", "-c:v", "libx264", "-preset", "fast", str(path)])
        variants.append((name, path, None if name == "portrait" else "face", "controlled crop of presenter"))
    path = directory / "mixed-shots.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(presenter), "-i", str(desktop), "-i", str(sports),
         "-filter_complex", "[0:v]split=2[p0][p1];"
         "[p0]trim=duration=5,setpts=PTS-STARTPTS,scale=960:540,setsar=1[a];"
         "[1:v]trim=duration=5,setpts=PTS-STARTPTS,scale=960:540,setsar=1[b];"
         "[2:v]trim=duration=5,setpts=PTS-STARTPTS,scale=960:540,setsar=1[c];"
         "[p1]trim=start=5:duration=5,setpts=PTS-STARTPTS,scale=960:540,setsar=1[d];"
         "[a][b][c][d]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-an",
         "-c:v", "libx264", "-preset", "fast", str(path)])
    variants.append(("mixed-shots", path, "mixed", "presenter / desktop / sports / presenter, 5 seconds each"))
    return variants


def write_gallery(directory, results):
    cards = []
    for result in results:
        name = escape(result["name"])
        status = "PASS" if result["passed"] else "FAIL"
        if result["expected"] is None:
            status = "Visual review (no automatic layout label)"
        video = (f'<video controls preload="metadata" src="{name}-vertical.mp4"></video>'
                 if result["rendered"] else "")
        cards.append(
            f'<section><h2>{name}</h2><p>{status} · {result["duration"]:.1f}s · '
            f'{result["analysis_seconds"]:.2f}s analysis · '
            f'full frame {result["full_frame_fraction"]:.0%} '
            f'(previous policy {result["baseline_full_frame_fraction"]:.0%})</p>'
            f'{video}<a href="{name}-contact.jpg"><img src="{name}-contact.jpg" alt="{name} source and output frames"></a>'
            f'<pre>{escape(json.dumps(result["plan"], indent=2))}</pre></section>')
    html = '<!doctype html><meta charset="utf-8"><title>Framing validation</title>' \
        '<style>body{font:16px system-ui;background:#151719;color:#eee;margin:32px auto;max-width:1100px;padding:0 20px}' \
        'section{border-top:1px solid #555;padding:24px 0}video{height:480px;max-width:100%}' \
        'img{width:100%;margin-top:20px}pre{overflow:auto;background:#222;padding:16px}</style>' \
        '<h1>Framing validation</h1><p>Source frames appear beside rendered results. ' \
        'All rendered videos are decoded completely and checked for dimensions and duration. ' \
        'Layout labels are assertions only where an expected layout is specified.</p>' + ''.join(cards)
    (directory / "index.html").write_text(html, encoding="utf-8")


def contact_sheet(source, rendered, destination, plan):
    duration = ffprobe_duration(source)
    source_capture = cv2.VideoCapture(str(source))
    output_capture = cv2.VideoCapture(str(rendered)) if rendered else None
    panels = []
    for fraction in (0.1, 0.3, 0.5, 0.7, 0.9):
        t = duration * fraction
        source_capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = source_capture.read()
        if not ok:
            continue
        tile = np.full((480, 600, 3), 24, np.uint8)
        h, w = frame.shape[:2]
        scale = min(360 / w, 360 / h)
        preview = cv2.resize(frame, (round(w * scale), round(h * scale)))
        ph, pw = preview.shape[:2]
        tile[60:60 + ph, :pw] = preview
        layout = next((p for p in plan if p["start"] <= t < p["end"]), {})
        cv2.putText(tile, f'{t:.1f}s {layout.get("kind", "?")}', (10, 30), 0, 0.7, (255, 255, 255), 2)
        if output_capture:
            output_capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, output = output_capture.read()
            if ok:
                tile[40:440, 375:600] = cv2.resize(output, (225, 400))
        panels.append(tile)
    source_capture.release()
    if output_capture:
        output_capture.release()
    if panels:
        cv2.imwrite(str(destination), cv2.hconcat(panels))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--variants", action="store_true", help="Add controlled composites of the corpus")
    parser.add_argument("--input", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, default=ROOT / "temp/framing-validation")
    parser.add_argument("--only", nargs="*", help="Limit corpus names (local inputs are always included)")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    clips = []
    for name, title, start, expected in CORPUS:
        if args.only is not None and name not in args.only:
            continue
        url = title if title.startswith("https://") else "https://commons.wikimedia.org/wiki/File:" + title
        if args.download:
            run([sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-progress",
                 "--socket-timeout", "20", "--retries", "1", "-o",
                 str(args.output / f"{name}.%(ext)s"), url])
        files = [p for p in args.output.glob(name + ".*") if p.suffix in {".ogv", ".mp4", ".webm", ".avi"}]
        if not files:
            raise FileNotFoundError(f"Missing {name}; use --download")
        clip = args.output / f"{name}-excerpt.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(files[0]),
             "-t", "20", "-map", "0:v:0", "-map", "0:a?", "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac", str(clip)])
        clips.append((name, clip, expected, url))
    clips.extend((p.stem, p, None, "local input") for p in args.input)
    if args.variants:
        clips.extend(create_variants(args.output))
    results = []
    failed = False
    for name, clip, expected, source in clips:
        print(f"Analyzing {name}...", flush=True)
        width, height = ffprobe_video_size(clip)
        duration = ffprobe_duration(clip)
        started = time.perf_counter()
        track, cuts, faces, evidence = analyze_vertical_clip(clip)
        seconds = time.perf_counter() - started
        plan = build_context_layout_plan(evidence, cuts, duration, min(1, height * 9 / 16 / width))
        baseline = build_layout_plan(track, cuts, duration)
        fraction = fit_fraction(plan, duration)
        passed = expected is None or (fraction >= 0.8 if expected == "fit" else fraction <= 0.2)
        if expected == "mixed":
            passed = all(next(p["kind"] for p in plan if p["start"] <= t < p["end"]) == kind
                         for t, kind in [(2, "face"), (7, "fit"), (12, "fit"), (17, "face")])
        failed |= not passed
        render = None
        if args.render:
            video_filter, mode, _ = build_vertical_filter_plan(clip, width, height)
            render = args.output / f"{name}-vertical.mp4"
            ok, _, _ = render_reframed_clip_ffmpeg(clip, render, "vertical", vertical_plan=(video_filter, mode))
            if not ok:
                raise RuntimeError(f"Rendering failed: {name}")
            assert ffprobe_video_size(render) == (1080, 1920)
            assert abs(ffprobe_duration(render) - duration) < 0.15
            # Decode every rendered frame: container metadata alone is insufficient.
            run(["ffmpeg", "-v", "error", "-xerror", "-i", str(render), "-f", "null", "-"])
        contact_sheet(clip, render, args.output / f"{name}-contact.jpg", plan)
        result = dict(name=name, source=source, duration=duration, dimensions=[width, height],
                      expected=expected, passed=passed, analysis_seconds=seconds,
                      full_frame_fraction=fraction, baseline_full_frame_fraction=fit_fraction(baseline, duration),
                      plan=plan, cuts=cuts, evidence=[asdict(s) for s in evidence],
                      rendered=str(render) if render else None)
        results.append(result)
        (args.output / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        write_gallery(args.output, results)
        print(f"  full frame {fraction:.0%}; baseline {fit_fraction(baseline, duration):.0%}; "
              f"{seconds:.2f}s analysis; {'PASS' if passed else 'FAIL'}", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
