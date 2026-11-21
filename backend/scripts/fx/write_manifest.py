#!/usr/bin/env python3
"""Write backend/src/fx_assets/manifest.json from the committed asset files (FINAL.md §12).

Per file: sha256 and duration; SFX also peak/RMS dBFS (read with numpy) and their Freesound source (from
SOURCES-CC0.txt), music beds also their loop (from music/beds.json, written by import_music.py), BPM and integrated
loudness (ffmpeg ebur128). The emoji entry hashes the atlas and its map and names the font version recorded by
build_emoji_atlas.ts.

SFX variant numbers come from ``SFX_FILES`` below: variant 0 is the most neutral take of each sound (the default for
sounds added in the studio or by the AI edit), and risers alternate 1.5 s / 2.5 s, so a riser's length is
``RISER_SECONDS[variant % 2]`` (the same as the synthesised riser_0/riser_1 it replaces). Run after
import_music.py and the atlas build:

    cd backend && .venv/bin/python scripts/fx/write_manifest.py [--assets DIR]

CI never regenerates assets; tests/unit/fx/test_fx_assets_manifest.py checks the hashes against the files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ASSETS = BACKEND_DIR / "src" / "fx_assets"
MANIFEST_VERSION = 1
SR = 48_000
SFX_SOURCES = "SOURCES-CC0.txt"
RISER_SECONDS = (1.5, 2.5)   # riser length by variant parity

# Built-in SFX (sfx/<sound>-<name>.wav), in variant order per sound. Every file here must be listed in SOURCES-CC0.txt.
SFX_FILES: dict[str, tuple[str, ...]] = {
    "whoosh": ("air-bright", "slide-in", "slide-long", "short-punch", "electronic", "swoosh-soft"),
    "impact": ("tight-sub", "cinematic-clean", "crack-decay", "metal-body", "dense-short", "sharp-hit", "punch-sub"),
    "pop": ("classic", "plop", "mouth", "blop", "tight", "bubble-soft"),   # variant 1 = the emoji-burst pop
    "riser": ("1s5-reverse-crash", "2s5-noise-uplift", "1s5-reverse-crash-low", "2s5-reverse-crash", "1s5-synth",
              "2s5-sweep", "1s5-noise-uplift", "2s5-dark"),
    "record_scratch": ("classic", "dj-cut", "quick", "back-forth", "needle-rip", "stab"),
    "boom": ("cinematic", "soft-cinematic", "wide", "short", "deep", "sub-crack"),
    "cash_register": ("classic", "ding", "bell", "chching", "designed"),
    "ticker": ("stopwatch", "timer", "tictoc", "roll-12", "roll-16", "roll-20"),
    "slap": ("crisp", "face", "cheek", "hand", "loud", "wet"),
    "stamp": ("rubber", "rubber-2", "rubber-3", "heavy", "self-inking", "desk"),
    "glitch": ("burst", "pitch-stutter", "designed-stutter", "tech", "short", "hologram"),
    "tape_stop": ("classic", "vinyl-dark", "record", "power-down", "sweep-down", "synth-stab"),
}
_SOURCE_ROW = re.compile(r"^(sfx/[A-Za-z0-9_.-]+\.wav) \| (https://freesound\.org/s/\d+/) \| ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as fh:
        if fh.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        frames = fh.readframes(fh.getnframes())
        rate = fh.getframerate()
        channels = fh.getnchannels()
    data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32767.0
    return data.reshape(-1, channels), rate


def level_db(value: float) -> float:
    return round(20.0 * math.log10(max(value, 1e-9)), 2)


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def measure_lufs(path: Path) -> float:
    """Integrated loudness (ffmpeg ebur128) of a music bed."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    matches = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    if not matches:
        raise RuntimeError(f"no loudness for {path}")
    return float(matches[-1])


def sfx_sources(assets: Path) -> dict[str, str]:
    """``sfx/<file>.wav`` -> Freesound URL, from the provenance table in SOURCES-CC0.txt."""
    rows = {}
    for line in (assets / SFX_SOURCES).read_text(encoding="utf-8").splitlines():
        match = _SOURCE_ROW.match(line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def sfx_entries(assets: Path) -> list[dict]:
    sources = sfx_sources(assets)
    entries = []
    for sound, names in SFX_FILES.items():
        for variant, name in enumerate(names):
            file = f"sfx/{sound}-{name}.wav"
            if file not in sources:
                raise ValueError(f"{file} is not listed in {SFX_SOURCES}")
            path = assets / file
            samples, rate = read_wav(path)
            if rate != SR:
                raise ValueError(f"{path}: {rate} Hz, expected {SR}")
            duration = round(len(samples) / rate, 4)
            if sound == "riser" and abs(duration - RISER_SECONDS[variant % 2]) > 1e-3:
                raise ValueError(f"{file}: {duration} s, riser variant {variant} must be {RISER_SECONDS[variant % 2]} s")
            entries.append({
                "id": f"{sound}-{name}",
                "sound": sound,
                "variant": variant,
                "file": file,
                "sha256": sha256_file(path),
                "duration": duration,
                "peak_db": level_db(float(np.max(np.abs(samples)))),
                "rms_db": round(level_db(float(np.sqrt(np.mean(samples**2)))), 1),
                "gain_db": 0.0,
                "source": sources[file],
                "license": "CC0-1.0",
            })
    return entries


def music_entries(assets: Path) -> list[dict]:
    """The curated CC0 beds listed in music/beds.json (written by import_music.py), in mood then id order."""
    entries = []
    for bed in json.loads((assets / "music" / "beds.json").read_text(encoding="utf-8")):
        path = assets / bed["file"]
        entries.append({
            "id": bed["id"],
            "mood": bed["mood"],
            "file": bed["file"],
            "sha256": sha256_file(path),
            "duration": round(probe_duration(path), 4),
            "loop_start": bed["loop_start"],
            "loop_seconds": bed["loop_seconds"],
            "bpm": bed["bpm"],
            "lufs": round(measure_lufs(path), 1),
            "source": bed["url"],
            "license": bed["license"],
        })
    return entries


def emoji_entry(assets: Path) -> dict:
    atlas = assets / "emoji" / "atlas.png"
    mapping = assets / "emoji" / "atlas.json"
    data = json.loads(mapping.read_text(encoding="utf-8"))
    font = assets / "emoji" / "font.txt"
    source = font.read_text(encoding="utf-8").strip() if font.exists() else "Noto Color Emoji"
    if not re.match(r"^Noto Color Emoji", source):
        raise ValueError(f"unexpected emoji font label {source!r}")
    return {
        "atlas": "emoji/atlas.png",
        "map": "emoji/atlas.json",
        "sha256": {"atlas": sha256_file(atlas), "map": sha256_file(mapping)},
        "cell": int(data["cell"]),
        "count": len(data["glyphs"]),
        "source": source,
        "license": "OFL-1.1",
    }


def build_manifest(assets: Path) -> dict:
    return {
        "version": MANIFEST_VERSION,
        "sfx": sfx_entries(assets),
        "music": music_entries(assets),
        "emoji": emoji_entry(assets),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    args = parser.parse_args(argv)
    manifest = build_manifest(args.assets)
    out = args.assets / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{out}: {len(manifest['sfx'])} sfx, {len(manifest['music'])} beds, {manifest['emoji']['count']} emoji")
    return 0


if __name__ == "__main__":
    sys.exit(main())
