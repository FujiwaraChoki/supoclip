"""Stream every clip of a task as one ZIP, without staging it on disk.

The archive is produced on the fly: each clip is read in chunks from local
disk or object storage, written through ``zipfile`` into an in-memory sink, and
the sink is drained after every chunk. Memory stays at about one chunk per
download and nothing is written to disk.

Entries use DEFLATE at level 0 (stored blocks, no compression work; MP4 is
already compressed). Because the output is not seekable, sizes and CRCs go in
data descriptors after each entry. Readers that unpack a ZIP front to back
(Java's ``ZipInputStream``, some archive utilities) cannot find the end of a
STORED entry that has a data descriptor, but they can for a DEFLATE stream,
so this keeps streamed archives readable everywhere for ~0.01% overhead.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import logging
from pathlib import Path
import re
import secrets
import time
from typing import Any, AsyncIterator, Callable, Iterable
from urllib.parse import quote
import unicodedata
import zipfile

import redis.asyncio as redis

from .config import get_config
from .media_storage import MediaStorage, is_s3
from .utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
# zipfile switches to ZIP64 records at 2 GiB less a margin; force them early
# for entries that could cross the limit.
ZIP64_THRESHOLD = int(zipfile.ZIP64_LIMIT / 1.05)
SLUG_MAX_CHARS = 60


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    reference: str
    size: int


class ClipArchiveBusy(Exception):
    pass


def slugify(text: str | None, max_chars: int = SLUG_MAX_CHARS) -> str:
    """Lowercase, filesystem-safe slug that keeps non-Latin letters."""
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    slug = re.sub(r"[^\w]+", "-", normalized).replace("_", "-")
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:max_chars].rstrip("-")


def archive_entry_names(clips: list[dict[str, Any]]) -> list[str]:
    """``01-hook-title.mp4`` in the given order.

    The position prefix keeps names unique even when hook titles repeat.
    """
    width = max(2, len(str(len(clips))))
    names = []
    for position, clip in enumerate(clips, start=1):
        label = slugify(clip.get("hook_title")) or f"clip-{slugify(str(clip.get('id', '')))[:8] or position}"
        names.append(f"{position:0{width}d}-{label}.mp4")
    return names


def archive_filename(title: str | None) -> str:
    return f"{slugify(title) or 'supoclip'}-clips.zip"


def content_disposition(filename: str) -> str:
    """Attachment header with an ASCII fallback and an RFC 5987 UTF-8 name."""
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_name)
    ascii_name = re.sub(r"-{2,}", "-", ascii_name).strip("-.") or "supoclip-clips.zip"
    if not ascii_name.endswith(".zip"):
        ascii_name = "supoclip-clips.zip"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def resolve_entries(clips: list[dict[str, Any]], storage: MediaStorage) -> list[ArchiveEntry]:
    """Keep only clips whose file exists, with their size. Blocking."""
    present = []
    for clip in clips:
        reference = clip.get("file_path") or ""
        if not reference:
            continue
        if is_s3(reference):
            size = storage.object_size(reference)
        else:
            path = Path(reference)
            size = path.stat().st_size if path.is_file() else None
        if size is None:
            logger.warning("Skipping clip %s in archive: file is missing", clip.get("id"))
            continue
        present.append((clip, reference, size))
    names = archive_entry_names([clip for clip, _, _ in present])
    return [ArchiveEntry(name, reference, size) for name, (_, reference, size) in zip(names, present)]


def open_entry(entry: ArchiveEntry, storage: MediaStorage):
    """A binary reader with ``read(n)`` and ``close()``. Blocking."""
    if is_s3(entry.reference):
        return storage.open_object(entry.reference)
    return open(entry.reference, "rb")


class _ChunkSink:
    """Write-only, non-seekable target that hands written bytes back out."""

    def __init__(self):
        self._chunks: list[bytes] = []
        self._offset = 0

    def write(self, data) -> int:
        data = bytes(data)
        self._chunks.append(data)
        self._offset += len(data)
        return len(data)

    def tell(self) -> int:
        return self._offset

    def seek(self, *_args):
        raise io.UnsupportedOperation("seek")

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def _entry_info(name: str) -> zipfile.ZipInfo:
    # ZipFile.open(name) would date entries 1980-01-01 and leave no permissions.
    info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info._compresslevel = 0
    info.external_attr = 0o644 << 16
    return info


def _copy_chunk(source, target, size: int) -> bool:
    data = source.read(size)
    if not data:
        return False
    target.write(data)
    return True


async def stream_archive(
    entries: Iterable[ArchiveEntry],
    opener: Callable[[ArchiveEntry], Any],
    *,
    chunk_bytes: int = CHUNK_BYTES,
    on_chunk: Callable[[], Any] | None = None,
) -> AsyncIterator[bytes]:
    """Yield the ZIP's bytes. Blocking reads and writes run off the event loop.

    A read error mid-stream propagates, so the HTTP response is cut off and the
    browser reports a failed download instead of saving a truncated archive.
    """
    sink = _ChunkSink()
    archive = zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=0, allowZip64=True)
    for entry in entries:
        source = await run_in_thread(opener, entry)
        try:
            target = archive.open(_entry_info(entry.name), mode="w", force_zip64=entry.size >= ZIP64_THRESHOLD)
            while await run_in_thread(_copy_chunk, source, target, chunk_bytes):
                data = sink.take()
                if data:
                    yield data
                if on_chunk is not None:
                    await on_chunk()
            await run_in_thread(target.close)
        finally:
            # Closing a file or an S3 body does not block; keep it synchronous
            # so it also runs when the client disconnects mid-download.
            source.close()
        data = sink.take()
        if data:
            yield data
    archive.close()
    data = sink.take()
    if data:
        yield data


# --- One archive download at a time per user --------------------------------
#
# Uvicorn may run several API processes or replicas, so the guard lives in
# Redis rather than in process memory. The key carries a random token and a
# short TTL that the stream refreshes while it is sending; a crashed process
# frees its slot within the TTL.

SLOT_TTL_MS = 60_000
SLOT_REFRESH_SECONDS = 15
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
_REFRESH = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) end return 0"


def _slot_key(user_id: str) -> str:
    return f"clip_archive:{user_id}"


def _redis_client():
    config = get_config()
    return redis.Redis(
        host=config.redis_host, port=config.redis_port,
        password=config.redis_password, decode_responses=True,
    )


async def archive_in_progress(user_id: str) -> bool:
    client = _redis_client()
    try:
        return bool(await client.exists(_slot_key(user_id)))
    except Exception as exc:
        logger.warning("Could not check the clip archive slot: %s", exc)
        return False
    finally:
        await client.aclose()


class ArchiveSlot:
    """Held for the lifetime of one streamed archive."""

    def __init__(self, user_id: str):
        self.key = _slot_key(user_id)
        self.token = secrets.token_hex(16)
        self.client = None
        self._refreshed_at = 0.0

    async def acquire(self) -> "ArchiveSlot":
        client = _redis_client()
        try:
            acquired = await client.set(self.key, self.token, nx=True, px=SLOT_TTL_MS)
        except Exception as exc:
            # Fail open: a Redis outage should not block reading one's own clips.
            logger.warning("Could not reserve the clip archive slot: %s", exc)
            await client.aclose()
            return self
        if not acquired:
            await client.aclose()
            raise ClipArchiveBusy("A ZIP download of your clips is already in progress. Wait for it to finish, then try again.")
        self.client = client
        self._refreshed_at = time.monotonic()
        return self

    async def refresh(self) -> None:
        if self.client is None or time.monotonic() - self._refreshed_at < SLOT_REFRESH_SECONDS:
            return
        self._refreshed_at = time.monotonic()
        try:
            await self.client.eval(_REFRESH, 1, self.key, self.token, SLOT_TTL_MS)
        except Exception as exc:
            logger.warning("Could not refresh the clip archive slot: %s", exc)

    async def release(self) -> None:
        client, self.client = self.client, None
        if client is None:
            return
        try:
            await client.eval(_RELEASE, 1, self.key, self.token)
        except Exception as exc:
            logger.warning("Could not release the clip archive slot: %s", exc)
        finally:
            await client.aclose()


async def stream_with_slot(
    entries: list[ArchiveEntry], storage: MediaStorage, slot: ArchiveSlot
) -> AsyncIterator[bytes]:
    try:
        async for chunk in stream_archive(entries, lambda entry: open_entry(entry, storage), on_chunk=slot.refresh):
            yield chunk
    except Exception:
        logger.exception("Clip archive stream failed")
        raise
    finally:
        await slot.release()
