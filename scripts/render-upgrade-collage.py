#!/usr/bin/env python3
"""Programmatically render the seamlessly looping upgrade-modal example-clip collage.

Produces two assets committed to the frontend:
  - frontend/public/videos/upgrade-collage.mp4        (H.264 yuv420p, faststart, muted)
  - frontend/public/videos/upgrade-collage-poster.jpg (frame 0 for instant paint & reduced motion)

The collage composes six real completed SupoClip vertical clips (gaming, podcasts/interviews,
and vlogs) into two wide staggered 9:16 columns playing simultaneously while drifting in
opposite vertical directions (left column up, right column down) across an exact seamless loop.

Usage:
    python3 scripts/render-upgrade-collage.py
    python3 scripts/render-upgrade-collage.py --offline   # use bundled frontend/public/clips/ only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "frontend" / "public" / "videos"
LOCAL_CLIPS_DIR = REPO / "frontend" / "public" / "clips"

DEFAULT_MANIFEST_URL = (
    os.environ.get("EXAMPLES_MANIFEST_URL", "").strip()
    or "https://supoclip-examples.sami-c72.workers.dev/examples/v1/manifest.json"
)

# Canvas and 2-column 9:16 tile geometry (matches ~366x630 right-panel gallery at 2x so object-cover never clips columns)
CANVAS_W = 720
CANVAS_H = 1240
PAD_X = 18
GAP_X = 16
GAP_Y = 16
TILE_W = (CANVAS_W - 2 * PAD_X - GAP_X) // 2  # 334 px
TILE_H = 594                                   # 334 * 16 / 9 = 593.78 -> 594 (even)
PITCH = TILE_H + GAP_Y                         # 610 px
TILES_PER_COL = 3
PERIOD = TILES_PER_COL * PITCH                 # 1830 px (exact wrap distance)
RIGHT_COL_STAGGER = PITCH // 2                 # 305 px half-tile masonry offset
CORNER_RADIUS = 22

# Loop timing
FPS = 30
DURATION = 12.0                                # seconds per full seamless loop
TOTAL_FRAMES = int(round(DURATION * FPS))      # 360 frames
CLIP_FADE = 0.6                                # crossfade tail -> head so every clip loops seamlessly
CLIP_START_OFFSET = 2.0                        # skip initial silence so captions & action are active immediately

# Neutral dark stage behind the rounded 9:16 cards
BG_HEX = "#0e0e11"
BG_RGB = (14, 14, 17)

# Preferred clip IDs per column (left drifts UP, right drifts DOWN) for balanced visual variety.
# If the manifest changes, the selector falls back automatically to round-robin top clips by group.
PREFERRED_COLUMNS: tuple[tuple[str, ...], tuple[str, ...]] = (
    (
        "gta-6-better-than-we-thought-1",
        "how-drugs-get-into-prison-1",
        "72-hours-in-japan-1",
    ),
    (
        "gta-6-part-6-2",
        "how-drugs-get-into-prison-2",
        "72-hours-in-japan-2",
    ),
)

LOCAL_FALLBACK_FILES: tuple[Path, ...] = (
    LOCAL_CLIPS_DIR / "podcast-1.mp4",
    LOCAL_CLIPS_DIR / "articles" / "price-1.mp4",
    LOCAL_CLIPS_DIR / "articles" / "ton-1.mp4",
    LOCAL_CLIPS_DIR / "podcast-2.mp4",
    LOCAL_CLIPS_DIR / "articles" / "price-2.mp4",
    LOCAL_CLIPS_DIR / "articles" / "ton-2.mp4",
)


def run(*args: str) -> None:
    subprocess.run(list(args), check=True)


def probe_duration(video_path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        text=True,
    ).strip()
    return float(out)


def create_card_masks(tmp_dir: Path) -> tuple[Path, Path]:
    """Create a supersampled rounded-rectangle alpha mask and subtle 1px border rim overlay."""
    scale = 4
    w, h, r = TILE_W * scale, TILE_H * scale, CORNER_RADIUS * scale

    mask_hi = Image.new("L", (w, h), 0)
    draw_mask = ImageDraw.Draw(mask_hi)
    draw_mask.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    mask = mask_hi.resize((TILE_W, TILE_H), Image.Resampling.LANCZOS)
    mask_path = tmp_dir / "card-mask.png"
    mask.save(mask_path)

    rim_hi = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_rim = ImageDraw.Draw(rim_hi)
    draw_rim.rounded_rectangle(
        (scale // 2, scale // 2, w - 1 - scale // 2, h - 1 - scale // 2),
        radius=r,
        outline=(255, 255, 255, 34),
        width=scale,
    )
    rim = rim_hi.resize((TILE_W, TILE_H), Image.Resampling.LANCZOS)
    rim_path = tmp_dir / "card-rim.png"
    rim.save(rim_path)

    return mask_path, rim_path


def select_manifest_clip_urls(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """Pick 6 diverse clips (left 3, right 3) from the examples manifest."""
    by_id: dict[str, str] = {}
    by_group: dict[str, list[tuple[int, str, str]]] = {"gaming": [], "interview": [], "vlog": []}
    for video in manifest.get("videos", []):
        group = video.get("group")
        for clip in video.get("clips", []):
            cid = clip.get("id")
            url = clip.get("video_url")
            score = int(round(clip.get("virality_score", 0)))
            if isinstance(cid, str) and isinstance(url, str):
                by_id[cid] = url
                if group in by_group:
                    by_group[group].append((score, cid, url))

    preferred_flat = [*PREFERRED_COLUMNS[0], *PREFERRED_COLUMNS[1]]
    if all(cid in by_id for cid in preferred_flat):
        return [(cid, by_id[cid]) for cid in preferred_flat]

    # Fallback: round-robin highest-scoring clips across groups.
    for group_list in by_group.values():
        group_list.sort(key=lambda item: item[0], reverse=True)
    picked: list[tuple[str, str]] = []
    while len(picked) < TILES_PER_COL * 2:
        progressed = False
        for group in ("gaming", "interview", "vlog"):
            if by_group[group]:
                _, cid, url = by_group[group].pop(0)
                picked.append((cid, url))
                progressed = True
                if len(picked) >= TILES_PER_COL * 2:
                    break
        if not progressed:
            break
    if len(picked) < TILES_PER_COL * 2:
        raise RuntimeError(f"Expected at least {TILES_PER_COL * 2} clips in manifest, found {len(picked)}")
    return picked


def fetch_clips(manifest_url: str, cache_dir: Path, offline: bool) -> list[Path]:
    """Download the 6 live example clips (cached across runs) or fall back to local bundled clips."""
    if not offline:
        try:
            req = urllib.request.Request(manifest_url, headers={"User-Agent": "SupoClip-Collage-Builder/1.0"})
            with urllib.request.urlopen(req, timeout=15) as response:
                manifest = json.loads(response.read().decode("utf-8"))
            selected = select_manifest_clip_urls(manifest)
            paths: list[Path] = []
            for cid, url in selected:
                dest = cache_dir / f"{cid}.mp4"
                if not dest.exists() or dest.stat().st_size == 0:
                    print(f"Downloading {cid} ...")
                    dl_req = urllib.request.Request(url, headers={"User-Agent": "SupoClip-Collage-Builder/1.0"})
                    with urllib.request.urlopen(dl_req, timeout=60) as r:
                        dest.write_bytes(r.read())
                paths.append(dest)
            return paths
        except Exception as exc:
            print(f"Remote manifest unavailable ({exc}); falling back to bundled local clips.")

    missing = [p for p in LOCAL_FALLBACK_FILES if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing local fallback clips: {missing}")
    return list(LOCAL_FALLBACK_FILES)


def prepare_looping_tile(
    source_path: Path,
    mask_path: Path,
    rim_path: Path,
    out_path: Path,
) -> None:
    """Render one seamlessly looping 9:16 rounded tile with vertical gap padding underneath."""
    src_dur = probe_duration(source_path)
    need = DURATION + CLIP_FADE
    if src_dur >= need + CLIP_START_OFFSET:
        start = CLIP_START_OFFSET
        input_args = ["-ss", f"{start:.3f}", "-t", f"{need:.3f}", "-i", str(source_path)]
    elif src_dur >= need:
        input_args = ["-t", f"{need:.3f}", "-i", str(source_path)]
    else:
        # Short local fallback clips: loop input seamlessly so we always have DURATION + CLIP_FADE.
        input_args = ["-stream_loop", "-1", "-t", f"{need:.3f}", "-i", str(source_path)]

    # Scale/crop to exact TILE_W x TILE_H, crossfade tail into head for seamless clip loop,
    # apply rounded-corner alpha mask, composite over BG_HEX with GAP_Y padding below, and add 1px rim.
    filter_complex = (
        f"[0:v]fps={FPS},"
        f"scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={TILE_W}:{TILE_H},setsar=1,format=rgba,split[a][b];"
        f"[a]trim={CLIP_FADE:.3f}:{need:.3f},setpts=PTS-STARTPTS[body];"
        f"[b]trim=0:{CLIP_FADE:.3f},setpts=PTS-STARTPTS[head];"
        f"[body][head]xfade=transition=fade:duration={CLIP_FADE:.3f}:offset={DURATION - CLIP_FADE:.3f},"
        f"format=rgba[looped];"
        f"[1:v]format=gray[mask];"
        f"[looped][mask]alphamerge[rounded];"
        f"[rounded][2:v]overlay=0:0:format=auto[card];"
        f"color=c={BG_HEX}:s={TILE_W}x{PITCH}:r={FPS}:d={DURATION:.3f},format=rgba[cellbg];"
        f"[cellbg][card]overlay=0:0:shortest=1,format=yuv444p[tile]"
    )

    run(
        "ffmpeg",
        "-v",
        "error",
        "-y",
        *input_args,
        "-loop",
        "1",
        "-i",
        str(mask_path),
        "-loop",
        "1",
        "-i",
        str(rim_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[tile]",
        "-frames:v",
        str(TOTAL_FRAMES),
        "-an",
        "-c:v",
        "ffv1",
        str(out_path),
    )


def render_collage(tile_paths: list[Path], out_dir: Path, crf: int) -> None:
    """Composite the 6 looping tiles into 2 opposing-drift columns and encode final MP4 + poster."""
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / "upgrade-collage.mp4"
    poster_path = out_dir / "upgrade-collage-poster.jpg"

    left_x = PAD_X
    right_x = PAD_X + TILE_W + GAP_X

    # Use frame index `n` (0 .. TOTAL_FRAMES-1) for exact, drift-free wrap at frame TOTAL_FRAMES == frame 0.
    left_y_expr = f"mod(n*{PERIOD}/{TOTAL_FRAMES},{PERIOD})"
    right_y_expr = f"mod({PERIOD}+{RIGHT_COL_STAGGER}-mod(n*{PERIOD}/{TOTAL_FRAMES},{PERIOD}),{PERIOD})"

    inputs: list[str] = []
    for tile in tile_paths:
        inputs += ["-i", str(tile)]

    filter_complex = (
        # Build left column (tiles 0,1,2) doubled vertically for seamless upward wrap.
        "[0:v][1:v][2:v]vstack=inputs=3,split[l1][l2];"
        "[l1][l2]vstack=inputs=2,"
        f"crop=w={TILE_W}:h={CANVAS_H}:x=0:y='{left_y_expr}':exact=1[left];"
        # Build right column (tiles 3,4,5) doubled vertically for seamless downward wrap.
        "[3:v][4:v][5:v]vstack=inputs=3,split[r1][r2];"
        "[r1][r2]vstack=inputs=2,"
        f"crop=w={TILE_W}:h={CANVAS_H}:x=0:y='{right_y_expr}':exact=1[right];"
        # Composite onto neutral dark stage and convert to yuv420p for broad playback compatibility.
        f"color=c={BG_HEX}:s={CANVAS_W}x{CANVAS_H}:r={FPS}:d={DURATION:.3f},format=yuv444p[bg];"
        f"[bg][left]overlay={left_x}:0:shortest=1[c0];"
        f"[c0][right]overlay={right_x}:0:shortest=1,format=yuv420p[out]"
    )

    run(
        "ffmpeg",
        "-v",
        "error",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-frames:v",
        str(TOTAL_FRAMES),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        str(crf),
        "-profile:v",
        "high",
        "-level",
        "4.0",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(mp4_path),
    )

    # Extract frame 0 so the poster matches the very first video frame with zero transition jump.
    run(
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(mp4_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(poster_path),
    )

    mp4_kb = mp4_path.stat().st_size / 1024
    poster_kb = poster_path.stat().st_size / 1024
    print(f"Rendered {mp4_path} ({mp4_kb:.0f} KiB, {CANVAS_W}x{CANVAS_H}, {FPS}fps, {DURATION:.1f}s)")
    print(f"Rendered {poster_path} ({poster_kb:.0f} KiB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--crf", type=int, default=26, help="H.264 CRF quality (lower = sharper/larger)")
    parser.add_argument("--offline", action="store_true", help="Skip remote manifest and use local bundled clips")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "supoclip-upgrade-collage-cache",
        help="Cache downloaded source clips between runs",
    )
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    source_clips = fetch_clips(args.manifest_url, args.cache_dir, offline=args.offline)
    print(f"Using {len(source_clips)} clips: {[p.name for p in source_clips]}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        mask_path, rim_path = create_card_masks(tmp_dir)
        tile_paths: list[Path] = []
        for idx, clip_path in enumerate(source_clips):
            print(f"Preparing looping tile {idx + 1}/{len(source_clips)} ({clip_path.name}) ...")
            tile_path = tmp_dir / f"tile-{idx}.mkv"
            prepare_looping_tile(clip_path, mask_path, rim_path, tile_path)
            tile_paths.append(tile_path)
        print("Compositing 2-column scrolling collage ...")
        render_collage(tile_paths, args.out, crf=args.crf)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
