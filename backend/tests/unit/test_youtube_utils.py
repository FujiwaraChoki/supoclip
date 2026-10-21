import pytest
import requests

from src.apify_youtube_downloader import ApifyDownloadError
from src.config import Config, set_config_override
from src.youtube_utils import (
    YOUTUBE_DOWNLOAD_PROVIDER_APIFY,
    YOUTUBE_DOWNLOAD_PROVIDER_YTDLP,
    YOUTUBE_METADATA_PROVIDER_DATA_API,
    YOUTUBE_METADATA_PROVIDER_YTDLP,
    _fetch_video_info_with_youtube_data_api,
    _parse_iso8601_duration_to_seconds,
    _pick_best_thumbnail,
    download_youtube_video,
    download_youtube_video_with_apify,
    get_youtube_video_info,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


def test_download_youtube_video_prefers_ytdlp_by_default(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"

    ytdlp_path = tmp_path / "abcdefghijk.mp4"
    ytdlp_path.write_bytes(b"video")

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._download_youtube_video_with_ytdlp",
            lambda *args, **kwargs: ytdlp_path,
        )
        monkeypatch.setattr(
            "src.youtube_utils.download_youtube_video_with_apify",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Apify should not be used")),
        )

        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == ytdlp_path
    finally:
        set_config_override(None)


def test_download_youtube_video_uses_apify_when_configured(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"
    config.apify_youtube_default_quality = "720"
    config.youtube_download_provider = YOUTUBE_DOWNLOAD_PROVIDER_APIFY

    captured = {}

    def fake_apify(url: str, video_id: str, quality=None):
        captured["url"] = url
        captured["video_id"] = video_id
        captured["quality"] = quality
        target = tmp_path / f"{video_id}.mp4"
        target.write_bytes(b"video")
        return target

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils.download_youtube_video_with_apify", fake_apify)
        monkeypatch.setattr(
            "src.youtube_utils._download_youtube_video_with_ytdlp",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("yt-dlp should not be used")),
        )

        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")

        target = tmp_path / "abcdefghijk.mp4"
        assert result == target
        assert captured == {
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "video_id": "abcdefghijk",
            "quality": None,
        }

        download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk", quality="720")
        assert captured["quality"] == "720"
    finally:
        set_config_override(None)


def test_download_youtube_video_falls_back_when_token_missing(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = None

    fallback_path = tmp_path / "abcdefghijk.mp4"
    fallback_path.write_bytes(b"fallback")

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._download_youtube_video_with_ytdlp",
            lambda *args, **kwargs: fallback_path,
        )

        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == fallback_path
    finally:
        set_config_override(None)


def test_download_youtube_video_falls_back_to_apify_when_ytdlp_fails(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"
    config.youtube_download_provider = YOUTUBE_DOWNLOAD_PROVIDER_YTDLP

    apify_path = tmp_path / "abcdefghijk.mp4"

    def fake_apify(*args, **kwargs):
        apify_path.write_bytes(b"fallback")
        return apify_path

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._download_youtube_video_with_ytdlp",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "src.youtube_utils.download_youtube_video_with_apify",
            fake_apify,
        )

        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == apify_path
    finally:
        set_config_override(None)


def test_download_youtube_video_falls_back_to_ytdlp_when_apify_fails(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"
    config.youtube_download_provider = YOUTUBE_DOWNLOAD_PROVIDER_APIFY

    fallback_path = tmp_path / "abcdefghijk.webm"
    fallback_path.write_bytes(b"fallback")

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils.download_youtube_video_with_apify",
            lambda *args, **kwargs: (_ for _ in ()).throw(ApifyDownloadError("boom")),
        )
        monkeypatch.setattr(
            "src.youtube_utils._download_youtube_video_with_ytdlp",
            lambda *args, **kwargs: fallback_path,
        )

        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == fallback_path
    finally:
        set_config_override(None)


def test_download_youtube_video_uses_configured_quality(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"
    config.apify_youtube_default_quality = "720"
    config.youtube_download_provider = YOUTUBE_DOWNLOAD_PROVIDER_APIFY

    captured = {}

    def fake_download_video_via_apify(**kwargs):
        captured.update(kwargs)
        target = tmp_path / "abcdefghijk.mp4"
        target.write_bytes(b"video")
        return target

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils.download_video_via_apify", fake_download_video_via_apify)

        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == tmp_path / "abcdefghijk.mp4"
        assert captured["quality"] == "720"
        assert captured["api_token"] == "apify-token"
    finally:
        set_config_override(None)


def test_config_invalid_apify_quality_defaults_to_1080(monkeypatch):
    monkeypatch.setenv("APIFY_YOUTUBE_DEFAULT_QUALITY", "bad-value")

    config = Config()

    assert config.apify_youtube_default_quality == "1080"


def test_config_invalid_youtube_download_provider_defaults_to_ytdlp(monkeypatch):
    monkeypatch.setenv("YOUTUBE_DOWNLOAD_PROVIDER", "bad-value")

    config = Config()

    assert config.youtube_download_provider == YOUTUBE_DOWNLOAD_PROVIDER_YTDLP


def test_config_youtube_download_provider_allows_apify(monkeypatch):
    monkeypatch.setenv("YOUTUBE_DOWNLOAD_PROVIDER", "apify")

    config = Config()

    assert config.youtube_download_provider == YOUTUBE_DOWNLOAD_PROVIDER_APIFY


def test_config_invalid_youtube_metadata_provider_defaults_to_ytdlp(monkeypatch):
    monkeypatch.setenv("YOUTUBE_METADATA_PROVIDER", "bad-value")

    config = Config()

    assert config.youtube_metadata_provider == YOUTUBE_METADATA_PROVIDER_YTDLP


def test_parse_iso8601_duration_to_seconds():
    assert _parse_iso8601_duration_to_seconds("PT45S") == 45
    assert _parse_iso8601_duration_to_seconds("PT12M34S") == 754
    assert _parse_iso8601_duration_to_seconds("PT1H02M03S") == 3723


def test_pick_best_thumbnail_prefers_highest_resolution():
    thumbnails = {
        "default": {"url": "https://example.com/default.jpg"},
        "high": {"url": "https://example.com/high.jpg"},
        "maxres": {"url": "https://example.com/maxres.jpg"},
    }

    assert _pick_best_thumbnail(thumbnails) == "https://example.com/maxres.jpg"


def test_fetch_video_info_with_youtube_data_api_normalizes_response(monkeypatch):
    config = Config()
    config.youtube_data_api_key = "youtube-key"
    config.google_api_key = "google-key"

    def fake_get(url, params=None, timeout=None):
        assert params["key"] == "youtube-key"
        assert params["id"] == "abcdefghijk"
        assert params["part"] == "snippet,contentDetails,statistics"
        assert "fields" in params
        assert timeout == (10, 30)
        return FakeResponse(
            {
                "items": [
                    {
                        "id": "abcdefghijk",
                        "snippet": {
                            "title": "Test title",
                            "description": "Test description",
                            "channelTitle": "Test channel",
                            "publishedAt": "2024-01-02T03:04:05Z",
                            "thumbnails": {
                                "default": {"url": "https://example.com/default.jpg"},
                                "maxres": {"url": "https://example.com/maxres.jpg"},
                            },
                        },
                        "contentDetails": {"duration": "PT1H02M03S"},
                        "statistics": {"viewCount": "12345", "likeCount": "678"},
                    }
                ]
            }
        )

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils.requests.get", fake_get)

        result = _fetch_video_info_with_youtube_data_api(
            "https://www.youtube.com/watch?v=abcdefghijk"
        )

        assert result == {
            "id": "abcdefghijk",
            "title": "Test title",
            "description": "Test description",
            "duration": 3723,
            "uploader": "Test channel",
            "upload_date": "20240102",
            "view_count": 12345,
            "like_count": 678,
            "thumbnail": "https://example.com/maxres.jpg",
            "format_id": None,
            "resolution": None,
            "fps": None,
            "filesize": None,
        }
    finally:
        set_config_override(None)


def test_fetch_video_info_with_youtube_data_api_falls_back_to_google_key(monkeypatch):
    config = Config()
    config.youtube_data_api_key = None
    config.google_api_key = "google-key"

    captured = {}

    def fake_get(_url, params=None, timeout=None):
        captured["key"] = params["key"]
        return FakeResponse(
            {
                "items": [
                    {
                        "id": "abcdefghijk",
                        "snippet": {"title": "Test", "description": "", "thumbnails": {}},
                        "contentDetails": {"duration": "PT45S"},
                        "statistics": {},
                    }
                ]
            }
        )

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils.requests.get", fake_get)

        result = _fetch_video_info_with_youtube_data_api(
            "https://www.youtube.com/watch?v=abcdefghijk"
        )

        assert captured["key"] == "google-key"
        assert result["duration"] == 45
    finally:
        set_config_override(None)


def test_get_youtube_video_info_uses_ytdlp_by_default(monkeypatch):
    config = Config()
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_ytdlp",
            lambda _url: {"title": "From yt-dlp"},
        )
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_youtube_data_api",
            lambda _url: (_ for _ in ()).throw(AssertionError("fallback should not run")),
        )

        result = get_youtube_video_info("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == {"title": "From yt-dlp"}
    finally:
        set_config_override(None)


def test_get_youtube_video_info_uses_youtube_data_api_when_selected(monkeypatch):
    config = Config()
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_youtube_data_api",
            lambda _url: {"title": "From API"},
        )
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_ytdlp",
            lambda _url: (_ for _ in ()).throw(AssertionError("fallback should not run")),
        )

        result = get_youtube_video_info("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == {"title": "From API"}
    finally:
        set_config_override(None)


def test_get_youtube_video_info_falls_back_to_ytdlp_on_data_api_failure(monkeypatch):
    config = Config()
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_youtube_data_api",
            lambda _url: (_ for _ in ()).throw(ValueError("boom")),
        )
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_ytdlp",
            lambda _url: {"title": "Fallback yt-dlp"},
        )

        result = get_youtube_video_info("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == {"title": "Fallback yt-dlp"}
    finally:
        set_config_override(None)


def test_get_youtube_video_info_falls_back_to_data_api_on_ytdlp_failure(monkeypatch):
    config = Config()
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_ytdlp",
            lambda _url: (_ for _ in ()).throw(ValueError("boom")),
        )
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_youtube_data_api",
            lambda _url: {"title": "Fallback API"},
        )

        result = get_youtube_video_info("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == {"title": "Fallback API"}
    finally:
        set_config_override(None)


def test_get_youtube_video_info_falls_back_when_data_api_returns_empty_items(monkeypatch):
    config = Config()
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API
    config.youtube_data_api_key = "youtube-key"

    def fake_get(_url, params=None, timeout=None):
        return FakeResponse({"items": []})

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils.requests.get", fake_get)
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_ytdlp",
            lambda _url: {"title": "Fallback yt-dlp"},
        )

        result = get_youtube_video_info("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == {"title": "Fallback yt-dlp"}
    finally:
        set_config_override(None)


def test_get_youtube_video_info_falls_back_when_data_api_http_errors(monkeypatch):
    config = Config()
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API
    config.youtube_data_api_key = "youtube-key"

    def fake_get(_url, params=None, timeout=None):
        return FakeResponse({}, status_code=500)

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils.requests.get", fake_get)
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_ytdlp",
            lambda _url: {"title": "Fallback yt-dlp"},
        )

        result = get_youtube_video_info("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == {"title": "Fallback yt-dlp"}
    finally:
        set_config_override(None)


def test_get_youtube_video_info_falls_back_to_ytdlp_when_no_data_api_key(monkeypatch):
    config = Config()
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API
    config.youtube_data_api_key = None
    config.google_api_key = None

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils._fetch_video_info_with_ytdlp",
            lambda _url: {"title": "Fallback yt-dlp"},
        )

        result = get_youtube_video_info("https://www.youtube.com/watch?v=abcdefghijk")

        assert result == {"title": "Fallback yt-dlp"}
    finally:
        set_config_override(None)


def test_cleanup_keeps_word_timings_for_cached_reprocessing(monkeypatch, tmp_path):
    from src.youtube_utils import cleanup_downloaded_files
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.youtube_utils.get_config", lambda: config)
    for name in ["abcdefghijk.mp4", "abcdefghijk.transcription.mp3", "abcdefghijk.mp4.part", "abcdefghijk.transcript_cache.json"]:
        (tmp_path / name).write_text("fixture")
    cleanup_downloaded_files("abcdefghijk")
    assert [p.name for p in tmp_path.iterdir()] == ["abcdefghijk.transcript_cache.json"]


@pytest.mark.parametrize(
    ("duration", "expected_quality"),
    [(1200, "1440"), (5196, "1080"), (None, "1440")],
)
def test_download_youtube_video_with_apify_caps_quality_for_long_videos(
    tmp_path, monkeypatch, duration, expected_quality
):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"
    config.apify_youtube_default_quality = "1440"
    config.apify_long_video_seconds = 1800

    captured = {}

    def fake_download_via_apify(**kwargs):
        captured.update(kwargs)
        return tmp_path / "abcdefghijk.mp4"

    set_config_override(config)
    try:
        monkeypatch.setattr(
            "src.youtube_utils.get_youtube_video_info",
            lambda url: {"duration": duration} if duration else None,
        )
        monkeypatch.setattr(
            "src.youtube_utils.download_video_via_apify", fake_download_via_apify
        )

        download_youtube_video_with_apify(
            "https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk"
        )

        assert captured["quality"] == expected_quality
    finally:
        set_config_override(None)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("apify", "apify"), (" APIFY ", "apify"), ("rapidapi", "rapidapi"),
     (" RAPIDAPI ", "rapidapi"), ("yt-dlp", "yt_dlp"),
     ("yt_dlp", "yt_dlp"), ("unsupported", "yt_dlp"), (None, "yt_dlp")],
)
def test_download_provider_normalization(configured, expected):
    assert Config._normalize_youtube_download_provider(configured) == expected


@pytest.mark.parametrize("permanent", [False, True])
def test_rapidapi_fallback_after_apify_failure(tmp_path, monkeypatch, permanent):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_download_provider = "apify"
    config.apify_api_token = "test-token"
    config.rapidapi_youtube_api_key = "test-key"
    calls = []
    def apify(*args):
        calls.append("apify")
        raise ApifyDownloadError("unavailable", permanent=permanent)
    def rapidapi(*args, **kwargs):
        calls.append("rapidapi")
        return tmp_path / "fallback.mp4"
    monkeypatch.setattr("src.youtube_utils.download_youtube_video_with_apify", apify)
    monkeypatch.setattr("src.youtube_utils.download_video_via_rapidapi", rapidapi)
    monkeypatch.setattr("src.youtube_utils._resolve_apify_quality", lambda *a: "1080")
    monkeypatch.setattr("src.youtube_utils._download_youtube_video_with_ytdlp",
                        lambda *a: pytest.fail("Direct downloader should not run"))
    set_config_override(config)
    try:
        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")
        assert calls == (["apify"] if permanent else ["apify", "rapidapi"])
        assert result == (None if permanent else tmp_path / "fallback.mp4")
    finally:
        set_config_override(None)


@pytest.mark.parametrize("default,requested,expected", [
    ("1080", None, "1080"),
    ("1080", "720", "720"),
    # A plan cap never raises the quality above the configured default.
    ("480", "720", "480"),
])
def test_apify_quality_follows_the_plan_cap(default, requested, expected):
    from src.youtube_utils import _resolve_apify_quality

    config = Config()
    config.apify_youtube_default_quality = default
    set_config_override(config)
    try:
        assert _resolve_apify_quality("https://www.youtube.com/watch?v=abcdefghijk", requested) == expected
    finally:
        set_config_override(None)


def test_cleanup_removes_completed_rapidapi_directory(tmp_path, monkeypatch):
    from src.youtube_utils import cleanup_downloaded_files
    monkeypatch.setattr("src.youtube_utils.get_config", lambda: type("Config", (), {"temp_dir": str(tmp_path)})())
    folder = tmp_path / "rapidapi-abcdefghijk-unique"
    folder.mkdir()
    source = folder / "abcdefghijk.mp4"
    source.write_bytes(b"video")
    cleanup_downloaded_files("abcdefghijk", source)
    assert not folder.exists()


@pytest.mark.parametrize('router_outcome', ['success', 'failed', 'missing'])
@pytest.mark.parametrize('apify_succeeds', [True, False])
def test_router_primary_leaves_ytdlp_until_all_paid_options_fail(
    tmp_path, monkeypatch, router_outcome, apify_succeeds
):
    from src.rapidapi_youtube_downloader import RapidAPIDownloadError
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_download_provider = 'rapidapi'
    config.rapidapi_youtube_api_key = '' if router_outcome == 'missing' else 'test-key'
    config.apify_api_token = 'test-token'
    calls = []
    target = tmp_path / 'complete.mp4'
    target.write_bytes(b'video')
    def router(*args, **kwargs):
        calls.append('rapidapi')
        if router_outcome == 'failed':
            raise RapidAPIDownloadError('HTTP 302')
        return target
    def apify(*args, **kwargs):
        calls.append('apify-pool')
        if not apify_succeeds:
            raise ApifyDownloadError('all configured actors failed')
        return target
    def ytdlp(*args, **kwargs):
        calls.append('yt_dlp')
        return target
    monkeypatch.setattr('src.youtube_utils.download_video_via_rapidapi', router)
    monkeypatch.setattr('src.youtube_utils.download_youtube_video_with_apify', apify)
    monkeypatch.setattr('src.youtube_utils._download_youtube_video_with_ytdlp', ytdlp)
    monkeypatch.setattr('src.youtube_utils._resolve_apify_quality', lambda *a: '1080')
    monkeypatch.setattr('src.youtube_utils._get_local_video_dimensions', lambda *a: (1920, 1080))
    set_config_override(config)
    try:
        assert download_youtube_video('https://www.youtube.com/watch?v=abcdefghijk') == target
        expected = [] if router_outcome == 'missing' else ['rapidapi']
        if router_outcome != 'success':
            expected.append('apify-pool')
            if not apify_succeeds:
                expected.append('yt_dlp')
        assert calls == expected
    finally:
        set_config_override(None)


def test_ytdlp_format_enforces_global_cap_and_honors_lower_plan_cap(tmp_path):
    from src.youtube_utils import YouTubeDownloader

    config = Config()
    config.temp_dir = str(tmp_path)
    set_config_override(config)
    try:
        downloader = YouTubeDownloader()
        assert downloader.get_optimal_download_options("abcdefghijk")["format"] == "bv*[height<=1080]+ba/b[height<=1080]"
        capped = downloader.get_optimal_download_options("abcdefghijk", 720)["format"]
        assert capped == "bv*[height<=720]+ba/b[height<=720]"
    finally:
        set_config_override(None)
