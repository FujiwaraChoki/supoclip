"""Render the Dynamic edits tour announcement (frontend/public/tours/dynamic-edits.{webm,mp4,webp}).

Three 9:16 panels side by side on the 2:1 announcement card, one per intensity (Subtle, Bold, Max), each drawing a
listed animated caption style with that intensity's caption look. Each is a real dynamic-edits render through the
production chain (ffmpeg → deno fx-core compositor → ffmpeg, ``media.fx_chain.run_fx_chain``) of a procedural demo
scene: a gradient backdrop with a swaying silhouette, no stock footage and no real person. Each panel's beats are
written here (standing in for the beat model) and the real resolver turns them into the plan, so what the card shows
is what a clip with those beats gets. Behind Head is left out: it needs a real person for its matte.

    cd backend && .venv/bin/python scripts/fx/render_tour_media.py            # writes the three files
    cd backend && .venv/bin/python scripts/fx/render_tour_media.py --keep     # also keeps the scratch renders

Like every announcement (docs/feature-tours.md) the card is 1600x800 and ships as a VP9 WebM, an H.264 MP4 for
browsers without VP9 (Safari on older iOS) and a WebP poster. Needs ffmpeg (libvpx-vp9, libx264, libwebp) and deno with the fx runtime cache (``cd src/fx_runtime && deno install --frozen``).
The loop is seamless: each panel ends with the loop-ending crossfade back to its first frame.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from src.edit_fx import assets as fx_assets  # noqa: E402
from src.edit_fx.beats import words_hash  # noqa: E402
from src.edit_fx.caption_style import look_caption_style  # noqa: E402
from src.edit_fx.contracts import Beat, EditBeats, EditStyle, caption_look_for  # noqa: E402
from src.edit_fx.resolver import ResolveInput, resolve  # noqa: E402
from src.media import fx_chain  # noqa: E402

REPO = BACKEND.parent
OUT_DIR = REPO / "frontend" / "public" / "tours"
FONTS = BACKEND / "fonts"

FPS = 30
# 6 s: the shortest clip that gets at least one of every budgeted effect (resolver.per10_cap), so Subtle shows its emoji.
DURATION = 6.0
# Each panel renders at this size, then is scaled into the card.
PANEL_W, PANEL_H = 540, 960
# Captions are drawn as if the panel were this much wider: the card shows each panel at about 400 px wide, where the
# looks' real sizes would be hard to read.
CAPTION_SCALE = 1.4
# The announcement card is 2:1 (feature-tour.tsx, `aspect-[2/1]`, object-cover), 1600x800 (docs/feature-tours.md).
CARD_W, CARD_H = 1600, 800
SHOWN_H = 720
SHOWN_W = 406  # 9:16 at 720 tall, even
GAP = 48
POSTER_AT = 2.6  # a frame where every panel has its effect on screen

# (template, intensity, spoken line, beats as (kind, first word, last word, extra fields), backdrop top/bottom colours).
# The beats stand in for the beat model's tags. One panel per intensity, each with a listed style and that
# intensity's caption look: Subtle's outlined subtitle with an emoji, Bold's Hormozi pill revealed word by word with
# the key word boxed, Max's heavy outlined caps.
PANELS: tuple[tuple[str, str, str, tuple[tuple[str, int, int, dict], ...], tuple[int, int]], ...] = (
    ("emoji_drop", "subtle", "We made our first million dollars online",
     (("hook", 0, 2, {}), ("number", 4, 5, {"strength": 3, "tone": "hype", "emoji": "\U0001F4B0"})),
     (0x2A3F9E, 0x0B0F2E)),
    ("highlighter_swipe", "bold", "The secret is showing up every single day",
     (("hook", 0, 1, {}), ("emphasis", 3, 3, {"strength": 3, "tone": "hype"}),
      ("punchline", 5, 7, {"strength": 3, "tone": "hype"})),
     (0x1F6B55, 0x061A14)),
    ("highlighter_swipe", "max", "This changes absolutely everything for you",
     (("hook", 0, 1, {}), ("emphasis", 3, 3, {"strength": 3, "tone": "shocked"}),
      ("payoff", 4, 6, {"strength": 3, "tone": "hype"})),
     (0x8A2442, 0x1C0710)),
)


def words_for(line: str, *, start: float = 0.35, end: float = DURATION - 0.7) -> list[dict]:
    """Evenly spoken words over [start, end] (longer words take a little longer), clip-timeline shape."""
    tokens = line.split()
    weights = [0.6 + len(token) * 0.08 for token in tokens]
    total = sum(weights)
    span = end - start
    words, t = [], start
    for index, (token, weight) in enumerate(zip(tokens, weights)):
        length = span * weight / total
        words.append({"id": f"w{index}", "start": round(t, 3), "end": round(t + length - 0.04, 3), "text": token})
        t += length
    return words


def backdrop(path: Path, colours: tuple[int, int]) -> Path:
    """A 9:16 gradient with a softly swaying silhouette (head and shoulders), silent, BT.709 tagged."""
    top, bottom = colours
    tr, tg, tb = (top >> 16) & 255, (top >> 8) & 255, top & 255
    br, bg, bb = (bottom >> 16) & 255, (bottom >> 8) & 255, bottom & 255

    def channel(a: int, b: int, shade: int) -> str:
        grad = f"({a}+({b}-{a})*Y/H)"
        cx = f"(W/2+14*sin(2*PI*T/{DURATION}))"  # one sway per loop, so the loop is seamless
        head = f"lte(hypot(X-{cx},(Y-0.40*H)*0.92),0.15*W)"
        body = f"lte(hypot((X-{cx})/(0.42*W),(Y-0.86*H)/(0.24*H)),1)"
        rim = f"lte(hypot(X-{cx},(Y-0.40*H)*0.92),0.158*W)"
        return f"if({head}+{body},{shade},if({rim},{grad}+40,{grad}))"

    geq = f"geq=r='{channel(tr, br, 18)}':g='{channel(tg, bg, 20)}':b='{channel(tb, bb, 26)}'"
    vsrc = f"color=c=black:s={PANEL_W}x{PANEL_H}:r={FPS}:d={DURATION},format=rgb24,{geq}"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", vsrc,
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
         str(path)],
        check=True,
    )
    return path


def beats_for(words: list[dict], specs: tuple[tuple[str, int, int, dict], ...]) -> EditBeats:
    beats = []
    for n, (kind, first, last, extra) in enumerate(specs):
        span = words[first:last + 1]
        beats.append(Beat(id=f"b{n}", kind=kind, start=span[0]["start"], end=span[-1]["end"],
                          text=" ".join(w["text"] for w in span), source="heuristic",
                          words=tuple(w["text"].lower() for w in span), **extra))
    hook = next((b for b in beats if b.kind == "hook"), None)
    return EditBeats(words_hash=words_hash(words), source="heuristic", mood="hype",
                     hook_end=hook.end if hook else None, beats=tuple(beats))


def render_panel(scratch: Path, index: int, template_id: str, intensity: str, line: str,
                 specs: tuple[tuple[str, int, int, dict], ...], colours: tuple[int, int]) -> Path:
    source = backdrop(scratch / f"backdrop{index}.mp4", colours)
    words = words_for(line)
    # Sound is muted on the card: leave the bus out. Loop ending makes the 5 s loop seamless.
    style = EditStyle(intensity=intensity, sfx=False, music=False, speed=False, loop_ending=True)
    index_ = fx_assets.load_index()
    plan = resolve(
        ResolveInput(
            words=tuple(words), beats=beats_for(words, specs), duration=DURATION,
            sfx_variants=fx_assets.sfx_variants(), beds=index_.beds, bed_lengths=index_.bed_lengths,
        ),
        style, template_id, seed=4242 + index,
    )
    # The intensity's caption look, as generation draws a listed style (video_utils._fx_ladder_setup), larger for the card.
    look = caption_look_for(template_id, intensity)
    caption = look_caption_style(template_id, look, width=round(PANEL_W * CAPTION_SCALE), height=PANEL_H,
                                 caption_y=None)
    request = fx_chain.FxRequest(
        plan=plan, words=[{**w, "highlight": False} for w in words], caption=caption, loudness="none",
    )
    out = scratch / f"panel{index}.mp4"
    result = fx_chain.run_fx_chain(
        input_path=source, output_path=out, head=f"[0:v]scale={PANEL_W}:{PANEL_H},setsar=1[base]",
        head_label="[base]", out_w=PANEL_W, out_h=PANEL_H, broll_overlays=[], broll_inputs=[],
        subtitle_ass_path=None, fonts_dir=FONTS, request=request, mode="full", scratch_dir=scratch / f"s{index}",
        has_audio=False,
        encode_args=["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", "-r", str(FPS)],
    )
    if not result.ok:
        raise SystemExit(f"panel {template_id} failed: {result.error}")
    kinds = [(c.kind, getattr(c, "variant", getattr(c, "mode", "")), c.beat, round(c.start, 2)) for c in plan.cues]
    print(f"{template_id:>14} {intensity:>6}: {len(plan.cues)} cues {kinds}, p2 {result.timings.get('p2', 0):.0f} ms")
    return out


def compose(panels: list[Path], webm: Path, mp4: Path, poster: Path) -> None:
    """The panels side by side on the card's dark background: VP9 WebM and H.264 MP4 (no audio), and a WebP poster."""
    left = (CARD_W - len(panels) * SHOWN_W - (len(panels) - 1) * GAP) // 2
    top = (CARD_H - SHOWN_H) // 2
    inputs: list[str] = []
    for panel in panels:
        inputs += ["-i", str(panel)]
    graph = [f"color=c=0x0A0A0B:s={CARD_W}x{CARD_H}:r={FPS}:d={DURATION}[bg0]"]
    for i in range(len(panels)):
        graph.append(f"[{i}:v]scale={SHOWN_W}:{SHOWN_H}:flags=lanczos,setsar=1[p{i}]")
    for i in range(len(panels)):
        x = left + i * (SHOWN_W + GAP)
        graph.append(f"[bg{i}][p{i}]overlay={x}:{top}:shortest=1[bg{i + 1}]")
    graph.append(f"[bg{len(panels)}]format=yuv420p[out]")
    filter_complex = ";".join(graph)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filter_complex, "-map", "[out]", "-an",
         "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "38", "-row-mt", "1", "-deadline", "good", "-cpu-used", "2",
         "-pix_fmt", "yuv420p", str(webm)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filter_complex, "-map", "[out]", "-an",
         "-c:v", "libx264", "-preset", "slow", "-crf", "30", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         str(mp4)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filter_complex,
         "-map", "[out]", "-ss", str(POSTER_AT), "-frames:v", "1", "-c:v", "libwebp", "-quality", "72",
         "-compression_level", "6", str(poster)],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory and print its path")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="directory for dynamic-edits.{webm,mp4,webp}")
    args = parser.parse_args()
    for binary in ("ffmpeg", "ffprobe", "deno"):
        if not shutil.which(binary):
            raise SystemExit(f"{binary} is required")
    scratch = Path(tempfile.mkdtemp(prefix="fx-tour-"))
    try:
        panels = [render_panel(scratch, i, *spec) for i, spec in enumerate(PANELS)]
        args.out.mkdir(parents=True, exist_ok=True)
        files = [args.out / f"dynamic-edits.{ext}" for ext in ("webm", "mp4", "webp")]
        compose(panels, *files)
        print(", ".join(f"{path} {path.stat().st_size / 1024:.0f} KB" for path in files))
    finally:
        if args.keep:
            print(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
