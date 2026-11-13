"""edit_fx.assets: the built-in index (resolver input) and the mix-time drop-in library (§12, AI-H8, AI-L5)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.edit_fx import assets
from src.edit_fx.contracts import MOODS, SOUND_IDS

BUILTIN = assets.builtin_dir()
MANIFEST = json.loads((BUILTIN / "manifest.json").read_text(encoding="utf-8"))
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _tone(path: Path, *, peak_db: float = -20.0, seconds: float = 0.3, freq: float = 440.0, channels: int = 1):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(48_000 * seconds)) / 48_000
    x = np.sin(2 * np.pi * freq * t) * 10 ** (peak_db / 20)
    pcm = np.round(np.repeat(x[:, None], channels, axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(channels)
        fh.setsampwidth(2)
        fh.setframerate(48_000)
        fh.writeframes(pcm.tobytes())
    return path


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "fx_library"
    (root / "sfx").mkdir(parents=True)
    (root / "music").mkdir(parents=True)
    monkeypatch.setenv("FX_LIBRARY_DIR", str(root))
    assets.load_library(refresh=True)
    yield root
    assets.load_library(refresh=True)


@pytest.fixture
def empty_library(tmp_path, monkeypatch):
    monkeypatch.setenv("FX_LIBRARY_DIR", str(tmp_path / "does-not-exist"))
    assets.load_library(refresh=True)


def _no_subprocess(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("load_index must not start a subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)


# ----------------------------------------------------------------------------- built-in index


def test_index_mirrors_the_builtin_manifest(empty_library):
    index = assets.load_index(refresh=True)
    expected: dict[str, int] = {}
    for entry in MANIFEST["sfx"]:
        expected[entry["sound"]] = expected.get(entry["sound"], 0) + 1
    assert assets.sfx_variants() == expected
    assert set(index.sfx) == set(SOUND_IDS)
    for sound, files in index.sfx.items():
        assert [p.name for p in files] == [f"{sound}_{i}.wav" for i in range(len(files))]
        assert all(p.is_file() for p in files)
    assert {mood: ids for mood, ids in index.beds.items()} == {m: (m,) for m in MOODS}
    for entry in MANIFEST["music"]:
        assert index.bed_lengths[entry["id"]] == entry["loop_seconds"]
        assert index.bed_files[entry["id"]] == BUILTIN / entry["file"]
    assert index.atlas_png == BUILTIN / "emoji" / "atlas.png" and index.atlas_json.is_file()
    assert "🔥" in index.emoji_keys and len(index.emoji_keys) == MANIFEST["emoji"]["count"]


def test_public_index_manifest_has_no_paths_and_content_addressed_urls(empty_library):
    public = assets.load_index(refresh=True).manifest
    blob = json.dumps(public, ensure_ascii=False)
    assert "/fx_assets" not in blob and '"file"' not in blob and "sha256" not in blob
    url = re.compile(r"^/fx/assets/[0-9a-f]{64}\.(wav|m4a|png|json)$")
    assert len(public["sfx"]) == len(MANIFEST["sfx"]) and len(public["music"]) == len(MANIFEST["music"])
    for entry in public["sfx"]:
        assert set(entry) == {"id", "sound", "variant", "url", "duration", "gain_db"} and url.match(entry["url"])
    for entry in public["music"]:
        assert set(entry) == {"id", "mood", "url", "duration", "loop_seconds"} and url.match(entry["url"])
    assert set(public["emoji"]) == {"atlas", "map", "cell"}
    assert url.match(public["emoji"]["atlas"]) and url.match(public["emoji"]["map"])


def test_load_index_is_identical_with_drop_ins_and_runs_no_subprocess(library, monkeypatch):
    _no_subprocess(monkeypatch)
    monkeypatch.setenv("FX_LIBRARY_DIR", str(library.parent / "nothing-here"))
    without = assets.load_index(refresh=True)

    monkeypatch.setenv("FX_LIBRARY_DIR", str(library))
    _tone(library / "sfx" / "whoosh-mine.wav")
    _tone(library / "sfx" / "boom.wav")
    _tone(library / "music" / "hype-mytrack.wav", seconds=1.0, channels=2)
    with_drop_ins = assets.load_index(refresh=True)

    assert with_drop_ins == without
    assert with_drop_ins.manifest == without.manifest
    assert assets.sfx_variants() == {s: len(f) for s, f in without.sfx.items()}


def test_load_index_is_cached(empty_library):
    assert assets.load_index() is assets.load_index()
    first = assets.load_index()
    assert assets.load_index(refresh=True) is not first


# ----------------------------------------------------------------------------- mix-time SFX


def test_builtin_sfx_variant_and_missing_variant_falls_back_to_zero(empty_library):
    path, gain = assets.mix_sfx_file("whoosh", 2, seed=7)
    assert path == BUILTIN / "sfx" / "whoosh_2.wav" and gain == 0.0
    assert assets.mix_sfx_file("pop", 9, seed=1)[0] == BUILTIN / "sfx" / "pop_0.wav"
    assert assets.mix_sfx_file("boom", 1, seed=1)[0] == BUILTIN / "sfx" / "boom_0.wav"
    assert assets.mix_sfx_file("slap", -1, seed=1)[0] == BUILTIN / "sfx" / "slap_0.wav"


def test_unknown_sound_raises(empty_library):
    with pytest.raises(KeyError):
        assets.mix_sfx_file("kazoo", 0, seed=0)


@needs_ffmpeg
def test_drop_in_replaces_the_builtin_sound(library):
    mine = _tone(library / "sfx" / "whoosh-mine.wav", peak_db=-20.0)
    for seed in (0, 1, 2, 41, 123456789):
        path, gain = assets.mix_sfx_file("whoosh", 0, seed)
        assert path == mine
        assert gain == pytest.approx(14.0, abs=0.3)  # normalised to the built-ins' -6 dBFS peak
    # other sounds keep their built-ins
    assert assets.mix_sfx_file("pop", 1, seed=3)[0] == BUILTIN / "sfx" / "pop_1.wav"


@needs_ffmpeg
def test_several_drop_ins_are_picked_by_seed_in_name_order(library, monkeypatch):
    monkeypatch.setattr(assets, "drop_in_gain_db", lambda _f: 0.0)
    a = _tone(library / "sfx" / "impact-a.wav")
    b = _tone(library / "sfx" / "impact-b.ogg")
    c = _tone(library / "sfx" / "impact.mp3")  # bare sound name is a valid drop-in too
    order = sorted([a, b, c], key=lambda p: p.name)
    for seed in range(7):
        assert assets.mix_sfx_file("impact", 1, seed)[0] == order[seed % 3]


def test_invalid_drop_in_names_are_ignored(library):
    _tone(library / "sfx" / "kazoo-honk.wav")       # not a SoundId
    _tone(library / "sfx" / "whoosh-x.flac")        # unsupported extension
    _tone(library / "sfx" / ".whoosh-hidden.wav")   # hidden
    (library / "sfx" / "README.txt").write_text("notes")
    _tone(library / "music" / "hype.wav")           # a bed needs <mood>-<name>
    _tone(library / "music" / "lofi-track.wav")     # not a mood
    _tone(library / "music" / "chill-x.ogg")        # ogg is SFX-only
    lib = assets.load_library(refresh=True)
    assert lib.files == () and lib.fingerprint == ""
    assert assets.mix_sfx_file("whoosh", 1, seed=0)[0] == BUILTIN / "sfx" / "whoosh_1.wav"


# ----------------------------------------------------------------------------- mix-time beds


def test_beds_without_drop_ins(empty_library):
    assert assets.mix_bed_file("chill", "chill", seed=4) == (BUILTIN / "music" / "chill.m4a", 0.0)
    # a missing (custom) bed falls back to the built-in of that mood
    assert assets.mix_bed_file("dark-gone", "dark", seed=4)[0] == BUILTIN / "music" / "dark.m4a"
    # a built-in id is honoured even if the mood says otherwise
    assert assets.mix_bed_file("funny", "hype", seed=0)[0] == BUILTIN / "music" / "funny.m4a"


@needs_ffmpeg
def test_bed_drop_ins(library, monkeypatch):
    monkeypatch.setattr(assets, "drop_in_gain_db", lambda _f: -3.0)
    one = _tone(library / "music" / "hype-one.wav", seconds=1.0, channels=2)
    two = _tone(library / "music" / "hype-two.mp3", seconds=1.0, channels=2)
    mine = _tone(library / "music" / "dark-mine.wav", seconds=1.0, channels=2)
    # explicit custom id → it, whatever the mood
    assert assets.mix_bed_file("dark-mine", "hype", seed=5) == (mine, -3.0)
    # built-in id + drop-ins of that mood → drop_ins[seed % len]
    assert assets.mix_bed_file("hype", "hype", seed=0)[0] == one
    assert assets.mix_bed_file("hype", "hype", seed=1)[0] == two
    # moods without drop-ins keep their built-in
    assert assets.mix_bed_file("chill", "chill", seed=1)[0] == BUILTIN / "music" / "chill.m4a"
    # a missing custom id of a mood that has drop-ins uses one of them
    assert assets.mix_bed_file("hype-gone", "hype", seed=3)[0] in (one, two)


@needs_ffmpeg
def test_bed_drop_in_gain_matches_builtin_level_with_headroom(library):
    quiet = _tone(library / "music" / "chill-quiet.wav", peak_db=-30.0, seconds=2.0, channels=2)
    loud = _tone(library / "music" / "chill-loud.wav", peak_db=-0.5, seconds=2.0, channels=2)
    lib = assets.load_library(refresh=True)
    gains = {f.path: assets.drop_in_gain_db(f) for f in lib.files}
    # a sine's mean level is peak - 3 dB; the quiet bed is raised to the built-in mean, capped by the -1 dBFS ceiling
    assert gains[quiet] == pytest.approx(min(assets.BED_MEAN_DB - (-33.0), -1.0 - (-30.0)), abs=0.3)
    assert gains[loud] == pytest.approx(assets.BED_MEAN_DB - (-3.5), abs=0.3)


def test_unmeasurable_drop_in_gets_zero_gain(library):
    (library / "sfx" / "pop-broken.wav").write_bytes(b"not audio at all")
    (broken,) = assets.load_library(refresh=True).files
    assert assets.drop_in_gain_db(broken) == 0.0


@needs_ffmpeg
def test_broken_drop_ins_are_skipped_at_mix_time_and_in_the_served_manifest(library):
    (library / "sfx" / "pop-a-broken.wav").write_bytes(b"not audio at all")
    good = _tone(library / "sfx" / "pop-b-good.wav")
    for seed in range(4):
        assert assets.mix_sfx_file("pop", 0, seed)[0] == good
    (library / "sfx" / "pop-b-good.wav").unlink()
    assert assets.mix_sfx_file("pop", 1, seed=0)[0] == BUILTIN / "sfx" / "pop_1.wav"

    (library / "music" / "hype-broken.wav").write_bytes(b"nope")
    assert assets.mix_bed_file("hype-broken", "hype", seed=0)[0] == BUILTIN / "music" / "hype.m4a"
    served = assets.public_manifest()
    assert not [e for e in served["sfx"] + served["music"] if e.get("custom")]


# ----------------------------------------------------------------------------- fingerprint


def test_fingerprint_empty_without_drop_ins(empty_library):
    assert assets.library_fingerprint() == ""


def test_fingerprint_changes_when_a_drop_in_changes(library):
    assert assets.library_fingerprint() == ""
    target = _tone(library / "sfx" / "whoosh-mine.wav", freq=440.0)
    first = assets.library_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert assets.library_fingerprint() == first  # stable

    _tone(target, freq=880.0, seconds=0.31)  # same name, new content
    second = assets.library_fingerprint()
    assert second not in ("", first)

    _tone(library / "music" / "hype-new.wav", seconds=0.5)
    third = assets.library_fingerprint()
    assert third not in ("", first, second)

    target.unlink()
    (library / "music" / "hype-new.wav").unlink()
    assert assets.library_fingerprint() == ""


# ----------------------------------------------------------------------------- serving helpers (WP13)


@needs_ffmpeg
def test_public_manifest_adds_drop_ins_flagged_custom(library):
    _tone(library / "sfx" / "whoosh-b.wav")
    _tone(library / "sfx" / "whoosh-a.wav", peak_db=-12.0)
    _tone(library / "music" / "funny-kazoo.wav", seconds=1.5, channels=2)
    served = assets.public_manifest()
    custom_sfx = [e for e in served["sfx"] if e.get("custom")]
    assert [e["id"] for e in custom_sfx] == ["whoosh-a", "whoosh-b"]
    assert [e["variant"] for e in custom_sfx] == [0, 1]
    assert custom_sfx[0]["gain_db"] == pytest.approx(6.0, abs=0.3)
    assert custom_sfx[0]["duration"] == pytest.approx(0.3, abs=0.02)
    (bed,) = [e for e in served["music"] if e.get("custom")]
    assert bed["id"] == "funny-kazoo" and bed["mood"] == "funny" and bed["loop_seconds"] == pytest.approx(1.5, abs=0.02)
    assert "/fx_library" not in json.dumps(served)
    # built-ins are unchanged and the built-in index view never includes drop-ins
    assert [e for e in served["sfx"] if not e.get("custom")] == assets.load_index().manifest["sfx"]
    assert assets.public_manifest(include_library=False) == assets.load_index().manifest


def test_asset_file_serves_only_known_hashes(library):
    entry = MANIFEST["sfx"][0]
    assert assets.asset_file(entry["sha256"], "wav") == BUILTIN / entry["file"]
    assert assets.asset_file(entry["sha256"].upper(), "WAV") == BUILTIN / entry["file"]
    assert assets.asset_file(MANIFEST["emoji"]["sha256"]["atlas"], "png") == BUILTIN / "emoji" / "atlas.png"
    assert assets.asset_file(entry["sha256"], "m4a") is None          # wrong extension
    assert assets.asset_file("0" * 64, "wav") is None                  # unknown
    assert assets.asset_file("../../etc/passwd", "wav") is None        # not a hash
    assert assets.asset_file(entry["sha256"], "py") is None
    mine = _tone(library / "sfx" / "pop-mine.wav")
    sha = assets.load_library().files[0].sha256
    assert assets.asset_file(sha, "wav") == mine


def test_canonical_emoji_tolerates_variation_selectors(empty_library):
    keys = assets.load_index().emoji_keys
    assert assets.canonical_emoji("🔥") == "🔥"
    assert assets.canonical_emoji("❤") == "❤️" and "❤️" in keys
    assert assets.canonical_emoji("  ⚡️ ") in ("⚡", "⚡️")
    assert assets.canonical_emoji("") is None and assets.canonical_emoji("abc") is None
    assert assets.canonical_emoji("🔥", keys=frozenset()) is None
    # a selector inside a ZWJ sequence (heart on fire), either way round
    zwj = frozenset({"\u2764\ufe0f\u200d\U0001f525"})
    assert assets.canonical_emoji("\u2764\u200d\U0001f525", keys=zwj) == "\u2764\ufe0f\u200d\U0001f525"
    assert assets.canonical_emoji("\u2764\ufe0f\u200d\U0001f525\ufe0f", keys=zwj) is not None
