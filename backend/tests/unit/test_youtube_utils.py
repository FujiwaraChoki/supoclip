import requests

from src.apify_youtube_downloader import ApifyDownloadError
from src.config import Config, set_config_override
from src.youtube_utils import (
    YOUTUBE_METADATA_PROVIDER_DATA_API,
    YOUTUBE_METADATA_PROVIDER_YTDLP,
    _fetch_video_info_with_youtube_data_api,
    _parse_iso8601_duration_to_seconds,
    _pick_best_thumbnail,
    download_youtube_video,
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


def test_download_youtube_video_prefers_apify(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"
    config.apify_youtube_default_quality = "720"

    captured = {}

    def fake_apify(url: str, video_id: str):
        captured["url"] = url
        captured["video_id"] = video_id
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
        }
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


def test_download_youtube_video_falls_back_to_ytdlp_when_apify_fails(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"

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
