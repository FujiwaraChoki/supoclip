"""Tests for ``src/_youtube_downloader.py``."""

import pytest

from src._youtube_downloader import YouTubeDownloader


class TestYouTubeDownloader:
    def test_initializes_temp_dir(self, tmp_path, monkeypatch):
        from src import config as config_module

        class FakeCfg:
            temp_dir = str(tmp_path / "downloads")

        monkeypatch.setattr(config_module, "get_config", lambda: FakeCfg())
        # _youtube_downloader.get_config is imported at module load; re-patch
        from src import _youtube_downloader

        monkeypatch.setattr(_youtube_downloader, "get_config", lambda: FakeCfg())

        dl = YouTubeDownloader()
        assert dl.temp_dir.exists()
        assert dl.temp_dir.name == "downloads"

    def test_optimal_download_options_has_expected_keys(self, tmp_path, monkeypatch):
        from src import _youtube_downloader

        class FakeCfg:
            temp_dir = str(tmp_path)

        monkeypatch.setattr(_youtube_downloader, "get_config", lambda: FakeCfg())
        dl = YouTubeDownloader()

        opts = dl.get_optimal_download_options("abc123")
        assert "bestvideo" in opts["format"]
        assert opts["merge_output_format"] == "mp4"
        assert opts["quiet"] is True
        assert opts["noplaylist"] is True
        assert opts["outtmpl"].endswith("abc123.%(ext)s")

    def test_output_template_includes_video_id(self, tmp_path, monkeypatch):
        from src import _youtube_downloader

        class FakeCfg:
            temp_dir = str(tmp_path)

        monkeypatch.setattr(_youtube_downloader, "get_config", lambda: FakeCfg())
        dl = YouTubeDownloader()

        opts = dl.get_optimal_download_options("zzz999")
        assert "zzz999" in opts["outtmpl"]
