"""Real yt-dlp HTTP transport, injected failures, and no proxy connections."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
from threading import Thread

import pytest

from src import youtube_proxy as proxy
from src.config import Config, set_config_override


@pytest.fixture
def transport(tmp_path, monkeypatch):
    payload = b"media-for-native-download" * 8192
    state = {"requests": 0, "metadata": 0, "sessions": [], "failure": "502", "failures": 1}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["requests"] += 1
            failing = state["requests"] <= state["failures"]
            if failing and state["failure"] in {"502", "503", "504", "403"}:
                self.send_error(int(state["failure"]))
                return
            if failing and state["failure"] == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if failing and state["failure"] == "truncated":
                self.wfile.write(payload[:16384])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = Config()
    config.youtube_proxy_url = ""
    config.youtube_pot_provider_url = ""
    set_config_override(config)
    media_url = f"http://127.0.0.1:{server.server_port}/audio.m4a"

    def options(_task, _video, **kwargs):
        state["sessions"].append(kwargs.get("session_id"))
        return {"proxy": "", "quiet": True, "noprogress": True, "retries": 0,
                "fragment_retries": 0, "fixup": "never", "continuedl": False}

    def metadata(_url, _options):
        state["metadata"] += 1
        return {"formats": [{"format_id": "140", "protocol": "http", "vcodec": "none",
                             "acodec": "aac", "abr": 128, "ext": "m4a", "url": media_url}]}

    monkeypatch.setattr(proxy, "base_proxy_params", options)
    monkeypatch.setattr(proxy, "extract_video_info", metadata)
    try:
        yield state, payload, lambda budget: proxy.download_audio(
            "https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk", tmp_path / "audio", budget=budget,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        set_config_override(None)


@pytest.mark.parametrize("failure", ["502", "503", "504", "truncated", "disconnect"])
def test_real_transport_failure_recovers_once_without_a_proxy(transport, failure):
    state, payload, download = transport
    state["failure"] = failure
    budget = proxy.ByteBudget(len(payload) * 3)
    assert download(budget).read_bytes() == payload
    assert state["metadata"] == state["requests"] == 2
    assert state["sessions"][0] == proxy.sticky_session_id(None, "abcdefghijk")
    assert state["sessions"][1] and state["sessions"][1] != state["sessions"][0]
    assert budget.total >= len(payload) + (16384 if failure == "truncated" else 0)


def test_real_403_does_not_rotate_transport_session(transport):
    state, _, download = transport
    state["failure"] = "403"
    with pytest.raises(proxy.AudioDownloadForbidden):
        download(proxy.ByteBudget(1_000_000))
    assert state["metadata"] == state["requests"] == 1


def test_truncated_last_block_exhausts_budget_before_another_request(transport):
    state, _, download = transport
    state["failure"] = "truncated"
    budget = proxy.ByteBudget(16383)
    with pytest.raises(proxy.DownloadBudgetExceeded):
        download(budget)
    assert budget.total == 16384
    assert state["metadata"] == state["requests"] == 1


def test_wrapped_budget_error_overrides_transient_outer_message():
    try:
        raise proxy.DownloadBudgetExceeded("over cap")
    except proxy.DownloadBudgetExceeded as cause:
        error = RuntimeError("connection reset during download")
        error.__cause__ = cause
    assert not proxy.is_transient_proxy_error(error)


@pytest.mark.parametrize("failure", ["502", "truncated", "disconnect"])
def test_real_transport_exhaustion_stops_at_two_attempts(transport, failure):
    state, payload, download = transport
    state.update(failure=failure, failures=100)
    with pytest.raises(Exception):
        download(proxy.ByteBudget(len(payload) * 3))
    assert state["metadata"] == state["requests"] == 2


@pytest.mark.parametrize("failure", ["502", "truncated"])
def test_real_hls_section_retry_preserves_previous_section(tmp_path, monkeypatch, failure):
    """Exercise yt-dlp's fragment downloader and real local ffmpeg muxing."""
    segment = tmp_path / "fixture.ts"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
        "-c:a", "aac", "-f", "mpegts", str(segment),
    ], check=True, capture_output=True, timeout=30)
    payload = segment.read_bytes()
    requests, sessions = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            failing = self.path == "/audio-1.ts" and requests.count(self.path) == 1
            if failing and failure == "502":
                self.send_error(502)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[:16384] if failing else payload)
            self.wfile.flush()
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    config = Config()
    config.youtube_proxy_url = config.youtube_pot_provider_url = ""
    set_config_override(config)

    def options(_task, _video, **kwargs):
        sessions.append(kwargs.get("session_id"))
        return {"proxy": "", "quiet": True, "noprogress": True, "retries": 0,
                "fragment_retries": 0, "fixup": "never", "continuedl": False}

    def metadata(_url, _options):
        return {"duration": 4, "formats": [
            {"format_id": kind, "url": f"{base}/{kind}.m3u8", "protocol": "m3u8_native",
             "ext": "mp4", "vcodec": "avc1" if kind == "video" else "none",
             "acodec": "aac", "height": 180 if kind == "video" else None,
             "fragments": [{"url": f"{base}/{kind}-{i}.ts", "duration": 2} for i in range(2)]}
            for kind in ("video", "audio")
        ]}

    monkeypatch.setattr(proxy, "base_proxy_params", options)
    monkeypatch.setattr(proxy, "extract_video_info", metadata)
    budget = proxy.ByteBudget(len(payload) * 10)
    try:
        outputs = []
        for i in range(2):
            path, start, end = proxy.download_section_file(
                "https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk",
                i * 2, (i + 1) * 2, tmp_path / f"section-{i}.mp4", budget=budget,
            )
            assert (start, end) == (i * 2, (i + 1) * 2)
            outputs.append((path, path.read_bytes()))
        assert outputs[0][0].read_bytes() == outputs[0][1]
        assert requests == ["/video-0.ts", "/audio-0.ts", "/video-1.ts", "/audio-1.ts",
                            "/video-1.ts", "/audio-1.ts"]
        assert sessions[0] == sessions[1] == proxy.sticky_session_id(None, "abcdefghijk")
        assert sessions[2] and sessions[2] != sessions[1]
        assert budget.total >= len(payload) * 5
        assert not list(tmp_path.glob(".parts-*"))
        for path, _ in outputs:
            media = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path),
            ]))
            assert {stream["codec_type"] for stream in media["streams"]} == {"audio", "video"}
            subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                           check=True, capture_output=True, timeout=30)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        set_config_override(None)
