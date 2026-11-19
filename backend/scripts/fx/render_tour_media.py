"""Render the Dynamic edits tour announcement (frontend/public/tours/dynamic-edits.{webm,mp4,webp}).

The same six seconds of a real clip at Subtle, Bold and Max, side by side on the 1600x800 announcement card, each
panel labelled with its intensity. The panels are the homepage's Dynamic edits renders
(``frontend/public/clips/dynamic-edits/interview-{subtle,bold,max}.mp4``, see ``lib/dynamic-edits-demo.ts`` for the
source and how they were made): what the card shows is exactly what those intensities produce, from the public
episode the homepage demo already uses.

    cd backend && .venv/bin/python scripts/fx/render_tour_media.py

The card is 2:1 (feature-tour.tsx, ``aspect-[2/1]``, object-cover) and ships as a VP9 WebM, an H.264 MP4 for browsers
without VP9 and a WebP poster (docs/feature-tours.md). The loop is seamless: its last half second crossfades into the
first. Needs ffmpeg (libvpx-vp9, libx264, libwebp) and ImageMagick (``magick``) for the frame and labels.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
CLIPS = REPO / "frontend" / "public" / "clips" / "dynamic-edits"
OUT_DIR = REPO / "frontend" / "public" / "tours"
FONT = BACKEND / "fonts" / "Inter.ttf"

CARD_W, CARD_H = 1600, 800
PANEL_W, PANEL_H = 476, 760          # 0.626: a 9:16 frame with a little of its top and bottom cut
GAP = 26
TOP = (CARD_H - PANEL_H) // 2
LEFT = (CARD_W - 3 * PANEL_W - 2 * GAP) // 2
RADIUS = 30
BACKGROUND = "#0b0b0b"
# Where the panels start in the source clips, and how much of them the card shows. The window opens on the hook title
# and Bold's first boxed key word, and holds Max's first punch-ins.
START, LENGTH, FADE = 0.2, 6.0, 0.5
CROP_Y = 46                          # of the scaled 476x846 frame: keeps the hook title, trims under the mic
POSTER_AT = 1.9                      # every panel has a caption on screen and Max is punched in
PANELS = (("subtle", "Subtle"), ("bold", "Bold"), ("max", "Max"))


def run(*args: str) -> None:
    subprocess.run(list(args), check=True)


def frame_overlay(path: Path) -> None:
    """The card around the panels: background with rounded windows, and an intensity pill at the foot of each."""
    mask = ["-size", f"{CARD_W}x{CARD_H}", "xc:white", "-fill", "black"]
    labels = ["-font", str(FONT), "-weight", "600", "-pointsize", "33", "-gravity", "North"]
    for index, (_, label) in enumerate(PANELS):
        x = LEFT + index * (PANEL_W + GAP)
        mask += ["-draw", f"roundrectangle {x},{TOP} {x + PANEL_W - 1},{TOP + PANEL_H - 1} {RADIUS},{RADIUS}"]
        cx = x + PANEL_W // 2
        half = 30 + 12 * len(label)
        y0, y1 = TOP + PANEL_H - 92, TOP + PANEL_H - 36
        labels += ["-fill", "rgba(255,255,255,0.94)", "-draw", f"roundrectangle {cx - half},{y0} {cx + half},{y1} 28,28",
                   "-fill", "#0c0a09", "-annotate", f"{cx - CARD_W // 2:+d}+{y0 + 11}", label]
    run("magick", "-size", f"{CARD_W}x{CARD_H}", f"xc:{BACKGROUND}",
        "(", *mask, ")", "-alpha", "off", "-compose", "CopyOpacity", "-composite", "-compose", "Over", *labels, str(path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        overlay = Path(tmp) / "frame.png"
        frame_overlay(overlay)
        inputs: list[str] = []
        chains: list[str] = []
        for index, (name, _) in enumerate(PANELS):
            inputs += ["-ss", f"{START}", "-t", f"{LENGTH + FADE}", "-i", str(CLIPS / f"interview-{name}.mp4")]
            chains.append(f"[{index}:v]fps=30,scale={PANEL_W}:-2:flags=lanczos,crop={PANEL_W}:{PANEL_H}:0:{CROP_Y},"
                          f"setsar=1[p{index}]")
        inputs += ["-i", str(overlay)]
        x = [LEFT + i * (PANEL_W + GAP) for i in range(len(PANELS))]
        graph = ";".join(chains) + (
            f";color=c={BACKGROUND}:s={CARD_W}x{CARD_H}:r=30:d={LENGTH + FADE}[bg]"
            f";[bg][p0]overlay={x[0]}:{TOP}[c0];[c0][p1]overlay={x[1]}:{TOP}[c1];[c1][p2]overlay={x[2]}:{TOP}[c2]"
            f";[c2][3:v]overlay=0:0,format=yuv420p,split[a][b]"
            # Seamless loop: play from FADE to the end, crossfading the last FADE seconds into the first.
            f";[a]trim={FADE}:{LENGTH + FADE},setpts=PTS-STARTPTS[body];[b]trim=0:{FADE},setpts=PTS-STARTPTS[head]"
            f";[body][head]xfade=transition=fade:duration={FADE}:offset={LENGTH - FADE},format=yuv420p[out]"
        )
        master = Path(tmp) / "card.mkv"
        run("ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-an",
            "-c:v", "ffv1", str(master))
        out = args.out
        out.mkdir(parents=True, exist_ok=True)
        run("ffmpeg", "-v", "error", "-y", "-i", str(master), "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36",
            "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-an", str(out / "dynamic-edits.webm"))
        run("ffmpeg", "-v", "error", "-y", "-i", str(master), "-c:v", "libx264", "-preset", "slow", "-crf", "26",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(out / "dynamic-edits.mp4"))
        run("ffmpeg", "-v", "error", "-y", "-ss", f"{POSTER_AT}", "-i", str(master), "-frames:v", "1",
            "-c:v", "libwebp", "-quality", "82", str(out / "dynamic-edits.webp"))
    for suffix in ("webm", "mp4", "webp"):
        path = args.out / f"dynamic-edits.{suffix}"
        print(f"{path}: {path.stat().st_size / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
