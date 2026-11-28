"""A part of a live YouTube stream, read from its DVR by segment number.

A loopback server stands in for YouTube: one fragmented MP4 is split into
segments, each repeating the MP4 header as live segments do, served at
``?sq=N`` with ``X-Head-Seqnum`` on the base URL. yt-dlp's own downloader and
the local remux run for real; nothing leaves the machine.
"""
from __future__ import annotations

import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from src import youtube_proxy
from src.config import Config
from src.youtube_proxy import ByteBudget, LiveDvrUnavailable, live_dvr_fragments

SEGMENT_SECONDS = 2.0


def _boxes(data: bytes):
    offset = 0
    while offset < len(data):
        size, kind = struct.unpack(">I4s", data[offset:offset + 8])
        yield kind.decode(), data[offset:offset + size]
        offset += size


def _segments(tmp_path: Path, seconds: int = 20) -> list[bytes]:
    source = tmp_path / "live.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "aac", "-b:a", "64k", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
         "-frag_duration", str(int(SEGMENT_SECONDS * 1_000_000)), str(source)],
        check=True,
    )
    header = b""
    segments: list[bytes] = []
    pending = b""
    for kind, box in _boxes(source.read_bytes()):
        if kind in ("ftyp", "moov"):
            header += box
        elif kind == "moof":
            pending = box
        elif kind == "mdat":
            segments.append(header + pending + box)
    return segments


@pytest.fixture
def live_server(tmp_path):
    segments = _segments(tmp_path)
    requested: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return None

        def _sequence(self):
            values = parse_qs(urlparse(self.path).query).get("sq")
            return int(values[0]) if values else None

        def do_HEAD(self):
            sequence = self._sequence()
            if sequence is None:
                self.send_response(200)
                self.send_header("X-Head-Seqnum", str(len(segments) - 1))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200 if 0 <= sequence < len(segments) else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            sequence = self._sequence()
            if sequence is None or not 0 <= sequence < len(segments):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            requested.append(sequence)
            body = segments[sequence]
            self.send_response(200)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/videoplayback?itag=140", segments, requested
    server.shutdown()


@pytest.fixture
def no_proxy(monkeypatch):
    config = Config()
    config.youtube_proxy_url = ""
    config.youtube_pot_provider_url = ""
    monkeypatch.setattr(youtube_proxy, "get_config", lambda: config)
    monkeypatch.setattr(youtube_proxy, "trial_config", lambda value: value)
    return config


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def test_fragment_numbers_follow_the_segment_length():
    fmt = {"url": "https://media.example.com/videoplayback?itag=140", "target_duration": 2}
    fragments, start, end = live_dvr_fragments(fmt, 61.0, 70.5, head=1000)
    assert [f["start_time"] for f in fragments] == [60.0, 62.0, 64.0, 66.0, 68.0, 70.0]
    assert (start, end) == (60.0, 72.0)
    assert "sq=30" in fragments[0]["url"] and "itag=140" in fragments[0]["url"]


def test_a_part_never_reads_the_segment_still_being_written():
    fmt = {"url": "https://media.example.com/videoplayback", "target_duration": 5}
    fragments, _start, end = live_dvr_fragments(fmt, 90.0, 200.0, head=30)
    assert end == 150.0 and len(fragments) == 12


def test_a_part_beyond_the_recording_is_refused():
    fmt = {"url": "https://media.example.com/videoplayback", "target_duration": 5}
    with pytest.raises(LiveDvrUnavailable):
        live_dvr_fragments(fmt, 500.0, 600.0, head=30)


def test_dvr_part_downloads_only_its_segments_and_remuxes_them(tmp_path, live_server, no_proxy):
    url, segments, requested = live_server
    fmt = {"url": url, "target_duration": SEGMENT_SECONDS, "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none",
           "format_id": "140", "protocol": "http_dash_segments_generator"}
    budget = ByteBudget(10**9)
    options = youtube_proxy.download_options(None, "livevideo1", budget, allow_direct=True)
    assert "proxy" not in options
    head = youtube_proxy.dvr_head_sequence(fmt, options)
    assert head == len(segments) - 1

    path, media_start, media_end = youtube_proxy._download_dvr_format(
        options, "https://www.youtube.com/watch?v=livevideo1", "livevideo1", fmt,
        tmp_path / "part", 6.5, 11.0, head,
    )

    assert sorted(requested) == [3, 4, 5]
    assert (media_start, media_end) == (6.0, 12.0)
    # One clean file whose length matches the three segments, not the whole stream.
    assert _duration(path) == pytest.approx(6.0, abs=0.15)
    assert budget.snapshot() > 0


def test_dvr_part_before_what_the_stream_keeps_is_refused(tmp_path, live_server, no_proxy, monkeypatch):
    url, _segments_list, requested = live_server
    fmt = {"url": url, "target_duration": SEGMENT_SECONDS, "ext": "m4a", "protocol": "http_dash_segments_generator"}
    options = youtube_proxy.download_options(None, "livevideo1", ByteBudget(10**9), allow_direct=True)
    monkeypatch.setattr(youtube_proxy, "_dvr_segment_available", lambda *_a: False)
    with pytest.raises(LiveDvrUnavailable):
        youtube_proxy._download_dvr_format(
            options, "https://www.youtube.com/watch?v=livevideo1", "livevideo1", fmt,
            tmp_path / "part", 0.0, 4.0, 9,
        )
    assert requested == []


def test_live_video_audio_section_uses_the_dvr(tmp_path, live_server, no_proxy, monkeypatch):
    url, _segments_list, requested = live_server
    dvr = {"url": url, "target_duration": SEGMENT_SECONDS, "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none",
           "format_id": "140", "protocol": "http_dash_segments_generator", "abr": 128}
    seen = {}

    def extract(_url, options):
        seen["live_from_start"] = options.get("live_from_start")
        return {"live_status": "is_live", "formats": [dvr]}

    monkeypatch.setattr(youtube_proxy, "extract_video_info", extract)
    path, start, end = youtube_proxy.download_audio_section(
        "https://www.youtube.com/watch?v=livevideo1", "livevideo1", 2.0, 8.0, tmp_path / "audio", live=True,
    )
    assert seen["live_from_start"] is True
    assert (start, end) == (2.0, 8.0)
    assert sorted(requested) == [1, 2, 3]
    assert _duration(path) == pytest.approx(6.0, abs=0.15)
