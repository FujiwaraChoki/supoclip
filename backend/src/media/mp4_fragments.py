"""Length of a fragmented MP4 from its header and its last fragments.

A fragmented MP4 (recorders such as OBS can write these) keeps no sample
table in its ``moov``; every fragment carries its own. To find the length,
ffprobe visits every fragment. Over a signed storage URL that is one ranged request per
fragment, or a read of the whole file when fragments are small, which can take
longer than the create-task request may wait.

Each fragment's ``tfdt`` holds its start time on the track's timeline, so the
last fragments alone give the end. This reads the header boxes and the tail of
the file: a few ranged requests whatever the file's size.

``fragmented_mp4_duration`` returns None for anything else (a regular MP4,
another container, a fragmented file it cannot read); the caller then asks
ffprobe.
"""

from __future__ import annotations

import logging
import struct
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

HEAD_BYTES = 64 * 1024
# Most fragments are a few MB. Grow the tail read until a whole fragment fits;
# a file with larger fragments has few of them, and ffprobe reads it quickly.
TAIL_WINDOWS = (4 * 1024 * 1024, 16 * 1024 * 1024, 64 * 1024 * 1024)
# A fragmented file's moov holds no sample table and is a few KB. A larger one
# indexes a regular MP4, which ffprobe reads quickly; don't fetch it twice.
MAX_MOOV_BYTES = 1024 * 1024
MAX_TOP_LEVEL_BOXES = 32
# All ranged reads together; the caller still has time for ffprobe after this.
READ_BUDGET_SECONDS = 15


class _Unreadable(Exception):
    pass


class _FileReader:
    def __init__(self, path: Path):
        self.path = path
        self.size = path.stat().st_size

    def read(self, start: int, length: int) -> bytes:
        with self.path.open("rb") as fh:
            fh.seek(start)
            return fh.read(length)


class _UrlReader:
    """Ranged GETs against a signed URL."""

    def __init__(self, url: str, budget: float):
        self.url = url
        self.deadline = time.monotonic() + budget
        self.size = None
        self._head = self._get(0, HEAD_BYTES)

    def _get(self, start: int, length: int) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _Unreadable("out of time")
        request = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{start + length - 1}"})
        with urllib.request.urlopen(request, timeout=remaining) as response:
            # A server that ignores Range would send the whole file.
            if response.status != 206:
                raise _Unreadable(f"range request answered {response.status}")
            content_range = response.headers.get("Content-Range", "")
            total = content_range.rpartition("/")[2]
            if not total.isdigit():
                raise _Unreadable("no object size in Content-Range")
            self.size = int(total)
            return response.read(length)

    def read(self, start: int, length: int) -> bytes:
        if start == 0 and length <= len(self._head):
            return self._head[:length]
        return self._get(start, length)


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    """(type, payload start, box end) for each complete box in ``data[start:end]``."""
    end = len(data) if end is None else end
    offset = start
    while offset + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            return
        yield kind, offset + header, offset + size
        offset += size


def _box_size(header: bytes) -> int:
    size = struct.unpack_from(">I", header)[0]
    if size == 1:
        return struct.unpack_from(">Q", header, 8)[0] if len(header) >= 16 else 0
    return size


def _child(data: bytes, start: int, end: int, kind: bytes):
    for found, payload, box_end in _boxes(data, start, end):
        if found == kind:
            return payload, box_end
    return None


def _tracks(moov: bytes) -> tuple[dict[int, int], dict[int, int]] | None:
    """Timescale of each audio/video track and its default sample duration, or None if not fragmented.

    ``moov`` is the box's payload, without its header.
    """
    mvex = _child(moov, 0, len(moov), b"mvex")
    if mvex is None:
        return None
    timescales: dict[int, int] = {}
    for kind, payload, end in _boxes(moov):
        if kind != b"trak":
            continue
        tkhd = _child(moov, payload, end, b"tkhd")
        mdia = _child(moov, payload, end, b"mdia")
        if tkhd is None or mdia is None:
            continue
        hdlr = _child(moov, mdia[0], mdia[1], b"hdlr")
        mdhd = _child(moov, mdia[0], mdia[1], b"mdhd")
        if hdlr is None or mdhd is None or moov[hdlr[0] + 8:hdlr[0] + 12] not in (b"vide", b"soun"):
            continue
        track_id = struct.unpack_from(">I", moov, tkhd[0] + (20 if moov[tkhd[0]] == 1 else 12))[0]
        timescale = struct.unpack_from(">I", moov, mdhd[0] + (20 if moov[mdhd[0]] == 1 else 12))[0]
        if timescale:
            timescales[track_id] = timescale
    defaults: dict[int, int] = {}
    for kind, payload, _end in _boxes(moov, mvex[0], mvex[1]):
        if kind == b"trex":
            track_id, _index, duration = struct.unpack_from(">III", moov, payload + 4)
            defaults[track_id] = duration
    return (timescales, defaults) if timescales else None


def _fragment_times(data: bytes, start: int, end: int, defaults: dict[int, int]) -> dict[int, tuple[int, int]]:
    """(first, last) tick of each track fragment in one ``moof``."""
    times: dict[int, tuple[int, int]] = {}
    for kind, payload, traf_end in _boxes(data, start, end):
        if kind != b"traf":
            continue
        tfhd = _child(data, payload, traf_end, b"tfhd")
        tfdt = _child(data, payload, traf_end, b"tfdt")
        if tfhd is None or tfdt is None:
            continue
        flags = int.from_bytes(data[tfhd[0] + 1:tfhd[0] + 4], "big")
        track_id = struct.unpack_from(">I", data, tfhd[0] + 4)[0]
        offset = tfhd[0] + 8
        offset += 8 if flags & 0x01 else 0  # base data offset
        offset += 4 if flags & 0x02 else 0  # sample description index
        default = struct.unpack_from(">I", data, offset)[0] if flags & 0x08 else defaults.get(track_id, 0)
        base = struct.unpack_from(">Q" if data[tfdt[0]] == 1 else ">I", data, tfdt[0] + 4)[0]
        ticks = 0
        for run_kind, run, run_end in _boxes(data, payload, traf_end):
            if run_kind != b"trun":
                continue
            run_flags = int.from_bytes(data[run + 1:run + 4], "big")
            count = struct.unpack_from(">I", data, run + 4)[0]
            cursor = run + 8 + (4 if run_flags & 0x01 else 0) + (4 if run_flags & 0x04 else 0)
            if not run_flags & 0x100:
                ticks += count * default
                continue
            stride = 4 * bin(run_flags & 0xF00).count("1")
            if cursor + count * stride > run_end:
                raise _Unreadable("truncated trun")
            ticks += sum(struct.unpack_from(">I", data, cursor + i * stride)[0] for i in range(count))
        times[track_id] = (base, base + ticks)
    return times


def _moofs(tail: bytes):
    """Every complete ``moof`` box in a byte window that may start mid-box."""
    position = tail.find(b"moof", 4)
    while position != -1:
        start = position - 4
        size = struct.unpack_from(">I", tail, start)[0]
        end = start + size
        if 16 <= size and end <= len(tail) and tail[start + 12:start + 16] == b"mfhd":
            yield start, end
        position = tail.find(b"moof", position + 4)


def _duration(reader) -> float | None:
    head = reader.read(0, HEAD_BYTES)
    if head[4:8] != b"ftyp":
        return None
    tracks = first_moof = None
    samples_before_fragments = False
    offset = 0
    for _ in range(MAX_TOP_LEVEL_BOXES):
        header = head[offset:offset + 16] if offset + 16 <= len(head) else reader.read(offset, 16)
        if len(header) < 8:
            return None
        kind = header[4:8]
        size = _box_size(header)
        payload = 16 if struct.unpack_from(">I", header)[0] == 1 else 8
        if size < payload:
            return None
        if kind == b"moov":
            if size > MAX_MOOV_BYTES:
                return None
            moov = head[offset:offset + size] if offset + size <= len(head) else reader.read(offset, size)
            tracks = _tracks(moov[payload:])
            if tracks is None:
                # No movie extends box: a regular MP4, which ffprobe reads quickly.
                return None
        elif kind == b"moof":
            if tracks is None or size > MAX_MOOV_BYTES:
                return None
            first_moof = head[offset:offset + size] if offset + size <= len(head) else reader.read(offset, size)
            after = offset + size
            header = head[after:after + 16] if after + 16 <= len(head) else reader.read(after, 16)
            if header[4:8] == b"mdat" and _box_size(header) > TAIL_WINDOWS[1]:
                # Few, large fragments: ffprobe visits them quickly.
                return None
            break
        elif kind == b"mdat":
            if tracks is None:
                # Samples before the movie header: a regular MP4 with its index at the end.
                return None
            # The moov indexes these first samples itself; fragments follow.
            samples_before_fragments = True
        offset += size
        if offset >= reader.size:
            return None
    if first_moof is None:
        return None
    timescales, defaults = tracks
    starts = {} if samples_before_fragments else _fragment_times(first_moof, 8, len(first_moof), defaults)

    tail = b""
    for window in TAIL_WINDOWS:
        length = min(window, reader.size)
        tail = reader.read(reader.size - length, length - len(tail)) + tail
        ends: dict[int, int] = {}
        for start, end in _moofs(tail):
            for track_id, (_first, last) in _fragment_times(tail, start + 8, end, defaults).items():
                ends[track_id] = max(ends.get(track_id, 0), last)
        ends = {track: tick for track, tick in ends.items() if track in timescales}
        if ends:
            first = min(
                (starts[track][0] / timescales[track] for track in timescales if track in starts),
                default=0.0,
            )
            return max(tick / timescales[track] for track, tick in ends.items()) - first
        if length == reader.size:
            break
    return None


def fragmented_mp4_duration(source: str | Path, *, budget: float = READ_BUDGET_SECONDS) -> float | None:
    """Seconds in a fragmented MP4 at a local path or signed URL, or None when this can't tell."""
    try:
        text = str(source)
        reader = _UrlReader(text, budget) if text.startswith(("http://", "https://")) else _FileReader(Path(source))
        duration = _duration(reader)
    except Exception as exc:
        logger.info("Fragmented MP4 length unavailable, falling back to ffprobe: %s", exc)
        return None
    return duration if duration is not None and duration > 0 else None
