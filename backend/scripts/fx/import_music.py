#!/usr/bin/env python3
"""Import the curated CC0 music library as the built-in beds (backend/src/fx_assets/music/).

The library (built outside the repo, one folder: ``catalog.json`` plus ``masters/<id>.wav``, 48 kHz 16-bit stereo)
cuts every bed as ``[s, e + 2400)``: its last 50 ms is a copy of the audio at ``s``, ready for the mixer's drop-in
crossfade. A built-in bed must instead tile exactly at its loop length (``fx_audio._looped`` seamless mode, the
resolver's ``offset`` and the browser preview all assume it), so the import bakes that crossfade in: the tail is
blended into the head with an equal-power fade and dropped, leaving exactly one musical period. An MP3 smears its
first and last milliseconds, so the file carries 100 ms of the loop's own end before the period and of its start after
it; the manifest's ``loop_start`` says where the period begins, and players tile ``[loop_start, loop_start +
loop_seconds)``, never touching the encoded edges.

Each bed is then set to -20 LUFS integrated (the level the mixer assumes for built-ins, ``assets.BED_MEAN_DB``), and
encoded as LAME MP3 128 kbit/s CBR with its gapless header (ffmpeg decodes it to the exact sample count; AAC
produced glitch bursts on 808-heavy beds). Under speech, 128 kbit/s is indistinguishable from the 320 kbit/s masters.

Writes ``music/<mood>-<name>.mp3``, ``music/beds.json`` (bpm, key, provenance) and ``music/SOURCES-CC0.txt``. Run
``write_manifest.py`` afterwards.

    cd backend && .venv/bin/python scripts/fx/import_music.py /path/to/music-library
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[2]
ASSETS = BACKEND_DIR / "src" / "fx_assets"
SR = 48000
SEAM = 2400                  # 50 ms: the library's seam copy, == fx_audio.BED_SEAM_CROSSFADE at 48 kHz
PAD = 4800                   # 100 ms of the loop on each side of it in the file: the MP3 edges never reach the seam
TARGET_LUFS = -20.0
BITRATE = "128k"


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if handle.getframerate() != SR or handle.getnchannels() != 2 or handle.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 48 kHz 16-bit stereo")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.float64) / 32768.0


def integrated_lufs(path: Path) -> float:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr
    summary = out[out.rindex("Summary:"):]
    return float(summary.split("I:")[1].split("LUFS")[0])


def circular(audio: np.ndarray) -> np.ndarray:
    """One period that tiles exactly: the 50 ms seam copy faded into the head, then dropped."""
    period = audio[:-SEAM].copy()
    tail = audio[-SEAM:]
    theta = np.linspace(0.0, math.pi / 2.0, SEAM)[:, None]
    period[:SEAM] = period[:SEAM] * np.sin(theta) + tail * np.cos(theta)
    return period


def encode(audio: np.ndarray, out: Path, gain_db: float) -> None:
    pcm = np.clip(np.round(audio * 10 ** (gain_db / 20.0) * 32767.0), -32768, 32767).astype("<i2")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", BITRATE, "-map_metadata", "-1", "-fflags", "+bitexact",
                    "-flags:a", "+bitexact", str(out)], input=pcm.tobytes(), check=True)


def decoded_samples(path: Path) -> int:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return len(raw) // 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("library", type=Path)
    parser.add_argument("--assets", type=Path, default=ASSETS)
    args = parser.parse_args(argv)
    catalog = json.loads((args.library / "catalog.json").read_text(encoding="utf-8"))
    music = args.assets / "music"
    music.mkdir(parents=True, exist_ok=True)
    for old in list(music.glob("*.m4a")) + list(music.glob("*.mp3")):
        old.unlink()

    beds = []
    for entry in sorted(catalog, key=lambda e: (e["mood"], e["id"])):
        if entry["license"] != "CC0-1.0":
            raise ValueError(f"{entry['id']}: not CC0")
        period = circular(read_wav(args.library / "masters" / f"{entry['id']}.wav"))
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.wav"
            with wave.open(str(probe), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(SR)
                handle.writeframes(np.clip(np.round(period * 32767.0), -32768, 32767).astype("<i2").tobytes())
            gain = TARGET_LUFS - integrated_lufs(probe)
        out = music / f"{entry['id']}.mp3"
        encode(np.concatenate([period[-PAD:], period, period[:PAD]]), out, gain)
        decoded = decoded_samples(out) - 2 * PAD
        # The gapless header removes the encoder delay; a few padding samples may remain at the end, and the mixer
        # cuts a built-in bed at loop_seconds anyway.
        if not period.shape[0] <= decoded < period.shape[0] + 1152:
            raise RuntimeError(f"{out.name}: decodes to {decoded} samples, expected {period.shape[0]} (gapless header)")
        beds.append({
            "id": entry["id"], "mood": entry["mood"], "file": f"music/{entry['id']}.mp3",
            # 6 decimals, so round(loop_seconds * SR) is the period to the sample (4 would drop up to 2.4 samples)
            "loop_start": round(PAD / SR, 4), "loop_seconds": round(period.shape[0] / SR, 6),
            "bpm": entry["bpm"], "key": entry["key"],
            "title": entry["title"], "author": entry["author"], "url": entry["url"], "source": entry["source"],
            "license": "CC0-1.0", "checked": entry["checked"],
        })
        print(f"{out.name}: {period.shape[0] / SR:.2f} s, gain {gain:+.1f} dB", flush=True)

    (music / "beds.json").write_text(json.dumps(beds, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    lines = ["SupoClip dynamic-edit music beds (music/*.mp3)", "=" * 46, "",
             "License: CC0-1.0 (Creative Commons Zero 1.0 Universal, public domain dedication).",
             "https://creativecommons.org/publicdomain/zero/1.0/", "",
             "Every bed was cut from a track its author released under CC0 1.0. The dedication was read on each",
             "track's own page on the date in beds.json. CC0 needs no attribution; authors are listed for provenance",
             "and thanks. Processing: a whole-bar loop cut from the track body, the seam crossfaded, 20 Hz high-pass,",
             "true-peak limited, set to -20 LUFS, MP3 128 kbit/s. The edits are also released under CC0 1.0.", "",
             "file | source page | author | original title"]
    lines += [f"{b['file']} | {b['url']} | {b['author']} | {b['title']}" for b in beds]
    (music / "SOURCES-CC0.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(beds)} beds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
