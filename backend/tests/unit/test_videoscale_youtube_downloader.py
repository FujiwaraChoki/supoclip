import io
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from src import videoscale_youtube_downloader as vs
from src.config import Config, set_config_override

VIDEO_ID = "abcdefghijk"
SOURCE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SIGNED_URL = "http://s3.fr-par.scw.cloud/youtube-download/file.mp4?signature=private"


class Response:
    def __init__(self, payload=None, *, status=200, content=b"", headers=None):
        self.status_code = status
        self.payload = payload
        self.content = content
        self.headers = headers or {"Content-Length": str(len(content))}

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        del chunk_size
        yield self.content

    def close(self):
        pass


@pytest.fixture
def config(tmp_path):
    value = Config()
    value.temp_dir = str(tmp_path)
    value.videoscale_username = "user"
    value.videoscale_password = "very-private-password"
    value.videoscale_video_quality = 1080
    value.videoscale_download_timeout_seconds = 30
    value.videoscale_transfer_timeout_seconds = 30
    value.max_video_upload_bytes = 10000000
    set_config_override(value)
    yield value
    set_config_override(None)


@pytest.fixture
def media_bytes(tmp_path):
    video = tmp_path / "source-video.mp4"
    audio = tmp_path / "source-audio.m4a"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=1920x1080:r=25:d=2", "-an", "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-y", str(video)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-vn", "-c:a", "aac", "-y", str(audio)], check=True)
    return video.read_bytes(), audio.read_bytes()


def formats():
    return [
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
         "height": 1080, "width": 1920, "filesize": 600, "url": "https://youtube.example/v?dur=2"},
        {"format_id": "136", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none",
         "height": 720, "width": 1280, "filesize": 300, "url": "https://youtube.example/v?dur=2"},
        {"format_id": "140-dub", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
         "language_preference": -1, "language": "es", "abr": 160, "filesize": 100},
        {"format_id": "140-original", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
         "language_preference": 10, "format_note": "original", "abr": 128, "filesize": 100},
    ]


def test_selects_target_h264_and_original_audio_not_fixed_itag():
    video, audio = vs.select_formats(formats(), 720, 10000)
    assert video["format_id"] == "136"
    assert audio["format_id"] == "140-original"
    video, audio = vs.select_formats(formats(), 1080, 10000)
    assert video["format_id"] == "137"
    assert audio["format_id"] == "140-original"


def test_rejects_estimated_oversize_before_any_job():
    with pytest.raises(vs.VideoScaleDownloadError, match="size limit"):
        vs.select_formats(formats(), 1080, 699)


def test_new_intermediate_status_polls_same_job_without_resubmission(monkeypatch):
    job = "00000000-0000-0000-0000-000000000001"
    responses = iter([
        {"task_id": job}, {"status": "uploading"},
        {"status": "completed"}, {"download_url": SIGNED_URL},
    ])
    calls = []

    def request(session, method, path, deadline, **kwargs):
        calls.append((method, path))
        return next(responses)

    monkeypatch.setattr(vs, "_request", request)
    monkeypatch.setattr(vs.time, "sleep", lambda _: None)
    result = vs._submit_and_wait(object(), SOURCE_URL, "137", vs.time.monotonic() + 10)
    assert result.startswith("https://s3.fr-par.scw.cloud/")
    assert [method for method, _ in calls].count("POST") == 1
    assert calls[1] == calls[2] == ("GET", f"/api/status/{job}")


def test_complete_two_track_download_uses_tls_no_storage_auth(config, media_bytes, monkeypatch):
    request_calls, get_calls = [], []
    task_ids = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
    jobs = iter(task_ids)
    downloads = iter(media_bytes)

    class Session:
        def __init__(self):
            self.auth = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def request(self, method, url, **kwargs):
            request_calls.append((method, url, kwargs, self.auth))
            if url.endswith("/api/formats"):
                return Response(formats())
            if method == "POST":
                return Response({"task_id": next(jobs), "status": "queued"})
            if "/api/status/" in url:
                return Response({"status": "completed", "progress": 100})
            return Response({"download_url": SIGNED_URL})

        def get(self, url, **kwargs):
            get_calls.append((url, kwargs, self.auth, self.trust_env))
            data = next(downloads)
            return Response(content=data)

    monkeypatch.setattr(vs.requests, "Session", Session)
    path = vs.download_video_via_videoscale(SOURCE_URL + "&list=untrusted", VIDEO_ID, expected_duration=2)
    assert path.exists()
    streams, duration, dimensions, stream_durations = vs._probe(path, vs.time.monotonic() + 10)
    assert streams == {"video", "audio"}
    assert abs(duration - 2) < 0.1
    assert dimensions == (1920, 1080)
    assert all(abs(value - 2) < 0.1 for value in stream_durations.values())
    assert [c[2]["json"]["format_id"] for c in request_calls if c[0] == "POST"] == ["137", "140-original"]
    assert all(c[2]["json"]["url"] == SOURCE_URL for c in request_calls if c[0] == "POST")
    assert request_calls[0][2]["params"]["video_url"] == SOURCE_URL
    assert request_calls[0][2]["timeout"][1] > 30
    assert all(call[0].startswith("https://s3.fr-par.scw.cloud/youtube-download/") for call in get_calls)
    assert all(call[2] is None and call[1]["allow_redirects"] is False for call in get_calls)
    assert all(call[3] is False for call in get_calls)
    assert len(list(path.parent.iterdir())) == 1


def test_post_timeout_never_repeats_paid_job_or_leaks_secret(config, monkeypatch):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.request.side_effect = [Response(formats()), requests.Timeout("very-private-password")]
    monkeypatch.setattr(vs.requests, "Session", lambda: session)
    with pytest.raises(vs.VideoScaleDownloadError) as error:
        vs.download_video_via_videoscale(SOURCE_URL, VIDEO_ID)
    assert "very-private-password" not in str(error.value)
    assert sum(call.args[0] == "POST" for call in session.request.call_args_list) == 1


def test_poll_transient_errors_reuse_same_job(config, monkeypatch):
    task_id = "00000000-0000-0000-0000-000000000001"
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.request.side_effect = [Response(formats()), Response({"task_id": task_id}),
                                   requests.Timeout("secret"), Response(status=429),
                                   Response(status=503), Response({"status": "failed"})]
    monkeypatch.setattr(vs.requests, "Session", lambda: session)
    monkeypatch.setattr(vs.time, "sleep", lambda _: None)
    with pytest.raises(vs.VideoScaleDownloadError, match="failed at the provider"):
        vs.download_video_via_videoscale(SOURCE_URL, VIDEO_ID)
    assert [c.args[0] for c in session.request.call_args_list] == ["GET", "POST", "GET", "GET", "GET", "GET"]


@pytest.mark.parametrize("url", [
    "http://evil.test/youtube-download/file.mp4",
    "https://s3.fr-par.scw.cloud.evil.test/youtube-download/file.mp4",
    "https://user:pass@s3.fr-par.scw.cloud/youtube-download/file.mp4",
    "https://s3.fr-par.scw.cloud/other/file.mp4",
    "https://s3.fr-par.scw.cloud:443/youtube-download/file.mp4",
])
def test_rejects_untrusted_output_url(url):
    with pytest.raises(vs.VideoScaleDownloadError, match="invalid file URL"):
        vs._safe_download_url(url)


def test_missing_credentials_stops_before_api(config, monkeypatch):
    config.videoscale_password = None
    make_session = Mock(side_effect=AssertionError("network reached"))
    monkeypatch.setattr(vs.requests, "Session", make_session)
    with pytest.raises(vs.VideoScaleDownloadError, match="credentials"):
        vs.download_video_via_videoscale(SOURCE_URL, VIDEO_ID)
    make_session.assert_not_called()


def test_incomplete_transfer_removes_partial_file(config, monkeypatch):
    class BrokenSession:
        def get(self, *args, **kwargs):
            return Response(content=b"short", headers={"Content-Length": "100"})

    target = Path(config.temp_dir) / "partial.mp4"
    with pytest.raises(vs.VideoScaleDownloadError, match="incomplete"):
        vs._transfer(BrokenSession(), vs._safe_download_url(SIGNED_URL), target,
                     vs.time.monotonic() + 10, 1000)
    assert not target.exists()
    assert not target.with_suffix(".mp4.part").exists()


def test_rejects_truncated_video_duration(config, media_bytes, monkeypatch):
    # The actual fixture is two seconds; a ten-minute source claim must fail.
    video, audio = media_bytes
    assert not vs._duration_matches(2, 600)
