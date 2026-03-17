from src.apify_youtube_downloader import ApifyDownloadError
from src.config import Config, set_config_override
from src.youtube_utils import download_youtube_video


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
