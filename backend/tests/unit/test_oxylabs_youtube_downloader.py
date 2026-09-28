import io
from unittest.mock import Mock

import pytest
import requests

from src import oxylabs_youtube_downloader as oxy
from src.config import Config, set_config_override
from src.youtube_utils import download_youtube_video


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = Config()
    config.youtube_download_provider = "oxylabs"
    config.oxylabs_username = "user"
    config.oxylabs_password = "private-password"
    config.oxylabs_storage_bucket = "test-bucket"
    config.oxylabs_storage_prefix = "youtube"
    config.temp_dir = str(tmp_path)
    set_config_override(config)
    client = Mock()
    monkeypatch.setattr(oxy.boto3, "client", lambda *a, **kw: client)
    monkeypatch.setattr(oxy.time, "sleep", lambda n: None)
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(oxy.requests, "Session", lambda: session)
    monkeypatch.setattr(oxy, "_streams", lambda *a: {"audio", "video"})
    client.get_object.return_value = {"ContentLength": 5, "Body": io.BytesIO(b"video")}
    yield config, client, session, tmp_path
    set_config_override(None)


def response(status="done", **extra):
    r = Mock(status_code=200)
    r.json.return_value = {"id": "job123", "status": status, **extra}
    return r


def test_download_polls_https_and_returns_private_local_file(setup):
    config, client, session, tmp = setup
    session.request.side_effect = [response("pending", _links=[{"href": "http://evil.test"}]), response()]
    path = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")
    assert path.read_bytes() == b"video"
    assert path.parent.parent == tmp
    assert session.request.call_count == 2
    assert session.request.call_args_list[1].args[:2] == ("GET", oxy.API_URL + "/job123")
    submitted = session.request.call_args_list[0].kwargs["json"]
    assert submitted["storage_url"] == "test-bucket/youtube/"
    assert {"key": "download_type", "value": "audio_video"} in submitted["context"]
    client.get_object.assert_called_once_with(Bucket="test-bucket", Key="youtube/abcdefghijk_job123.mp4")


def test_failure_does_not_fallback_or_repeat_paid_submission(setup, monkeypatch):
    config, client, session, tmp = setup
    session.request.return_value = response("faulted", error="private-password")
    fallback = Mock(side_effect=AssertionError("must not use Apify"))
    monkeypatch.setattr("src.youtube_utils.download_youtube_video_with_apify", fallback)
    with pytest.raises(oxy.OxylabsDownloadError, match="failed at the provider") as error:
        download_youtube_video("https://youtu.be/abcdefghijk")
    assert "private-password" not in str(error.value)
    assert session.request.call_count == 1
    fallback.assert_not_called()


def test_submit_timeout_is_not_retried(setup):
    _, _, session, _ = setup
    session.request.side_effect = requests.Timeout("contains secret")
    with pytest.raises(oxy.OxylabsDownloadError, match="could not be reached"):
        oxy.download_video_via_oxylabs("abcdefghijk")
    assert session.request.call_count == 1


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_api_errors_are_safe(setup, status):
    _, _, session, _ = setup
    session.request.return_value = Mock(status_code=status, text="private-password")
    with pytest.raises(oxy.OxylabsDownloadError, match=f"HTTP {status}") as error:
        oxy.download_video_via_oxylabs("abcdefghijk")
    assert "private-password" not in str(error.value)


def test_missing_credentials_stops_before_submission(setup):
    config, _, session, _ = setup
    config.oxylabs_password = None
    with pytest.raises(oxy.OxylabsDownloadError, match="credentials"):
        oxy.download_video_via_oxylabs("abcdefghijk")
    session.request.assert_not_called()


def test_storage_read_failure_stops_before_submission(setup):
    _, client, session, _ = setup
    client.head_bucket.side_effect = RuntimeError("secret")
    with pytest.raises(oxy.OxylabsDownloadError):
        oxy.download_video_via_oxylabs("abcdefghijk")
    session.request.assert_not_called()


@pytest.mark.parametrize("length,limit", [(0, 10), (50, 10), (10, 50)])
def test_empty_large_or_incomplete_files_are_removed(setup, length, limit):
    config, client, session, tmp = setup
    config.max_video_upload_bytes = limit
    client.get_object.return_value = {"ContentLength": length, "Body": io.BytesIO(b"video")}
    session.request.return_value = response()
    with pytest.raises(oxy.OxylabsDownloadError):
        oxy.download_video_via_oxylabs("abcdefghijk")
    assert list(tmp.iterdir()) == []


def test_missing_audio_is_fetched_and_muxed(setup, monkeypatch):
    _, client, session, _ = setup
    session.request.return_value = response()
    client.get_object.side_effect = [
        {"ContentLength": 5, "Body": io.BytesIO(b"video")},
        {"ContentLength": 5, "Body": io.BytesIO(b"audio")},
    ]
    streams = iter([{"video"}, {"audio", "video"}])
    monkeypatch.setattr(oxy, "_streams", lambda *a: next(streams))
    def merge(args, **kwargs):
        from pathlib import Path
        Path(args[-1]).write_bytes(b"merged")
    monkeypatch.setattr(oxy.subprocess, "run", merge)
    path = oxy.download_video_via_oxylabs("abcdefghijk")
    assert path.read_bytes() == b"merged"
    assert len(list(path.parent.iterdir())) == 1


def test_storage_credentials_encoded_and_https_required(setup):
    config, _, _, _ = setup
    config.oxylabs_storage_endpoint = "https://storage.example.com"
    config.oxylabs_storage_write_key = "writer"
    config.oxylabs_storage_write_secret = "secret/@+#"
    _, kind, url, _, _ = oxy._storage(config)
    assert kind == "s3_compatible"
    assert "secret%2F%40%2B%23@" in url
    config.oxylabs_storage_endpoint = "http://storage.example.com"
    with pytest.raises(oxy.OxylabsDownloadError, match="secure"):
        oxy._storage(config)


def test_config_accepts_oxylabs(monkeypatch):
    monkeypatch.setenv("YOUTUBE_DOWNLOAD_PROVIDER", "oxylabs")
    assert Config().youtube_download_provider == "oxylabs"


def test_poll_network_error_reuses_job(setup):
    _, _, session, _ = setup
    session.request.side_effect = [response("pending"), requests.Timeout("secret"), response()]
    assert oxy.download_video_via_oxylabs("abcdefghijk").exists()
    assert [c.args[0] for c in session.request.call_args_list] == ["POST", "GET", "GET"]


def test_download_deadline_is_bounded(setup, monkeypatch):
    config, _, session, _ = setup
    config.oxylabs_download_timeout_seconds = 10
    session.request.return_value = response("pending")
    ticks = iter([0, 1, 11])
    monkeypatch.setattr(oxy.time, "monotonic", lambda: next(ticks))
    with pytest.raises(oxy.OxylabsDownloadError, match="timed out"):
        oxy.download_video_via_oxylabs("abcdefghijk")
    assert session.request.call_count == 1


def test_cleanup_only_removes_this_downloads_media(setup):
    from src.youtube_utils import cleanup_downloaded_files
    _, _, session, tmp = setup
    session.request.return_value = response()
    path = oxy.download_video_via_oxylabs("abcdefghijk")
    cache = path.with_suffix(".transcript_cache.json")
    cache.write_text("{}")
    other = tmp / "abcdefghijk.mp4"
    other.write_bytes(b"other-job")
    cleanup_downloaded_files("abcdefghijk", source_path=path)
    assert not path.exists()
    assert cache.exists()
    assert other.exists()
