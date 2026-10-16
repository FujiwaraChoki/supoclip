import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import youtube_utils
from src import ytdl_youtube_downloader as ytdl
from src.config import Config, set_config_override

VIDEO_ID = "abcdefghijk"
CONTENT = b"mp4-bytes" * 1000


class FakeService(ThreadingHTTPServer):
    """Speaks the ytdl API; ``script`` decides each response."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.polls_before_done = 1
        self.final = {"status": "completed", "result": {"size": len(CONTENT), "height": 1080,
                                                         "route": "proxy", "attempts": 1}}
        self.post_statuses = []
        self.file_statuses = []
        self.file_body = CONTENT


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        server: FakeService = self.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(("POST", self.path, body, self.headers.get("Authorization")))
        if server.post_statuses:
            return self._json(server.post_statuses.pop(0), {"detail": "busy"})
        server.polls = 0
        self._json(202, {"id": "job1", "status": "queued"})

    def do_GET(self):
        server: FakeService = self.server
        server.requests.append(("GET", self.path, None, self.headers.get("Authorization")))
        if self.path.endswith("/file"):
            status = server.file_statuses.pop(0) if server.file_statuses else 200
            if status != 200:
                return self._json(status, {"detail": "nope"})
            self.send_response(200)
            self.send_header("Content-Length", str(len(server.file_body)))
            self.end_headers()
            self.wfile.write(server.file_body)
            return
        server.polls += 1
        if server.polls < server.polls_before_done:
            return self._json(200, {"id": "job1", "status": "running"})
        self._json(200, {"id": "job1", **server.final})


@pytest.fixture
def service():
    server = FakeService()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture
def config(tmp_path, service, monkeypatch):
    monkeypatch.setattr(ytdl, "POLL_INTERVAL", 0.01)
    value = Config()
    value.temp_dir = str(tmp_path)
    value.ytdl_api_url = f"http://127.0.0.1:{service.server_address[1]}"
    value.ytdl_api_token = "service-token"
    value.ytdl_video_quality = 1080
    value.ytdl_download_timeout_seconds = 30
    value.max_video_upload_bytes = 10_000_000
    value.youtube_download_provider = "ytdl"
    value.youtube_download_fallback_provider = None
    set_config_override(value)
    yield value
    set_config_override(None)


def test_downloads_file_with_auth_and_limits(config, service, tmp_path):
    service.polls_before_done = 3
    path = ytdl.download_video_via_ytdl(VIDEO_ID)
    assert path.read_bytes() == CONTENT
    assert path.name == f"{VIDEO_ID}.mp4" and path.parent.name.startswith(f"ytdl-{VIDEO_ID}-")
    method, route, body, auth = service.requests[0]
    assert (method, route) == ("POST", "/v1/downloads")
    assert body == {"video_id": VIDEO_ID, "max_height": 1080, "max_bytes": 10_000_000}
    assert all(r[3] == "Bearer service-token" for r in service.requests)
    youtube_utils.cleanup_downloaded_files(VIDEO_ID, path)
    assert not path.parent.exists()


def test_service_failure_message_reaches_the_user(config, service, tmp_path):
    service.final = {"status": "failed", "error_code": "private", "error": "This YouTube video is private."}
    with pytest.raises(ytdl.YtdlDownloadError) as info:
        ytdl.download_video_via_ytdl(VIDEO_ID)
    assert str(info.value) == "This YouTube video is private." and info.value.permanent
    assert not any(tmp_path.iterdir()), "work directory is removed on failure"


def test_rides_out_service_restart(config, service):
    service.post_statuses = [503, 502]
    assert ytdl.download_video_via_ytdl(VIDEO_ID).read_bytes() == CONTENT


def test_resubmits_when_file_expired(config, service):
    service.file_statuses = [410]
    assert ytdl.download_video_via_ytdl(VIDEO_ID).read_bytes() == CONTENT
    assert [r[0] for r in service.requests].count("POST") == 2


def test_truncated_transfer_is_rejected(config, service, tmp_path):
    service.final["result"]["size"] = len(CONTENT) + 5
    with pytest.raises(ytdl.YtdlDownloadError, match="incomplete"):
        ytdl.download_video_via_ytdl(VIDEO_ID)
    assert not any(tmp_path.iterdir())


def test_unconfigured_service_fails_safely(config):
    config.ytdl_api_token = None
    with pytest.raises(ytdl.YtdlDownloadError, match="not configured"):
        ytdl.download_video_via_ytdl(VIDEO_ID)


def test_dispatch_falls_back_to_paid_provider_only_when_it_can_help(config, service, monkeypatch, tmp_path):
    config.youtube_download_fallback_provider = "oxylabs"
    fallback_file = tmp_path / "oxy.mp4"
    calls = []
    monkeypatch.setattr(youtube_utils, "download_video_via_oxylabs",
                        lambda video_id: calls.append(video_id) or fallback_file)
    url = f"https://www.youtube.com/watch?v={VIDEO_ID}"

    service.final = {"status": "failed", "error_code": "bot_check", "error": "YouTube rate-limited the download."}
    assert youtube_utils.download_youtube_video(url) == fallback_file
    assert calls == [VIDEO_ID]

    service.final = {"status": "failed", "error_code": "private", "error": "This YouTube video is private."}
    with pytest.raises(ytdl.YtdlDownloadError):
        youtube_utils.download_youtube_video(url)
    assert calls == [VIDEO_ID]


def test_config_parses_ytdl_settings(monkeypatch):
    monkeypatch.setenv("YOUTUBE_DOWNLOAD_PROVIDER", "ytdl")
    monkeypatch.setenv("YOUTUBE_DOWNLOAD_FALLBACK_PROVIDER", "Oxylabs")
    monkeypatch.setenv("YTDL_VIDEO_QUALITY", "999")
    value = Config()
    assert value.youtube_download_provider == "ytdl"
    assert value.youtube_download_fallback_provider == "oxylabs"
    assert value.ytdl_video_quality == 1080



def test_service_cannot_exceed_backend_size_limit(config, service, tmp_path):
    service.final["result"]["size"] = config.max_video_upload_bytes + 1
    with pytest.raises(ytdl.YtdlDownloadError, match="size limit"):
        ytdl.download_video_via_ytdl(VIDEO_ID)
    assert not any(request[1].endswith("/file") for request in service.requests)
    assert not any(tmp_path.iterdir())
