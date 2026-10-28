"""Cached YouTube audio and clip-range media.

Analysis uses the full audio track. Rendering uses only the selected time
ranges (padded and merged). Files live under ``youtube-cache/<video id>`` and
are copied to object storage when media storage is S3. Edits reuse that cache
and download a new range only when the cut extends past what is stored.
"""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .config import get_config
from .youtube_proxy import (
    AudioDownloadForbidden,
    ByteBudget,
    DownloadBudgetExceeded,
    PartialDownloadUnavailable,
    YouTubeDownloadError,
    YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY,
    download_audio,
    download_section_file,
    log_youtube_attempt,
    proxy_configured,
    public_download_error,
    redact_secrets,
)

logger = logging.getLogger(__name__)

CACHE_DIRNAME = "youtube-cache"
WORK_DIRNAME = "youtube-work"
MANIFEST_NAME = "manifest.json"
AUDIO_POLICY = "original-v1"


def parse_clock(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return None


def merge_ranges(
    ranges: Iterable[tuple[float, float]],
    *,
    padding: float = 0.0,
    duration: Optional[float] = None,
) -> list[tuple[float, float]]:
    """Pad each range, clamp to the media, and merge overlaps."""
    padded: list[tuple[float, float]] = []
    for start, end in ranges:
        try:
            start_value = float(start)
            end_value = float(end)
        except (TypeError, ValueError):
            continue
        if end_value <= start_value:
            continue
        start_value = max(0.0, start_value - padding)
        end_value = end_value + padding
        if duration is not None and duration > 0:
            end_value = min(end_value, float(duration))
        if end_value > start_value:
            padded.append((start_value, end_value))
    padded.sort()
    merged: list[list[float]] = []
    for start, end in padded:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def missing_intervals(
    cached: Iterable[tuple[float, float]],
    needed: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Portions of ``needed`` that no cached range fully covers."""
    have = sorted((float(start), float(end)) for start, end in cached if end > start)
    gaps: list[tuple[float, float]] = []
    for start, end in needed:
        cursor = float(start)
        limit = float(end)
        if limit <= cursor:
            continue
        for cached_start, cached_end in have:
            if cached_end <= cursor:
                continue
            if cached_start >= limit:
                break
            if cached_start > cursor:
                gaps.append((cursor, min(cached_start, limit)))
            cursor = max(cursor, cached_end)
            if cursor >= limit:
                break
        if cursor < limit - 0.05:
            gaps.append((cursor, limit))
    return merge_ranges(gaps, padding=0.0)


def ranges_from_segments(segments: Iterable[dict]) -> list[tuple[float, float]]:
    collected: list[tuple[float, float]] = []
    for segment in segments:
        start = parse_clock(segment.get("start_time"))
        end = parse_clock(segment.get("end_time"))
        if start is not None and end is not None and end > start:
            collected.append((start, end))
        hook_start = parse_clock(segment.get("hook_start_time"))
        hook_end = parse_clock(segment.get("hook_end_time"))
        if hook_start is not None and hook_end is not None and hook_end > hook_start:
            collected.append((hook_start, hook_end))
        for key in ("keep_ranges", "source_ranges"):
            for item in segment.get(key) or []:
                if isinstance(item, dict):
                    item_start, item_end = item.get("start"), item.get("end")
                else:
                    try:
                        item_start, item_end = item[0], item[1]
                    except (TypeError, IndexError, KeyError):
                        continue
                if item_start is not None and item_end is not None and float(item_end) > float(item_start):
                    collected.append((float(item_start), float(item_end)))
    return collected


@dataclass(frozen=True)
class TimeSpan:
    original_start: float
    original_end: float
    local_start: float

    @property
    def local_end(self) -> float:
        return self.local_start + (self.original_end - self.original_start)


@dataclass(frozen=True)
class TimeMap:
    """Maps original source time onto a concatenated local file."""

    spans: tuple[TimeSpan, ...]

    def contains(self, timestamp: float) -> bool:
        return any(span.original_start <= timestamp <= span.original_end for span in self.spans)

    def to_local(self, timestamp: float) -> float:
        for span in self.spans:
            if span.original_start <= timestamp <= span.original_end:
                return span.local_start + (timestamp - span.original_start)
        if not self.spans:
            return timestamp
        nearest = min(
            self.spans,
            key=lambda span: min(abs(timestamp - span.original_start), abs(timestamp - span.original_end)),
        )
        if timestamp < nearest.original_start:
            return nearest.local_start
        return nearest.local_end

    def to_original(self, timestamp: float) -> float:
        for span in self.spans:
            if span.local_start <= timestamp <= span.local_end:
                return span.original_start + (timestamp - span.local_start)
        if not self.spans:
            return timestamp
        nearest = min(
            self.spans,
            key=lambda span: min(abs(timestamp - span.local_start), abs(timestamp - span.local_end)),
        )
        if timestamp < nearest.local_start:
            return nearest.original_start
        return nearest.original_end

    def to_local_ranges(self, ranges: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        converted = []
        for start, end in ranges:
            local_start = self.to_local(float(start))
            local_end = self.to_local(float(end))
            if local_end > local_start:
                converted.append((local_start, local_end))
        return converted

    def to_original_ranges(self, ranges: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        converted = []
        for start, end in ranges:
            if not self.spans:
                if end > start:
                    converted.append((float(start), float(end)))
                continue
            # A local range may cross a stitched-section boundary. Map each
            # intersection, never the omitted source interval between them.
            for span in self.spans:
                a, b = max(float(start), span.local_start), min(float(end), span.local_end)
                if b > a:
                    converted.append((span.original_start + a - span.local_start,
                                      span.original_start + b - span.local_start))
        return converted


def build_time_map(pieces: Iterable[tuple[float, float]]) -> TimeMap:
    local = 0.0
    spans: list[TimeSpan] = []
    for start, end in pieces:
        start_value, end_value = float(start), float(end)
        if end_value <= start_value:
            continue
        spans.append(TimeSpan(start_value, end_value, local))
        local += end_value - start_value
    return TimeMap(tuple(spans))


def timeline_path(media_path: Path) -> Path:
    return media_path.with_suffix(".timeline.json")


def save_time_map(media_path: Path, time_map: TimeMap) -> None:
    payload = {
        "version": 1,
        "spans": [
            {
                "original_start": span.original_start,
                "original_end": span.original_end,
                "local_start": span.local_start,
            }
            for span in time_map.spans
        ],
    }
    timeline_path(media_path).write_text(json.dumps(payload), encoding="utf-8")


def load_time_map(media_path: Path) -> Optional[TimeMap]:
    path = timeline_path(media_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        spans = tuple(
            TimeSpan(
                original_start=float(item["original_start"]),
                original_end=float(item["original_end"]),
                local_start=float(item["local_start"]),
            )
            for item in payload.get("spans") or []
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        logger.warning("Ignoring unreadable YouTube timeline sidecar %s", path)
        return None
    return TimeMap(spans) if spans else None


def shift_transcript(payload: dict, time_map: TimeMap) -> dict:
    """Copy word timings onto the concatenated file's timeline."""

    def shift_word(word: dict) -> Optional[dict]:
        if not isinstance(word, dict):
            return None
        start = float(word.get("start") or 0)
        end = float(word.get("end") or start)
        midpoint = ((start + end) / 2.0) / 1000.0
        if not time_map.contains(midpoint):
            return None
        copied = dict(word)
        copied["start"] = int(round(time_map.to_local(start / 1000.0) * 1000))
        copied["end"] = int(round(time_map.to_local(end / 1000.0) * 1000))
        return copied

    words = [shifted for word in payload.get("words") or [] if (shifted := shift_word(word))]
    utterances = []
    for utterance in payload.get("utterances") or []:
        if not isinstance(utterance, dict):
            continue
        utterance_words = [
            shifted for word in utterance.get("words") or [] if (shifted := shift_word(word))
        ]
        start = utterance.get("start")
        end = utterance.get("end")
        if start is None or end is None:
            continue
        midpoint = (float(start) + float(end)) / 2.0 / 1000.0
        if not time_map.contains(midpoint) and not utterance_words:
            continue
        copied = dict(utterance)
        copied["start"] = int(round(time_map.to_local(float(start) / 1000.0) * 1000))
        copied["end"] = int(round(time_map.to_local(float(end) / 1000.0) * 1000))
        copied["words"] = utterance_words
        utterances.append(copied)
    shifted_payload = dict(payload)
    shifted_payload["words"] = words
    shifted_payload["utterances"] = utterances
    return shifted_payload


def copy_transcript_sidecar(source: Path, dest: Path, time_map: Optional[TimeMap] = None) -> None:
    cache = source.with_suffix(".transcript_cache.json")
    if not cache.exists():
        return
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if time_map is not None:
        payload = shift_transcript(payload, time_map)
    dest.with_suffix(".transcript_cache.json").write_text(json.dumps(payload), encoding="utf-8")


def localize_segment(segment: dict, time_map: TimeMap) -> dict:
    from .media.timeline import seconds_to_mmss

    local = dict(segment)
    for key in ("start_time", "end_time", "hook_start_time", "hook_end_time"):
        parsed = parse_clock(segment.get(key))
        if parsed is None:
            continue
        local[key] = seconds_to_mmss(time_map.to_local(parsed))
    for key in ("keep_ranges", "source_ranges"):
        ranges = segment.get(key)
        if ranges:
            local[key] = time_map.to_local_ranges(ranges)
    return local


def restore_clip_timeline(clip_info: dict, time_map: Optional[TimeMap], original: dict) -> dict:
    if not clip_info or time_map is None:
        return clip_info
    from .clip_source_map import load_clip_source_ranges, load_clip_output_offsets, save_clip_source_ranges

    path = Path(clip_info["path"])
    local_ranges = load_clip_source_ranges(path)
    if local_ranges:
        original_ranges, original_offsets = [], []
        for local_range, offset in zip(local_ranges, load_clip_output_offsets(path, local_ranges)):
            cursor = offset
            for start, end in time_map.to_original_ranges([local_range]):
                original_ranges.append((start, end))
                original_offsets.append(cursor)
                cursor += end - start
        save_clip_source_ranges(path, original_ranges, original_offsets)
    if original.get("start_time") is not None:
        clip_info["start_time"] = original["start_time"]
    if original.get("end_time") is not None:
        clip_info["end_time"] = original["end_time"]
    if clip_info.get("keep_ranges"):
        clip_info["keep_ranges"] = time_map.to_original_ranges(clip_info["keep_ranges"])
    return clip_info


def local_ranges_for(media_path: Path, ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    time_map = load_time_map(media_path)
    if time_map is None:
        return [(float(start), float(end)) for start, end in ranges]
    return time_map.to_local_ranges(ranges)


@dataclass
class CachedPiece:
    start: float
    end: float
    path: Path


def cache_root(config=None) -> Path:
    runtime = config or get_config()
    return Path(runtime.temp_dir) / CACHE_DIRNAME


def cache_dir_for(video_id: str, config=None) -> Path:
    return cache_root(config) / video_id


def is_preserved_cache_path(path: Optional[Path]) -> bool:
    return path is not None and CACHE_DIRNAME in Path(path).parts


def is_youtube_work_path(path: Optional[Path]) -> bool:
    return path is not None and WORK_DIRNAME in Path(path).parts


def persist_cache_sidecar(media_path: Optional[Path]) -> None:
    """Upload a transcript that sits next to a cached audio or full file."""
    if media_path is None or not is_preserved_cache_path(media_path):
        return
    sidecar = Path(media_path).with_suffix(".transcript_cache.json")
    if sidecar.exists():
        _persist_named(Path(media_path).parent.name, sidecar)


def transcript_source_for(video_id: str, media_path: Path) -> Path:
    """Keep the canonical transcript on the original timeline.

    A per-job render file has word timings shifted onto its local timeline.
    The cached full-length audio still has source timestamps.
    """
    if not video_id or not is_youtube_work_path(media_path):
        return media_path
    directory = cache_dir_for(video_id)
    if not directory.exists():
        return media_path
    audio = _audio_path(directory, _load_manifest(directory, video_id))
    if audio and audio.with_suffix(".transcript_cache.json").exists():
        return audio
    return media_path


@contextmanager
def _cache_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / ".lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _empty_manifest(video_id: str) -> dict:
    return {"version": 1, "video_id": video_id, "audio_policy": AUDIO_POLICY,
            "audio": None, "full": None, "ranges": []}


def _load_manifest(directory: Path, video_id: str) -> dict:
    path = directory / MANIFEST_NAME
    if not path.exists():
        return _empty_manifest(video_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_manifest(video_id)
    payload.setdefault("ranges", [])
    payload.setdefault("audio", None)
    payload.setdefault("full", None)
    if payload.get("audio_policy") != AUDIO_POLICY:
        # Earlier selection could choose an automatic dub. Keep a full source
        # file, but regenerate analysis audio and sections under the new policy.
        payload.update(audio=None, ranges=[], audio_policy=AUDIO_POLICY)
    return payload


def _save_manifest(directory: Path, manifest: dict) -> None:
    target = directory / MANIFEST_NAME
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest), encoding="utf-8")
    temporary.replace(target)


def _range_filename(start: float, end: float) -> str:
    return f"r-{int(round(start * 1000)):010d}-{int(round(end * 1000)):010d}.mp4"


def sweep_expired_cache(config=None) -> None:
    runtime = config or get_config()
    root = cache_root(runtime)
    ttl = int(getattr(runtime, "youtube_source_cache_ttl_seconds", 0) or 0)
    if ttl <= 0 or not root.exists():
        return
    now = time.time()
    for child in root.iterdir():
        manifest = child / MANIFEST_NAME
        if child.is_dir() and manifest.exists() and now - manifest.stat().st_mtime > ttl:
            shutil.rmtree(child, ignore_errors=True)
            logger.info("Expired YouTube source cache %s", child.name)


def _storage():
    from .media_storage import MediaStorage

    storage = MediaStorage()
    if not storage.enabled:
        return None
    return storage


def _persist_named(video_id: str, path: Path) -> None:
    storage = _storage()
    if storage is None or not path.exists():
        return
    try:
        storage.upload_file(path, storage.key(CACHE_DIRNAME, video_id, path.name))
    except Exception as exc:
        logger.warning("Could not store YouTube cache file %s: %s", path.name, redact_secrets(str(exc)))


def _restore_remote_cache(video_id: str, directory: Path) -> None:
    if (directory / MANIFEST_NAME).exists():
        return
    storage = _storage()
    if storage is None:
        return
    manifest_path = directory / MANIFEST_NAME
    try:
        found = storage._download(
            storage.key(CACHE_DIRNAME, video_id, MANIFEST_NAME),
            manifest_path,
            optional=True,
        )
    except Exception as exc:
        logger.warning("Could not restore YouTube cache for %s: %s", video_id, redact_secrets(str(exc)))
        return
    if not found:
        return
    manifest = _load_manifest(directory, video_id)
    names = []
    if manifest.get("audio"):
        names.append(manifest["audio"])
        names.append(str(Path(manifest["audio"]).with_suffix(".transcript_cache.json").name))
    if manifest.get("full"):
        names.append(manifest["full"])
        names.append(str(Path(manifest["full"]).with_suffix(".transcript_cache.json").name))
    for item in manifest.get("ranges") or []:
        if item.get("file"):
            names.append(item["file"])
    for name in names:
        dest = directory / name
        if dest.exists():
            continue
        try:
            storage._download(storage.key(CACHE_DIRNAME, video_id, name), dest, optional=True)
        except Exception as exc:
            logger.warning("Could not restore %s for %s: %s", name, video_id, redact_secrets(str(exc)))


def _pieces(directory: Path, manifest: dict) -> list[CachedPiece]:
    pieces = []
    for item in manifest.get("ranges") or []:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        path = directory / str(item.get("file") or "")
        if end > start and path.is_file() and path.stat().st_size > 0:
            pieces.append(CachedPiece(start, end, path))
    pieces.sort(key=lambda piece: (piece.start, piece.end))
    return pieces


def _full_path(directory: Path, manifest: dict) -> Optional[Path]:
    name = manifest.get("full")
    if not name:
        return None
    path = directory / name
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def _audio_path(directory: Path, manifest: dict) -> Optional[Path]:
    name = manifest.get("audio")
    if not name:
        return None
    path = directory / name
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def adopt_full_download(video_id: str, source: Path, *, task_id: Optional[str] = None) -> Path:
    """Move a full download into the durable cache and return that path."""
    runtime = get_config()
    if not getattr(runtime, "youtube_source_cache", True):
        return source
    directory = cache_dir_for(video_id, runtime)
    with _cache_lock(directory):
        _restore_remote_cache(video_id, directory)
        manifest = _load_manifest(directory, video_id)
        dest = directory / f"full{source.suffix.lower() or '.mp4'}"
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        manifest["full"] = dest.name
        _save_manifest(directory, manifest)
    copy_transcript_sidecar(source, dest)
    if CACHE_DIRNAME not in source.parts and source.exists():
        staging = source.parent
        source.unlink(missing_ok=True)
        transcript = source.with_suffix(".transcript_cache.json")
        if transcript.exists() and transcript.resolve() != dest.with_suffix(".transcript_cache.json").resolve():
            transcript.unlink(missing_ok=True)
        if staging.name.startswith(f"apify-{video_id}-"):
            shutil.rmtree(staging, ignore_errors=True)
    _persist_named(video_id, dest)
    transcript = dest.with_suffix(".transcript_cache.json")
    if transcript.exists():
        _persist_named(video_id, transcript)
    _persist_named(video_id, directory / MANIFEST_NAME)
    log_youtube_attempt(
        provider="cache",
        video_id=video_id,
        kind="full",
        outcome="stored",
        nbytes=dest.stat().st_size,
        task_id=task_id,
    )
    return dest


def _positive_duration(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _full_timeline_via_sections(
    url: str,
    video_id: str,
    duration: Optional[float],
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
) -> Path:
    """Download the whole source with the section path when HLS slicing applies.

    The section downloader prefers timed HLS fragments and refuses an ffmpeg
    network cut. A missing duration, or formats that cannot be sliced, raise
    ``PartialDownloadUnavailable`` so the caller can continue to the paid chain.
    """
    length = _positive_duration(duration)
    if length is None:
        raise PartialDownloadUnavailable("Section fallback needs a known duration")
    from .youtube_proxy import sticky_session_id

    name = sticky_session_id(task_id, video_id)
    work = Path(get_config().temp_dir) / WORK_DIRNAME / f"audio-fallback-{video_id}-{name}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    target = work / "timeline.mp4"
    try:
        path, media_start, _media_end = download_section_file(
            url,
            video_id,
            0.0,
            length,
            target,
            task_id=task_id,
            budget=budget,
            fallback="section",
        )
        if media_start > 1.0:
            raise PartialDownloadUnavailable(
                "Section fallback does not start on the source timeline"
            )
        runtime = get_config()
        if not getattr(runtime, "youtube_source_cache", True):
            kept = Path(runtime.temp_dir) / f"{video_id}-section-fallback{path.suffix or '.mp4'}"
            if kept.exists():
                kept.unlink()
            shutil.move(str(path), kept)
            return kept
        return adopt_full_download(video_id, path, task_id=task_id)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def download_analysis_source(
    url: str,
    video_id: str,
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
    duration: Optional[float] = None,
    download_full: Callable[[], Optional[Path]],
) -> tuple[Optional[Path], bool]:
    """Return ``(path, audio_first)`` for a proxied YouTube job.

    Healthy jobs still take the modest https audio pass. When that pass fails
    with HTTP 403 (the HLS retry lives inside ``download_audio``), the full
    timeline is fetched through the section downloader when a duration is
    known and the formats can be sliced. Anything else uses ``download_full``,
    the existing full-file / API Router / Apify chain.
    """
    try:
        return download_audio_for_job(url, video_id, task_id=task_id, budget=budget), True
    except DownloadBudgetExceeded:
        raise
    except AudioDownloadForbidden as exc:
        if _positive_duration(duration) is None:
            logger.warning(
                "Proxy audio download failed for %s, falling back to a full file: %s",
                video_id,
                public_download_error(exc),
            )
            return download_full(), False
        try:
            return _full_timeline_via_sections(
                url, video_id, duration, task_id=task_id, budget=budget,
            ), False
        except DownloadBudgetExceeded:
            raise
        except Exception as section_exc:
            logger.warning(
                "Proxy audio download failed for %s, falling back to a full file: %s",
                video_id,
                public_download_error(section_exc),
            )
            return download_full(), False
    except Exception as exc:
        logger.warning(
            "Proxy audio download failed for %s, falling back to a full file: %s",
            video_id,
            public_download_error(exc),
        )
        return download_full(), False


def download_audio_for_job(
    url: str,
    video_id: str,
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
) -> Path:
    """Return cached full-duration audio, downloading it through the proxy when needed."""
    runtime = get_config()
    sweep_expired_cache(runtime)
    directory = cache_dir_for(video_id, runtime)
    with _cache_lock(directory):
        _restore_remote_cache(video_id, directory)
        manifest = _load_manifest(directory, video_id)
        existing = _audio_path(directory, manifest)
        if existing:
            log_youtube_attempt(
                provider="cache",
                video_id=video_id,
                kind="audio",
                outcome="cache_hit",
                nbytes=0,
                task_id=task_id,
            )
            return existing
        if not proxy_configured(runtime):
            raise YouTubeDownloadError("Audio-first download requires YOUTUBE_PROXY_URL")
        # Hold the per-video lock through download and manifest publication.
        # Concurrent jobs must not overwrite the same native downloader files.
        downloaded = download_audio(
            url, video_id, directory / f"audio-{AUDIO_POLICY}", task_id=task_id, budget=budget,
        )
        manifest["audio"] = downloaded.name
        _save_manifest(directory, manifest)
    _persist_named(video_id, downloaded)
    _persist_named(video_id, directory / MANIFEST_NAME)
    return downloaded


def _cover_pieces(pieces: list[CachedPiece], start: float, end: float) -> Optional[list[CachedPiece]]:
    """Greedy cover of ``[start, end]`` by cached pieces, in timeline order."""
    chosen: list[CachedPiece] = []
    cursor = start
    while cursor < end - 0.05:
        candidates = [piece for piece in pieces if piece.start <= cursor + 0.25 and piece.end > cursor + 0.05]
        if not candidates:
            return None
        best = max(candidates, key=lambda piece: piece.end)
        chosen.append(best)
        if best.end <= cursor:
            return None
        cursor = best.end
    return chosen


def _plan_slices(
    chosen: list[CachedPiece],
    start: float,
    end: float,
) -> list[tuple[CachedPiece, float, float]]:
    """Non-overlapping original-time slices, in order, covering ``[start, end]``."""
    slices = []
    cursor = start
    for piece in chosen:
        slice_start = max(piece.start, cursor)
        slice_end = min(piece.end, end)
        if slice_end <= slice_start:
            continue
        slices.append((piece, slice_start, slice_end))
        cursor = slice_end
        if cursor >= end - 0.05:
            break
    if cursor < end - 0.05:
        return []
    return slices


def assemble_timeline(
    slices: list[tuple[CachedPiece, float, float]],
    dest: Path,
) -> TimeMap:
    """Trim cached files locally and concatenate them onto a t=0 timeline."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not slices:
        raise YouTubeDownloadError("No cached media covers the requested ranges")
    trimmed: list[Path] = []
    bounds: list[tuple[float, float]] = []
    if len(slices) == 1:
        piece, start, end = slices[0]
        if abs(start - piece.start) < .05 and abs(end - piece.end) < .05:
            shutil.copy2(piece.path, dest)
            time_map = build_time_map([(start, end)])
            save_time_map(dest, time_map)
            return time_map
    try:
        for index, (piece, slice_start, slice_end) in enumerate(slices):
            piece_path = dest.parent / f"slice-{index}.mkv"
            trimmed.append(piece_path)
            offset = max(0.0, slice_start - piece.start)
            duration = slice_end - slice_start
            _trim_local(piece.path, piece_path, offset, duration)
            bounds.append((slice_start, slice_end))
        _concat_local(trimmed, dest, durations=[end - start for start, end in bounds])
    finally:
        for path in trimmed:
            path.unlink(missing_ok=True)
    time_map = build_time_map(bounds)
    save_time_map(dest, time_map)
    return time_map


def _trim_local(source: Path, dest: Path, offset: float, duration: float) -> None:
    command = [
        "ffmpeg", "-y", "-nostdin",
        "-ss", f"{offset:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}",
        # Stream copy seeks to keyframes, extending cuts and shifting every
        # later span away from the transcript's original-source timestamps.
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2",
        # Encode AAC only once after joining. Per-piece AAC priming otherwise
        # becomes audible padding and shifts later transcript spans.
        "-c:a", "pcm_s16le", "-ar", "48000",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists():
        raise YouTubeDownloadError("Could not trim a cached section locally")


def _concat_local(files: list[Path], dest: Path, *, durations: Optional[list[float]] = None) -> None:
    listing = dest.with_suffix(".concat.txt")
    lines = []
    for index, path in enumerate(files):
        escaped = str(path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
        if durations is not None:
            # Use the source timeline, not encoder padding, for the next span.
            lines.append(f"duration {durations[index]:.9f}")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = [
        "ffmpeg", "-y", "-nostdin",
        "-f", "concat", "-safe", "0",
        "-i", str(listing),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    listing.unlink(missing_ok=True)
    if result.returncode != 0 or not dest.exists():
        raise YouTubeDownloadError("Could not concatenate cached sections locally")


def _work_dir(video_id: str, task_id: Optional[str]) -> Path:
    from .youtube_proxy import sticky_session_id

    name = sticky_session_id(task_id, video_id)
    directory = Path(get_config().temp_dir) / WORK_DIRNAME / f"{video_id}-{name}"
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def materialize_clip_source(
    url: str,
    video_id: str,
    segments: list[dict],
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
    duration: Optional[float] = None,
    audio_path: Optional[Path] = None,
) -> Path:
    """Build a render file that covers the selected segments.

    A cached full download is returned unchanged (original timestamps).
    Otherwise only missing padded ranges are fetched, then assembled into a
    local timeline with a ``.timeline.json`` sidecar.
    """
    runtime = get_config()
    counter = budget or ByteBudget(runtime.youtube_download_max_bytes)
    directory = cache_dir_for(video_id, runtime)
    needed = merge_ranges(
        ranges_from_segments(segments),
        padding=float(runtime.youtube_section_padding_seconds),
        duration=duration,
    )
    if not needed:
        raise YouTubeDownloadError("No clip ranges to download")

    with _cache_lock(directory):
        _restore_remote_cache(video_id, directory)
        manifest = _load_manifest(directory, video_id)
        full = _full_path(directory, manifest)
        if full:
            log_youtube_attempt(
                provider="cache", video_id=video_id, kind="full",
                outcome="cache_hit", nbytes=0, task_id=task_id,
            )
            return full
        pieces = _pieces(directory, manifest)
        gaps = missing_intervals([(piece.start, piece.end) for piece in pieces], needed)

        if gaps and not proxy_configured(runtime):
            raise PartialDownloadUnavailable("Section download requires YOUTUBE_PROXY_URL")

        # Recheck and fill gaps under the same lock so a waiting job reuses
        # completed files instead of writing to another job's native parts.
        for gap_start, gap_end in gaps:
            filename = _range_filename(gap_start, gap_end)
            target = directory / filename
            _path, media_start, media_end = download_section_file(
                url, video_id, gap_start, gap_end, target, task_id=task_id, budget=counter,
            )
            manifest = _load_manifest(directory, video_id)
            manifest["ranges"] = [
                item for item in manifest.get("ranges") or []
                if not (abs(float(item.get("start", -1)) - media_start) < 0.05 and item.get("file") == filename)
            ]
            manifest["ranges"].append({
                "start": media_start,
                "end": media_end,
                "file": filename,
                "bytes": target.stat().st_size,
            })
            _save_manifest(directory, manifest)
            _persist_named(video_id, target)
            _persist_named(video_id, directory / MANIFEST_NAME)

    with _cache_lock(directory):
        manifest = _load_manifest(directory, video_id)
        pieces = _pieces(directory, manifest)
        slices: list[tuple[CachedPiece, float, float]] = []
        for start, end in needed:
            chosen = _cover_pieces(pieces, start, end)
            if not chosen:
                raise YouTubeDownloadError(
                    f"Cached media still does not cover {start:.2f}-{end:.2f}s"
                )
            slices.extend(_plan_slices(chosen, start, end))

    # Adjacent needed ranges may have selected the same slice twice.
    deduped: list[tuple[CachedPiece, float, float]] = []
    for piece, slice_start, slice_end in slices:
        if deduped and deduped[-1][2] > slice_start:
            slice_start = deduped[-1][2]
        if slice_end <= slice_start + 0.05:
            continue
        if deduped and deduped[-1][0].path == piece.path and abs(deduped[-1][1] - slice_start) < 0.05 and abs(deduped[-1][2] - slice_end) < 0.05:
            continue
        deduped.append((piece, slice_start, slice_end))

    work = _work_dir(video_id, task_id)
    render_path = work / "render.mp4"
    time_map = assemble_timeline(deduped, render_path)
    source_audio = audio_path or _audio_path(directory, _load_manifest(directory, video_id))
    if source_audio:
        copy_transcript_sidecar(source_audio, render_path, time_map)
    return render_path


def cached_full_media(video_id: str) -> Optional[Path]:
    """Return a fresh cached full download, if one is already stored."""
    runtime = get_config()
    if not getattr(runtime, "youtube_source_cache", True):
        return None
    sweep_expired_cache(runtime)
    directory = cache_dir_for(video_id, runtime)
    if not directory.exists() and _storage() is None:
        return None
    with _cache_lock(directory):
        _restore_remote_cache(video_id, directory)
        return _full_path(directory, _load_manifest(directory, video_id))


def resolve_full_youtube_download(
    url: str,
    video_id: str,
    download_full: Callable[..., Optional[Path]],
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
    skip_proxy: bool = False,
) -> Optional[Path]:
    """Return a cached full file, or download one and store it.

    ``download_full`` is the existing provider chain (proxy first unless skipped).
    """
    runtime = get_config()
    if getattr(runtime, "youtube_source_cache", True):
        sweep_expired_cache(runtime)
        directory = cache_dir_for(video_id, runtime)
        with _cache_lock(directory):
            _restore_remote_cache(video_id, directory)
            full = _full_path(directory, _load_manifest(directory, video_id))
        if full:
            log_youtube_attempt(
                provider="cache", video_id=video_id, kind="full",
                outcome="cache_hit", nbytes=0, task_id=task_id,
            )
            return full
    downloaded = download_full(skip_proxy=skip_proxy, budget=budget)
    if not downloaded:
        return None
    if getattr(runtime, "youtube_source_cache", True):
        return adopt_full_download(video_id, Path(downloaded), task_id=task_id)
    return Path(downloaded)


__all__ = [
    "DownloadBudgetExceeded",
    "PartialDownloadUnavailable",
    "YouTubeDownloadError",
    "YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY",
]
