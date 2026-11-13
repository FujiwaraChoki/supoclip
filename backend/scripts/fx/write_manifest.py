#!/usr/bin/env python3
"""Write backend/src/fx_assets/manifest.json from the committed asset files (FINAL.md §12).

Per file: sha256 and duration; SFX also peak/RMS dBFS (read with numpy), music beds also the loop length (from the
synthesiser's tempo), BPM and integrated loudness (ffmpeg ebur128). The emoji entry hashes the atlas and its map and
names the font version recorded by build_emoji_atlas.ts. Run after the synth scripts and the atlas build:

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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from synth_music import MOODS, SR, loop_samples, measure_lufs  # noqa: E402
from synth_sfx import SOUNDS  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ASSETS = BACKEND_DIR / "src" / "fx_assets"
MANIFEST_VERSION = 1


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


def sfx_entries(assets: Path) -> list[dict]:
    entries = []
    for file_id, sound, variant, _seed, _build in SOUNDS:
        path = assets / "sfx" / f"{file_id}.wav"
        samples, rate = read_wav(path)
        if rate != SR:
            raise ValueError(f"{path}: {rate} Hz, expected {SR}")
        entries.append({
            "id": file_id,
            "sound": sound,
            "variant": variant,
            "file": f"sfx/{file_id}.wav",
            "sha256": sha256_file(path),
            "duration": round(len(samples) / rate, 4),
            "peak_db": level_db(float(np.max(np.abs(samples)))),
            "rms_db": round(level_db(float(np.sqrt(np.mean(samples**2)))), 1),
            "gain_db": 0.0,
            "source": "synth",
            "license": "CC0-1.0",
        })
    return entries


def music_entries(assets: Path) -> list[dict]:
    entries = []
    for spec in MOODS:
        path = assets / "music" / f"{spec.mood}.m4a"
        entries.append({
            "id": spec.mood,
            "mood": spec.mood,
            "file": f"music/{spec.mood}.m4a",
            "sha256": sha256_file(path),
            "duration": round(probe_duration(path), 4),
            "loop_seconds": round(loop_samples(spec.bpm) / SR, 4),
            "bpm": spec.bpm,
            "lufs": round(measure_lufs(path), 1),
            "source": "synth",
            "license": "CC0-1.0",
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
