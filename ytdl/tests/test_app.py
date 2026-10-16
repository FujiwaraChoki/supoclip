import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from supoclip_ytdl import jobs
from supoclip_ytdl.app import create_app, parse_video_id
from supoclip_ytdl.config import Route, Settings
from supoclip_ytdl.downloader import Result
from supoclip_ytdl.errors import DownloadFailed, Failure

AUTH = {"authorization": "Bearer token"}


@pytest.mark.parametrize("value, expected", [
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://evil.com/watch?v=dQw4w9WgXcQ", None),
    ("https://www.youtube.com/watch?v=short", None),
    ("https://www.youtube.com/@channel", None),
])
def test_parse_video_id(value, expected):
    assert parse_video_id(value) == expected


@pytest.fixture
def fake_download(monkeypatch):
    calls = []
    release = threading.Event()
    release.set()

    def download(video_id, max_height, max_bytes, settings, router, progress, work):
        calls.append(video_id)
        release.wait(5)
        if video_id == "privateVid1":
            raise DownloadFailed(Failure("private", "fatal", "This YouTube video is private."))
        work.mkdir(parents=True, exist_ok=True)
        path = work / f"{video_id}.mp4"
        path.write_bytes(b"video-bytes")
        return Result(path=path, size=11, title="Title", duration=12.0, width=1280, height=720,
                      format_id="136+140", route="direct", attempts=1, transferred_bytes=11)

    monkeypatch.setattr(jobs, "download", download)
    return calls, release


def make_client(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, api_token="token", routes=(Route("direct"),),
                        update_check_hours=0, **overrides)
    return TestClient(create_app(settings))


def wait(client, job_id):
    for _ in range(100):
        body = client.get(f"/v1/downloads/{job_id}", headers=AUTH).json()
        if body["status"] not in {"queued", "running"}:
            return body
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_requires_token(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ"}).status_code == 401
        assert client.get("/v1/stats", headers={"authorization": "Bearer nope"}).status_code == 401


def test_download_lifecycle_and_file(tmp_path, fake_download):
    with make_client(tmp_path) as client:
        created = client.post("/v1/downloads", json={"url": "https://youtu.be/dQw4w9WgXcQ"}, headers=AUTH)
        assert created.status_code == 202
        body = wait(client, created.json()["id"])
        assert body["status"] == "completed"
        assert body["result"]["height"] == 720 and body["result"]["size"] == 11
        file = client.get(f"/v1/downloads/{body['id']}/file", headers=AUTH)
        assert file.status_code == 200 and file.content == b"video-bytes"
        ranged = client.get(f"/v1/downloads/{body['id']}/file", headers={**AUTH, "range": "bytes=6-"})
        assert ranged.status_code == 206 and ranged.content == b"bytes"
        # The work directory is cleaned up after the result moves into the cache.
        assert not any((tmp_path / "work").iterdir())


def test_concurrent_requests_share_one_download(tmp_path, fake_download):
    calls, release = fake_download
    release.clear()
    with make_client(tmp_path) as client:
        first = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ"}, headers=AUTH).json()
        second = client.post("/v1/downloads", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                             headers=AUTH).json()
        assert first["id"] == second["id"]
        release.set()
        wait(client, first["id"])
        # A different quality is a different download.
        other = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ", "max_height": 720},
                            headers=AUTH).json()
        wait(client, other["id"])
    assert calls == ["dQw4w9WgXcQ", "dQw4w9WgXcQ"]


def test_disk_cache_survives_restart(tmp_path, fake_download):
    calls, _ = fake_download
    with make_client(tmp_path) as client:
        job = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ"}, headers=AUTH).json()
        wait(client, job["id"])
    with make_client(tmp_path) as client:
        again = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ"}, headers=AUTH)
        assert again.status_code == 200
        assert again.json()["cached"] is True and again.json()["result"]["title"] == "Title"
        assert client.get(f"/v1/downloads/{again.json()['id']}/file", headers=AUTH).content == b"video-bytes"
    assert calls == ["dQw4w9WgXcQ"]


def test_cache_respects_ttl_and_size_limit(tmp_path, fake_download):
    calls, _ = fake_download
    with make_client(tmp_path) as client:
        job = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ"}, headers=AUTH).json()
        wait(client, job["id"])
    entry = tmp_path / "cache" / jobs.cache_key("dQw4w9WgXcQ", 1080, 12_000_000_000)
    old = time.time() - 3 * 86400
    os.utime(entry, (old, old))
    with make_client(tmp_path) as client:
        too_small = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ", "max_bytes": 5},
                                headers=AUTH).json()
        assert too_small["cached"] is False
        client.app.state.manager.sweep()
    assert len(calls) == 2


def test_failed_download_reports_safe_error(tmp_path, fake_download):
    with make_client(tmp_path) as client:
        job = client.post("/v1/downloads", json={"video_id": "privateVid1"}, headers=AUTH).json()
        body = wait(client, job["id"])
        assert body["status"] == "failed" and body["error_code"] == "private"
        assert client.get(f"/v1/downloads/{job['id']}/file", headers=AUTH).status_code == 409
        # A failed job does not poison later submissions.
        retry = client.post("/v1/downloads", json={"video_id": "privateVid1"}, headers=AUTH).json()
        assert retry["id"] != job["id"]


def test_expired_file_returns_gone(tmp_path, fake_download):
    with make_client(tmp_path) as client:
        job = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ"}, headers=AUTH).json()
        wait(client, job["id"])
        (tmp_path / "cache" / jobs.cache_key("dQw4w9WgXcQ", 1080, 12_000_000_000) / "video.mp4").unlink()
        assert client.get(f"/v1/downloads/{job['id']}/file", headers=AUTH).status_code == 410


def test_draining_rejects_new_work(tmp_path):
    with make_client(tmp_path) as client:
        client.app.state.manager.draining = True
        response = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ"}, headers=AUTH)
        assert response.status_code == 503 and response.headers["retry-after"] == "30"


def test_stats_never_leak_proxy_credentials(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token="token", update_check_hours=0,
                        routes=(Route("res", "http://user-{session}:hunter2@gate:1"),))
    with TestClient(create_app(settings)) as client:
        router = client.app.state.router
        router.record(settings.routes[0], "failed",
                      error="Unable to connect to proxy http://user-abc:hunter2@gate:1")
        text = json.dumps(client.get("/v1/stats", headers=AUTH).json())
    assert "hunter2" not in text



@pytest.mark.parametrize("first_limit,second_limit", [(100, 10), (10, 100)])
def test_different_byte_limits_use_independent_jobs(tmp_path, fake_download, first_limit, second_limit):
    calls, release = fake_download
    release.clear()
    with make_client(tmp_path) as client:
        first = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ", "max_bytes": first_limit}, headers=AUTH).json()
        second = client.post("/v1/downloads", json={"video_id": "dQw4w9WgXcQ", "max_bytes": second_limit}, headers=AUTH).json()
        assert first["id"] != second["id"]
        release.set()
        small = first if first_limit == 10 else second
        large = first if first_limit == 100 else second
        assert wait(client, small["id"])["error_code"] == "too_large"
        assert wait(client, large["id"])["status"] == "completed"
