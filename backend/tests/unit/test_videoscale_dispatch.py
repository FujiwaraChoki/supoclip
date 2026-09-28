import pytest

from src.config import Config, set_config_override
from src import youtube_utils
from src.videoscale_youtube_downloader import VideoScaleDownloadError


@pytest.mark.parametrize("fails", [False, True])
def test_videoscale_dispatch_never_uses_paid_fallback(monkeypatch, tmp_path, fails):
    monkeypatch.setenv("YOUTUBE_DOWNLOAD_PROVIDER", "videoscale")
    config = Config()
    assert config.youtube_download_provider == "videoscale"
    target = tmp_path / "complete.mp4"

    def download(url, video_id):
        assert video_id == "abcdefghijk"
        if fails:
            raise VideoScaleDownloadError("Provider unavailable")
        return target

    def unexpected(*args, **kwargs):
        pytest.fail("Explicit VideoScale selection must not use another provider")

    monkeypatch.setattr(youtube_utils, "download_video_via_videoscale", download)
    monkeypatch.setattr(youtube_utils, "download_youtube_video_with_apify", unexpected)
    monkeypatch.setattr(youtube_utils, "_download_youtube_video_with_ytdlp", unexpected)
    set_config_override(config)
    try:
        if fails:
            with pytest.raises(VideoScaleDownloadError):
                youtube_utils.download_youtube_video("https://youtube.com/watch?v=abcdefghijk")
        else:
            assert youtube_utils.download_youtube_video("https://youtube.com/watch?v=abcdefghijk") == target
    finally:
        set_config_override(None)


def test_videoscale_completed_source_is_cleaned_up(monkeypatch, tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    directory = tmp_path / "videoscale-abcdefghijk-test"
    directory.mkdir()
    source = directory / "abcdefghijk.mp4"
    source.write_bytes(b"downloaded source")
    set_config_override(config)
    try:
        youtube_utils.cleanup_downloaded_files("abcdefghijk", source)
        assert not source.exists()
        assert not directory.exists()
    finally:
        set_config_override(None)
