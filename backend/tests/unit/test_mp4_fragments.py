"""A fragmented MP4's length comes from its header and last fragments, in a few ranged reads."""

import http.server
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.media import mp4_fragments
from src.media.ffmpeg import ffprobe_duration
from src.media.mp4_fragments import fragmented_mp4_duration

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required"
)


def box(kind: bytes, *children: bytes) -> bytes:
    payload = b"".join(children)
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def track(track_id: int, timescale: int, handler: bytes) -> bytes:
    return box(
        b"trak",
        full(b"tkhd", 0, 3, struct.pack(">III", 0, 0, track_id) + bytes(68)),
        box(
            b"mdia",
            full(b"mdhd", 1, 0, struct.pack(">QQII", 0, 0, timescale, 0) + bytes(4)),
            full(b"hdlr", 0, 0, bytes(4) + handler + bytes(13)),
        ),
    )


def fragment(sequence: int, trafs: list[bytes], mdat_bytes: int = 64) -> bytes:
    return box(b"moof", full(b"mfhd", 0, 0, struct.pack(">I", sequence)), *trafs) + box(b"mdat", bytes(mdat_bytes))


def traf(track_id: int, start: int, durations: list[int] | None = None, count: int = 0, default: int | None = None) -> bytes:
    tfhd_flags = 0x020000 | (0x08 if default is not None else 0)
    tfhd = full(b"tfhd", 0, tfhd_flags, struct.pack(">I", track_id) + (struct.pack(">I", default) if default is not None else b""))
    tfdt = full(b"tfdt", 1, 0, struct.pack(">Q", start))
    if durations is not None:
        # data offset + per-sample duration and size
        trun = full(b"trun", 0, 0x000301, struct.pack(">Ii", len(durations), 0) + b"".join(struct.pack(">II", d, 100) for d in durations))
    else:
        trun = full(b"trun", 0, 0x000001, struct.pack(">Ii", count, 0))
    return box(b"traf", tfhd, tfdt, trun)


def fragmented_file(fragments: list[bytes]) -> bytes:
    moov = box(
        b"moov",
        full(b"mvhd", 0, 0, bytes(96)),
        track(1, 90000, b"vide"),
        track(2, 48000, b"soun"),
        track(3, 1000, b"text"),
        box(b"mvex", full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 3000, 0, 0)), full(b"trex", 0, 0, struct.pack(">IIIII", 2, 1, 1024, 0, 0))),
    )
    return box(b"ftyp", b"isom", bytes(4), b"isomiso6") + moov + b"".join(fragments)


def two_second_fragments(count: int) -> list[bytes]:
    # Video: 60 samples of 3000 ticks at 90 kHz; audio: 94 samples of 1024 at 48 kHz.
    return [
        fragment(n + 1, [traf(1, n * 180000, durations=[3000] * 60), traf(2, n * 96256, count=94)])
        for n in range(count)
    ]


def test_length_is_the_end_of_the_last_fragment(tmp_path):
    path = tmp_path / "rec.mp4"
    path.write_bytes(fragmented_file(two_second_fragments(30)))
    # Audio ends at 30 * 96256 / 48000 = 60.16 s, past the video's 60 s.
    assert fragmented_mp4_duration(path) == pytest.approx(30 * 96256 / 48000)


def test_length_is_measured_from_the_first_fragment(tmp_path):
    fragments = [fragment(n + 1, [traf(1, 900000 + n * 180000, durations=[3000] * 60)]) for n in range(5)]
    path = tmp_path / "offset.mp4"
    path.write_bytes(fragmented_file(fragments))
    assert fragmented_mp4_duration(path) == pytest.approx(10.0)


def test_sample_durations_fall_back_to_the_fragment_then_the_track_default(tmp_path):
    fragments = [fragment(1, [traf(1, 0, count=30, default=6000), traf(2, 0, count=10)])]
    path = tmp_path / "defaults.mp4"
    path.write_bytes(fragmented_file(fragments))
    # Video: 30 * 6000 / 90000 = 2 s; audio: 10 * 1024 / 48000 (trex default) is shorter.
    assert fragmented_mp4_duration(path) == pytest.approx(2.0)


def test_regular_mp4_and_other_containers_are_left_to_ffprobe(tmp_path):
    faststart = box(b"ftyp", b"isom", bytes(4)) + box(b"moov", full(b"mvhd", 0, 0, bytes(96)), track(1, 90000, b"vide")) + box(b"mdat", bytes(64))
    index_at_end = box(b"ftyp", b"isom", bytes(4)) + box(b"mdat", bytes(64)) + box(b"moov", full(b"mvhd", 0, 0, bytes(96)))
    for name, data in {
        "faststart.mp4": faststart,
        "index_at_end.mp4": index_at_end,
        "video.webm": b"\x1a\x45\xdf\xa3" + bytes(64),
        "empty.mp4": b"",
        "truncated.mp4": fragmented_file(two_second_fragments(1))[:100],
    }.items():
        path = tmp_path / name
        path.write_bytes(data)
        assert fragmented_mp4_duration(path) is None, name


def test_large_fragments_are_left_to_ffprobe(tmp_path, monkeypatch):
    # Few fragments, each larger than the tail windows: ffprobe visits them quickly.
    monkeypatch.setattr(mp4_fragments, "TAIL_WINDOWS", (256, 512, 1024))
    path = tmp_path / "big.mp4"
    path.write_bytes(fragmented_file([
        fragment(n + 1, [traf(1, n * 180000, durations=[3000] * 60)], mdat_bytes=4096) for n in range(3)
    ]))
    assert fragmented_mp4_duration(path) is None


class _RangeServer:
    """Serves one file with Range support and counts requests."""

    def __init__(self, data: bytes, *, honour_range=True, delay=0.0):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.headers.get("Range"))
                time.sleep(delay)
                ranged = self.headers.get("Range")
                if honour_range and ranged:
                    first, last = ranged.removeprefix("bytes=").split("-")
                    first, last = int(first), min(int(last), len(data) - 1)
                    body = data[first:last + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {first}-{last}/{len(data)}")
                else:
                    body = data
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/source.mp4?X-Amz-Signature=abc"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_signed_url_needs_only_a_few_ranged_reads():
    # 1,800 fragments; ffprobe would request each one.
    data = fragmented_file(two_second_fragments(1800))
    server = _RangeServer(data)
    try:
        assert fragmented_mp4_duration(server.url) == pytest.approx(1800 * 96256 / 48000)
    finally:
        server.close()
    assert len(server.requests) <= 3
    assert all(r and r.startswith("bytes=") for r in server.requests)


def test_server_that_ignores_range_is_left_to_ffprobe():
    server = _RangeServer(fragmented_file(two_second_fragments(3)), honour_range=False)
    try:
        assert fragmented_mp4_duration(server.url) is None
    finally:
        server.close()
    assert len(server.requests) == 1


def test_slow_storage_gives_up_within_the_budget():
    server = _RangeServer(fragmented_file(two_second_fragments(3)), delay=2.0)
    started = time.monotonic()
    try:
        assert fragmented_mp4_duration(server.url, budget=0.5) is None
    finally:
        server.close()
    assert time.monotonic() - started < 1.5


@needs_ffmpeg
@pytest.mark.parametrize(
    "movflags",
    [
        "frag_keyframe+empty_moov+default_base_moof",
        "frag_keyframe+empty_moov+separate_moof",
        "frag_keyframe+empty_moov+global_sidx",
        "frag_keyframe",  # first samples indexed in the moov, then fragments
    ],
)
def test_matches_ffprobe_on_fragmented_recordings(tmp_path, movflags):
    path = tmp_path / "rec.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30",
            "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000",
            "-t", "7", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
            "-c:a", "aac", "-pix_fmt", "yuv420p", "-movflags", movflags, str(path),
        ],
        check=True,
        timeout=120,
    )
    assert fragmented_mp4_duration(path) == pytest.approx(ffprobe_duration(path), abs=0.01)


@needs_ffmpeg
@pytest.mark.parametrize("movflags", ["+faststart", ""])
def test_regular_mp4_from_ffmpeg_is_left_to_ffprobe(tmp_path, movflags):
    path = tmp_path / "regular.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30", "-t", "2",
            "-c:v", "libx264", "-preset", "ultrafast", *(["-movflags", movflags] if movflags else []), str(path),
        ],
        check=True,
        timeout=120,
    )
    assert fragmented_mp4_duration(Path(path)) is None
