"""Dynamic-edit asset registry: built-in SFX, music beds and the emoji atlas, plus self-hoster drop-ins (§12).

The built-in SFX are CC0 recordings (``fx_assets/SOURCES-CC0.txt``), 4-8 takes per sound id, 48 kHz 16-bit WAV at a
-6 dBFS peak. A cue's ``variant`` names one take; the resolver picks it as ``seed % count`` (or 0 for sounds added
by hand), so the same document always plays the same file. Riser takes alternate 1.5 s / 2.5 s by variant parity.

Two views, kept apart on purpose (AI-H8, AP-M7, AI-L5):

* ``load_index()`` is the **built-in** manifest committed in ``src/fx_assets`` (a JSON read, no ffmpeg, no
  subprocess). It is identical on the API, the AI worker and every render worker, so the resolver, which reads only
  this index, turns the same document into the same cues everywhere. Drop-ins never influence a plan.
* ``load_library()`` scans ``FX_LIBRARY_DIR`` for drop-ins (``sfx/<sound>[-<name>].{wav,mp3,m4a,ogg}`` and
  ``music/<mood>-<name>.{m4a,mp3,wav}``). They are applied **at mix time** only, by ``mix_sfx_file`` and
  ``mix_bed_file``: when a sound or mood has drop-ins they replace the built-in files for it, picked by
  ``seed % len`` in file-name order among the drop-ins that decode (a broken file is skipped and logged once, never
  fatal). Riser drop-ins are first narrowed to the ones closest in length to the cue's built-in variant
  (``length_matched``), so a 2.5 s riser stays a 2.5 s riser. Every render machine must mount the same library; renders log
  ``library_fingerprint()`` so a mismatch is visible.

Drop-ins are measured lazily, once per file content per process (ffmpeg ``volumedetect``; call the measuring
functions from a thread, e.g. ``run_in_thread``), and gain-normalised: SFX to the built-ins' -6 dBFS peak, beds to the
built-ins' mean level with at least 1 dB of headroom.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .contracts import MOODS, SOUND_IDS

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
SFX_PEAK_DB = -6.0                # built-in SFX are peak-normalised here; drop-ins are matched to it
BED_MEAN_DB = -20.3               # volumedetect mean of the built-in beds (~ -20 LUFS); drop-ins are matched to it
BED_PEAK_CEILING_DB = -1.0        # a drop-in bed is never raised above this peak
GAIN_LIMITS = (-30.0, 24.0)
LENGTH_MATCHED_SOUNDS = frozenset({"riser"})   # drop-ins of these are picked by length (the built-in variant's)
LENGTH_MATCH_SLACK = 0.25                      # s: drop-ins this much farther from the length than the closest count too
LIBRARY_SFX_EXTS = (".wav", ".mp3", ".m4a", ".ogg")
LIBRARY_MUSIC_EXTS = (".m4a", ".mp3", ".wav")
SERVED_EXTS = frozenset({"wav", "mp3", "m4a", "ogg", "png", "json"})
MEASURE_TIMEOUT_SECONDS = 60
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def builtin_dir() -> Path:
    """backend/src/fx_assets (``/app/src/fx_assets`` in the image)."""
    return Path(__file__).resolve().parent.parent / "fx_assets"


# ----------------------------------------------------------------------------- built-in index


@dataclass(frozen=True)
class AssetIndex:
    sfx: Mapping[str, tuple[Path, ...]]        # sound id → built-in files, in variant order
    beds: Mapping[str, tuple[str, ...]]        # mood → built-in bed ids
    bed_files: Mapping[str, Path]
    bed_lengths: Mapping[str, float]
    emoji_keys: frozenset[str]
    atlas_png: Path
    atlas_json: Path
    manifest: dict                             # public manifest (no paths) for GET /fx/assets
    sfx_gain_db: Mapping[Path, float] = field(default_factory=dict)   # built-in file → manifest gain_db
    sfx_duration: Mapping[Path, float] = field(default_factory=dict)  # built-in file → manifest duration (s)
    files_by_sha: Mapping[str, Path] = field(default_factory=dict)    # built-in sha256 → file (served URLs)
    bed_starts: Mapping[str, float] = field(default_factory=dict)     # bed id → seconds of padding before its loop


_index_lock = threading.Lock()
_index: AssetIndex | None = None


def asset_url(sha256: str, ext: str) -> str:
    """Content-addressed, immutable URL served by ``GET /fx/assets/{sha256}.{ext}`` (§5.6)."""
    return f"/fx/assets/{sha256}.{ext}"


def _ext(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def _build_index(root: Path) -> AssetIndex:
    raw = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    if raw.get("version") != 1:
        raise ValueError(f"unsupported fx asset manifest version {raw.get('version')!r}")

    sfx: dict[str, list[tuple[int, Path]]] = {}
    gains: dict[Path, float] = {}
    durations: dict[Path, float] = {}
    by_sha: dict[str, Path] = {}
    public_sfx = []
    for entry in raw["sfx"]:
        path = root / entry["file"]
        sfx.setdefault(entry["sound"], []).append((int(entry["variant"]), path))
        gains[path] = float(entry.get("gain_db", 0.0))
        durations[path] = float(entry["duration"])
        by_sha[entry["sha256"]] = path
        public_sfx.append({
            "id": entry["id"], "sound": entry["sound"], "variant": int(entry["variant"]),
            "url": asset_url(entry["sha256"], _ext(entry["file"])), "duration": entry["duration"],
            "gain_db": float(entry.get("gain_db", 0.0)),
        })

    beds: dict[str, list[str]] = {}
    bed_files: dict[str, Path] = {}
    bed_lengths: dict[str, float] = {}
    bed_starts: dict[str, float] = {}
    public_music = []
    for entry in raw["music"]:
        path = root / entry["file"]
        beds.setdefault(entry["mood"], []).append(entry["id"])
        bed_files[entry["id"]] = path
        bed_lengths[entry["id"]] = float(entry["loop_seconds"])
        bed_starts[entry["id"]] = float(entry.get("loop_start", 0.0))
        by_sha[entry["sha256"]] = path
        public_music.append({
            "id": entry["id"], "mood": entry["mood"], "url": asset_url(entry["sha256"], _ext(entry["file"])),
            "duration": entry["duration"], "loop_start": float(entry.get("loop_start", 0.0)),
            "loop_seconds": entry["loop_seconds"],
        })

    emoji = raw["emoji"]
    atlas_png, atlas_json = root / emoji["atlas"], root / emoji["map"]
    by_sha[emoji["sha256"]["atlas"]] = atlas_png
    by_sha[emoji["sha256"]["map"]] = atlas_json
    glyphs = json.loads(atlas_json.read_text(encoding="utf-8"))["glyphs"]

    public = {
        "version": raw["version"],
        "sfx": public_sfx,
        "music": public_music,
        "emoji": {
            "atlas": asset_url(emoji["sha256"]["atlas"], _ext(emoji["atlas"])),
            "map": asset_url(emoji["sha256"]["map"], _ext(emoji["map"])),
            "cell": int(emoji["cell"]),
        },
    }
    return AssetIndex(
        sfx=MappingProxyType({k: tuple(p for _v, p in sorted(v)) for k, v in sfx.items()}),
        beds=MappingProxyType({k: tuple(v) for k, v in beds.items()}),
        bed_files=MappingProxyType(bed_files),
        bed_lengths=MappingProxyType(bed_lengths),
        bed_starts=MappingProxyType(bed_starts),
        emoji_keys=frozenset(glyphs),
        atlas_png=atlas_png,
        atlas_json=atlas_json,
        manifest=public,
        sfx_gain_db=MappingProxyType(gains),
        sfx_duration=MappingProxyType(durations),
        files_by_sha=MappingProxyType(by_sha),
    )


def load_index(refresh: bool = False) -> AssetIndex:
    """The BUILT-IN manifest only: a JSON read, no ffmpeg, no subprocess, no drop-ins (AI-L5). Cached per process."""
    global _index
    with _index_lock:
        if _index is None or refresh:
            _index = _build_index(builtin_dir())
        return _index


def sfx_variants() -> dict[str, int]:
    """Built-in file count per sound id (the resolver's ``ResolveInput.sfx_variants``)."""
    return {sound: len(files) for sound, files in load_index().sfx.items()}


_VS16 = "\ufe0f"  # emoji presentation selector


def canonical_emoji(emoji: str, keys: frozenset[str] | None = None) -> str | None:
    """The atlas key for `emoji`, tolerating NFC differences and missing/extra U+FE0F (also inside ZWJ sequences);
    None when not in the atlas."""
    keys = load_index().emoji_keys if keys is None else keys
    text = unicodedata.normalize("NFC", (emoji or "").strip())
    if not text:
        return None
    if text in keys:
        return text
    bare = text.replace(_VS16, "")
    matches = sorted(k for k in keys if k.replace(_VS16, "") == bare)
    return matches[0] if matches else None


# ----------------------------------------------------------------------------- drop-in library


@dataclass(frozen=True)
class LibraryFile:
    id: str              # file stem: "whoosh-mine", "hype-mytrack"
    kind: str            # "sfx" | "music"
    key: str             # sound id (sfx) or mood (music)
    name: str            # "sfx/whoosh-mine.wav": relative to the library root
    path: Path
    sha256: str
    ext: str


@dataclass(frozen=True)
class AssetLibrary:
    root: Path
    sfx: Mapping[str, tuple[LibraryFile, ...]]      # sound id → drop-ins, file-name order (the mix order)
    music: Mapping[str, tuple[LibraryFile, ...]]    # mood → drop-ins, file-name order
    by_id: Mapping[str, LibraryFile]                # music bed id → drop-in ("hype-mytrack")
    files: tuple[LibraryFile, ...]
    fingerprint: str                                # library_fingerprint(); "" when empty


@dataclass(frozen=True)
class Measurement:
    duration: float
    mean_db: float
    max_db: float


_library_lock = threading.Lock()
_hash_cache: dict[tuple[str, int, int], str] = {}
_measure_cache: dict[str, Measurement | None] = {}
_warned: set[tuple[str, str]] = set()


def _warn_once(event: str, name: str) -> None:
    if (event, name) not in _warned:
        _warned.add((event, name))
        logger.warning("%s file=%s", event, name)


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _hash_cache.get(key)
    if cached:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    _hash_cache[key] = value
    return value


def _library_root() -> Path:
    from .env import fx_library_dir

    return fx_library_dir()


def _parse_drop_in(kind: str, path: Path) -> tuple[str, str] | None:
    """(id, key) for a valid drop-in name, None (logged) otherwise."""
    stem = path.stem
    if not _NAME.match(stem):
        return None
    if kind == "sfx":
        if path.suffix.lower() not in LIBRARY_SFX_EXTS:
            return None
        sound = stem.split("-", 1)[0]
        return (stem, sound) if sound in SOUND_IDS else None
    if path.suffix.lower() not in LIBRARY_MUSIC_EXTS:
        return None
    mood, _, name = stem.partition("-")
    return (stem, mood) if mood in MOODS and name else None


def _scan(root: Path) -> AssetLibrary:
    files: list[LibraryFile] = []
    for kind in ("sfx", "music"):
        folder = root / kind
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for path in entries:
            if path.name.startswith(".") or not path.is_file():
                continue
            parsed = _parse_drop_in(kind, path)
            if parsed is None:
                if path.suffix.lower() not in (".txt", ".md"):
                    _warn_once("fx_library_ignored_file", f"{kind}/{path.name}")
                continue
            try:
                sha = _sha256(path)
            except OSError:
                _warn_once("fx_library_unreadable_file", f"{kind}/{path.name}")
                continue
            files.append(LibraryFile(id=parsed[0], kind=kind, key=parsed[1], name=f"{kind}/{path.name}",
                                     path=path, sha256=sha, ext=_ext(path)))

    sfx: dict[str, list[LibraryFile]] = {}
    music: dict[str, list[LibraryFile]] = {}
    by_id: dict[str, LibraryFile] = {}
    for f in files:
        if f.kind == "sfx":
            sfx.setdefault(f.key, []).append(f)
        elif f.id not in by_id:  # two formats of the same bed name: the first in name order wins
            music.setdefault(f.key, []).append(f)
            by_id[f.id] = f
    fingerprint = ""
    if files:
        blob = "\n".join(f"{f.name}\t{f.sha256}" for f in sorted(files, key=lambda f: f.name))
        fingerprint = hashlib.sha256(blob.encode()).hexdigest()
    return AssetLibrary(
        root=root,
        sfx=MappingProxyType({k: tuple(v) for k, v in sfx.items()}),
        music=MappingProxyType({k: tuple(v) for k, v in music.items()}),
        by_id=MappingProxyType(by_id),
        files=tuple(files),
        fingerprint=fingerprint,
    )


def load_library(refresh: bool = False) -> AssetLibrary:
    """Drop-ins under FX_LIBRARY_DIR. The directory is re-listed on every call (a few stats), so added or changed
    files are picked up; content hashes and measurements are cached per (path, size, mtime) / sha per process.
    ``refresh`` drops those caches too. A missing directory is an empty library."""
    with _library_lock:
        if refresh:
            _hash_cache.clear()
            _measure_cache.clear()
            _warned.clear()
        return _scan(_library_root())


def library_fingerprint() -> str:
    """sha256 over the sorted drop-in (name, sha256) pairs; "" when there are none."""
    return load_library().fingerprint


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_MEAN = re.compile(r"mean_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB")
_MAX = re.compile(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB")


def measure(file: LibraryFile) -> Measurement | None:
    """ffmpeg volumedetect (one decode) → duration, mean and peak dB; cached per content sha. Blocking: call it
    from a worker thread. None when the file cannot be decoded or is silent."""
    if file.sha256 in _measure_cache:
        return _measure_cache[file.sha256]
    result: Measurement | None = None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(file.path), "-map", "0:a:0", "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=MEASURE_TIMEOUT_SECONDS,
        )
        duration, mean, peak = _DURATION.search(proc.stderr), _MEAN.search(proc.stderr), _MAX.search(proc.stderr)
        if proc.returncode == 0 and duration and mean and peak and "inf" not in (mean.group(1) + peak.group(1)):
            h, m, s = duration.groups()
            result = Measurement(int(h) * 3600 + int(m) * 60 + float(s), float(mean.group(1)), float(peak.group(1)))
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is None:
        _warn_once("fx_library_unmeasurable", file.name)
    _measure_cache[file.sha256] = result
    return result


def _clamp_gain(value: float) -> float:
    return round(max(GAIN_LIMITS[0], min(GAIN_LIMITS[1], value)), 2)


def drop_in_gain_db(file: LibraryFile) -> float:
    """Normalisation gain for a drop-in: SFX peak → -6 dBFS; beds mean → the built-in level, peak ≤ -1 dBFS.
    0 dB when the file cannot be measured."""
    m = measure(file)
    if m is None:
        return 0.0
    if file.kind == "sfx":
        return _clamp_gain(SFX_PEAK_DB - m.max_db)
    return _clamp_gain(min(BED_MEAN_DB - m.mean_db, BED_PEAK_CEILING_DB - m.max_db))


# ----------------------------------------------------------------------------- mix time


def usable_drop_ins(drops: tuple[LibraryFile, ...] | None) -> tuple[LibraryFile, ...]:
    """Drop-ins that decode (measured, not silent), in mix order. A broken file in the library is skipped (and
    logged once) instead of failing every render that picks it; ``public_manifest`` lists the same set, so the
    preview's ``custom[seed % len]`` picks the same file. Blocking (ffmpeg on first sight of a file)."""
    return tuple(f for f in (drops or ()) if measure(f) is not None)


def _drop_in_duration(file: LibraryFile) -> float:
    m = measure(file)
    return round(m.duration, 3) if m else 0.0   # the served manifest's rounding, so the client compares the same


def length_matched(drops: tuple[LibraryFile, ...], target: float) -> tuple[LibraryFile, ...]:
    """The drop-ins whose length is closest to `target` seconds (within LENGTH_MATCH_SLACK of the closest), in mix
    order. Never empty when `drops` is not. Mirrored by ``lengthMatched`` in the studio's assets-client.ts."""
    if not drops:
        return drops
    gaps = [abs(_drop_in_duration(f) - target) for f in drops]
    limit = min(gaps) + LENGTH_MATCH_SLACK + 1e-6
    return tuple(f for f, gap in zip(drops, gaps) if gap <= limit)


def mix_sfx_file(sound: str, variant: int, seed: int) -> tuple[Path, float]:
    """(file, normalisation gain dB) for an SfxCue at MIX time. Usable drop-ins for `sound` exist →
    drop_ins[seed % len] (risers: among the drop-ins closest in length to the built-in `variant`); else the built-in
    `variant` (a missing variant → variant 0). Raises KeyError for an unknown sound id."""
    index = load_index()
    files = index.sfx.get(sound)
    builtin = (files[variant] if 0 <= variant < len(files) else files[0]) if files else None
    drops = usable_drop_ins(load_library().sfx.get(sound))
    if drops:
        if sound in LENGTH_MATCHED_SOUNDS and builtin is not None:
            drops = length_matched(drops, index.sfx_duration[builtin])
        chosen = drops[seed % len(drops)]
        return chosen.path, drop_in_gain_db(chosen)
    if builtin is None:
        raise KeyError(f"unknown sfx sound {sound!r}")
    return builtin, index.sfx_gain_db.get(builtin, 0.0)


def mix_bed_file(bed_id: str, mood: str, seed: int) -> tuple[Path, float]:
    """(file, normalisation gain dB) for a MusicBed at MIX time. An explicit drop-in id → it if present; drop-ins of
    `mood` → drop_ins[seed % len]; a built-in id → it; anything else (a missing bed) → the built-in of that mood.
    Drop-ins that do not decode are skipped (``usable_drop_ins``)."""
    library = load_library()
    explicit = library.by_id.get(bed_id)
    if explicit is not None and measure(explicit) is not None:
        return explicit.path, drop_in_gain_db(explicit)
    drops = usable_drop_ins(library.music.get(mood))
    if drops:
        chosen = drops[seed % len(drops)]
        return chosen.path, drop_in_gain_db(chosen)
    index = load_index()
    if bed_id in index.bed_files:
        return index.bed_files[bed_id], 0.0
    fallback = index.beds.get(mood) or next(iter(index.beds.values()))
    return index.bed_files[fallback[0]], 0.0


# ----------------------------------------------------------------------------- serving (GET /fx/assets, WP13)


def public_manifest(include_library: bool = True) -> dict:
    """The served manifest: built-ins plus this machine's drop-ins flagged ``custom: true`` (no paths). Custom
    entries are listed per sound / mood in mix order (usable drop-ins only), so a client mirrors
    ``mix_sfx_file``/``mix_bed_file`` with ``custom[seed % len]`` (risers: after ``length_matched`` on the listed
    durations). Measures unmeasured drop-ins (ffmpeg): call it from a thread."""
    base = load_index().manifest
    manifest = {"version": base["version"], "sfx": list(base["sfx"]), "music": list(base["music"]),
                "emoji": dict(base["emoji"])}
    if not include_library:
        return manifest
    library = load_library()
    for sound, drops in library.sfx.items():
        for variant, f in enumerate(usable_drop_ins(drops)):
            m = measure(f)
            manifest["sfx"].append({
                "id": f.id, "sound": sound, "variant": variant, "url": asset_url(f.sha256, f.ext),
                "duration": round(m.duration, 3) if m else 0.0, "gain_db": drop_in_gain_db(f), "custom": True,
            })
    for mood, drops in library.music.items():
        for f in usable_drop_ins(drops):
            m = measure(f)
            duration = round(m.duration, 3) if m else 0.0
            manifest["music"].append({
                "id": f.id, "mood": mood, "url": asset_url(f.sha256, f.ext), "duration": duration,
                "loop_seconds": duration, "gain_db": drop_in_gain_db(f), "custom": True,
            })
    return manifest


def asset_file(sha256: str, ext: str) -> Path | None:
    """The file behind ``/fx/assets/{sha256}.{ext}``: only hashes in the built-in manifest or this machine's
    library, never a path built from input. None when unknown or the extension does not match."""
    sha256, ext = (sha256 or "").lower(), (ext or "").lower()
    if not _SHA256.match(sha256) or ext not in SERVED_EXTS:
        return None
    path = load_index().files_by_sha.get(sha256)
    if path is None:
        path = next((f.path for f in load_library().files if f.sha256 == sha256), None)
    if path is None or _ext(path) != ext or not path.is_file():
        return None
    return path
