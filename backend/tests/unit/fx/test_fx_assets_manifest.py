"""The committed dynamic-edit assets (backend/src/fx_assets) match their manifest and the §12 quality bar."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from src.edit_fx.constants import LLM_ALLOWED_EMOJI, TONE_EMOJI
from src.edit_fx.contracts import MOODS, SOUND_IDS
from src.emoji_captions import EMOJI_KEYWORD_MAP

BACKEND = Path(__file__).resolve().parents[3]
ASSETS = BACKEND / "src" / "fx_assets"
SCRIPTS = BACKEND / "scripts" / "fx"
MANIFEST = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
SR = 48_000
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _script(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"fx_script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_wav(path: Path) -> tuple[np.ndarray, wave._wave_params]:
    with wave.open(str(path), "rb") as fh:
        params = fh.getparams()
        pcm = np.frombuffer(fh.readframes(params.nframes), dtype="<i2")
    return pcm, params


def _decode(path: Path, channels: int = 2) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", str(channels), "-ar", str(SR), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)


def _rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12)


# ----------------------------------------------------------------------------- manifest shape


def test_manifest_shape_is_the_verbatim_one():
    assert MANIFEST["version"] == 1
    assert set(MANIFEST) == {"version", "sfx", "music", "emoji"}
    sfx_keys = ["id", "sound", "variant", "file", "sha256", "duration", "peak_db", "rms_db", "gain_db", "source",
                "license"]
    music_keys = ["id", "mood", "file", "sha256", "duration", "loop_seconds", "bpm", "lufs", "source", "license"]
    for entry in MANIFEST["sfx"]:
        assert list(entry) == sfx_keys
        assert re.fullmatch(r"https://freesound\.org/s/\d+/", entry["source"]) and entry["license"] == "CC0-1.0"
        assert entry["file"] == f"sfx/{entry['id']}.wav" and entry["id"].startswith(f"{entry['sound']}-")
        assert entry["gain_db"] == 0.0
    for entry in MANIFEST["music"]:
        assert list(entry) == music_keys
        assert entry["source"] == "synth" and entry["license"] == "CC0-1.0"
    assert list(MANIFEST["emoji"]) == ["atlas", "map", "sha256", "cell", "count", "source", "license"]
    assert MANIFEST["emoji"]["license"] == "OFL-1.1"
    assert MANIFEST["emoji"]["source"].startswith("Noto Color Emoji ")


def test_variants_are_contiguous_from_zero():
    by_sound: dict[str, list[int]] = {}
    for entry in MANIFEST["sfx"]:
        by_sound.setdefault(entry["sound"], []).append(entry["variant"])
    for sound, variants in by_sound.items():
        assert sorted(variants) == list(range(len(variants))), sound


def test_manifest_sha256_matches_every_file():
    for entry in (*MANIFEST["sfx"], *MANIFEST["music"]):
        assert _sha(ASSETS / entry["file"]) == entry["sha256"], entry["file"]
    emoji = MANIFEST["emoji"]
    assert _sha(ASSETS / emoji["atlas"]) == emoji["sha256"]["atlas"]
    assert _sha(ASSETS / emoji["map"]) == emoji["sha256"]["map"]


def test_no_unlisted_media_files():
    listed = {e["file"] for e in (*MANIFEST["sfx"], *MANIFEST["music"])}
    on_disk = {f"{p.parent.name}/{p.name}" for p in ASSETS.glob("*/*") if p.suffix in (".wav", ".m4a")}
    assert on_disk == listed


def test_licence_files_exist():
    sources = (ASSETS / "SOURCES.txt").read_text(encoding="utf-8")
    licence = (ASSETS / "LICENSE-noto-emoji.txt").read_text(encoding="utf-8")
    assert "CC0-1.0" in sources and "OFL" in sources and "SOURCES-CC0.txt" in sources
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence


def _cc0_rows() -> dict[str, tuple[str, str]]:
    """SOURCES-CC0.txt provenance table: file -> (Freesound URL, author)."""
    rows = {}
    for line in (ASSETS / "SOURCES-CC0.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("sfx/"):
            file, url, author, title = (part.strip() for part in line.split(" | ", 3))
            assert file not in rows, f"{file} listed twice"
            assert re.fullmatch(r"https://freesound\.org/s/\d+/", url), line
            assert author and title, line
            rows[file] = (url, author)
    return rows


def test_every_sfx_file_has_cc0_provenance():
    text = (ASSETS / "SOURCES-CC0.txt").read_text(encoding="utf-8")
    assert "License: CC0-1.0" in text and "https://creativecommons.org/publicdomain/zero/1.0/" in text
    rows = _cc0_rows()
    on_disk = {f"sfx/{p.name}" for p in (ASSETS / "sfx").iterdir() if not p.name.startswith(".")}
    assert set(rows) == on_disk == {e["file"] for e in MANIFEST["sfx"]}
    for entry in MANIFEST["sfx"]:
        assert entry["source"] == rows[entry["file"]][0], entry["file"]


# ----------------------------------------------------------------------------- SFX


def test_every_sound_id_has_a_file():
    sounds = {e["sound"] for e in MANIFEST["sfx"]}
    assert sounds == set(SOUND_IDS)


def test_manifest_sfx_matches_write_manifest():
    """manifest.json is what write_manifest.py writes for the committed files (order, variants, hashes, levels)."""
    assert _script("write_manifest").sfx_entries(ASSETS) == MANIFEST["sfx"]


def test_riser_length_follows_variant_parity():
    risers = sorted((e for e in MANIFEST["sfx"] if e["sound"] == "riser"), key=lambda e: e["variant"])
    assert len(risers) >= 2
    for entry in risers:
        assert entry["duration"] == (1.5, 2.5)[entry["variant"] % 2], entry["id"]


def test_resolver_fixtures_use_the_manifest_variant_counts():
    from tests.fixtures.fx_resolver.builders import SFX_VARIANTS

    counts: dict[str, int] = {}
    for entry in MANIFEST["sfx"]:
        counts[entry["sound"]] = counts.get(entry["sound"], 0) + 1
    assert SFX_VARIANTS == counts
    assert _script("gen_resolver_goldens").SFX_VARIANTS == counts
    for name in ("talking_head", "story", "no_timing"):
        data = json.loads((BACKEND / "tests" / "fixtures" / "fx_resolver" / f"{name}.input.json").read_text())
        assert data["sfx_variants"] == counts, name


@pytest.mark.parametrize("entry", MANIFEST["sfx"], ids=lambda e: e["id"])
def test_sfx_levels_format_and_dc(entry):
    # 48 kHz 16-bit PCM, mono or stereo as recorded: the mixer reads exactly this without ffmpeg (fx_audio._decode)
    pcm, params = _read_wav(ASSETS / entry["file"])
    assert params.nchannels in (1, 2) and (params.sampwidth, params.framerate) == (2, SR)
    x = pcm.astype(np.float64).reshape(-1, params.nchannels) / 32767.0
    # the -6 dBFS sample peak the mixer's levels (SFX_TRIM_DB, the bus gain) were calibrated against
    peak_db = 20 * np.log10(np.max(np.abs(x)))
    assert -6.2 <= peak_db <= -5.8
    assert abs(entry["peak_db"] - peak_db) < 0.05
    assert float(np.max(np.abs(np.mean(x, axis=0)))) < 1e-3, "DC offset"
    assert len(x) / SR <= 2.5
    assert abs(entry["duration"] - len(x) / SR) < 1e-3
    # edge fades: the first and last samples are (near) silent, so a cut-off head or tail never clicks
    assert np.max(np.abs(x[0])) < 0.01 and np.max(np.abs(x[-1])) < 0.01


def test_synth_fallback_is_deterministic_and_writes_valid_drop_ins(tmp_path):
    from src.edit_fx import assets

    synth = _script("synth_sfx")
    first, second = tmp_path / "a" / "sfx", tmp_path / "b" / "sfx"
    written = synth.synthesise(first)
    synth.synthesise(second)
    assert {p.name for p in written} == {synth.drop_in_name(fid) for fid, *_ in synth.SOUNDS}
    for path in written:
        assert path.read_bytes() == (second / path.name).read_bytes(), path.name
        assert assets._parse_drop_in("sfx", path) is not None, path.name
        pcm, params = _read_wav(path)
        assert params.nframes / SR <= 2.5


# ----------------------------------------------------------------------------- music beds


def test_every_mood_has_a_bed():
    assert {e["mood"] for e in MANIFEST["music"]} == set(MOODS)


def test_loop_length_is_32_bars_at_the_manifest_tempo():
    for entry in MANIFEST["music"]:
        bars = entry["loop_seconds"] * entry["bpm"] / 60 / 4
        assert abs(bars - 32) < 1e-3, entry["id"]
        assert abs(entry["loop_seconds"] - entry["duration"]) <= 0.001, entry["id"]
        assert -21.0 <= entry["lufs"] <= -19.0, entry["id"]


@needs_ffmpeg
@pytest.mark.parametrize("entry", MANIFEST["music"], ids=lambda e: e["id"])
def test_bed_decodes_to_its_loop_length_and_loops_seamlessly(entry):
    x = _decode(ASSETS / entry["file"]).mean(axis=1)
    n = len(x)
    assert abs(n / SR - entry["loop_seconds"]) <= 0.001

    # The seam (end → start) must sound like the same musical moment inside the loop: bar 17 (the mid-loop section
    # turn) has the same pattern, so the RMS just before, across and just after the seam stays within ±1 dB of it.
    looped = np.concatenate([x[-SR:], x[:SR]])
    seam, mid, h = SR, n // 2, SR // 4  # 250 ms windows
    pairs = (
        (looped[seam - h:seam], x[mid - h:mid]),
        (looped[seam - h:seam + h], x[mid - h:mid + h]),
        (looped[seam:seam + h], x[mid:mid + h]),
    )
    for at_seam, inside in pairs:
        assert abs(_rms_db(at_seam) - _rms_db(inside)) <= 1.0
    # no click: the sample step across the seam is no larger than the steps around it
    steps = np.abs(np.diff(looped))
    assert steps[seam - 1] <= np.max(np.delete(steps, seam - 1))


@needs_ffmpeg
def test_bed_peaks_leave_headroom():
    for entry in MANIFEST["music"]:
        x = _decode(ASSETS / entry["file"])
        assert np.max(np.abs(x)) < 10 ** (-1 / 20), entry["id"]


def test_music_render_is_deterministic():
    music = _script("synth_music")
    a = music.render_mood("hype")
    b = music.render_mood("hype")
    assert a.shape == (2, music.loop_samples(124))
    assert np.array_equal(a, b)


# ----------------------------------------------------------------------------- emoji atlas


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_atlas_covers_every_emoji_source():
    atlas = json.loads((ASSETS / "emoji" / "atlas.json").read_text(encoding="utf-8"))
    assert list(atlas) == ["cell", "cols", "glyphs"]
    assert atlas["cell"] == 160 and atlas["cols"] == 16
    required = set(EMOJI_KEYWORD_MAP.values()) | set(TONE_EMOJI.values()) | set(LLM_ALLOWED_EMOJI)
    assert required <= set(atlas["glyphs"])
    assert MANIFEST["emoji"]["count"] == len(atlas["glyphs"])
    assert MANIFEST["emoji"]["cell"] == atlas["cell"]


def test_atlas_cells_are_on_the_grid_and_unique():
    atlas = json.loads((ASSETS / "emoji" / "atlas.json").read_text(encoding="utf-8"))
    width, height = _png_size(ASSETS / "emoji" / "atlas.png")
    cell, cols = atlas["cell"], atlas["cols"]
    assert width == cell * cols
    seen = set()
    for key, pos in atlas["glyphs"].items():
        assert pos["x"] % cell == 0 and pos["y"] % cell == 0, key
        assert 0 <= pos["x"] <= width - cell and 0 <= pos["y"] <= height - cell, key
        seen.add((pos["x"], pos["y"]))
    assert len(seen) == len(atlas["glyphs"])


def test_emoji_list_script_matches_the_atlas_order():
    listed = _script("emoji_list").emoji_set()
    atlas = json.loads((ASSETS / "emoji" / "atlas.json").read_text(encoding="utf-8"))
    assert listed == list(atlas["glyphs"])
    cells = [(p["y"] // 160) * 16 + p["x"] // 160 for p in atlas["glyphs"].values()]
    assert cells == list(range(len(cells)))
