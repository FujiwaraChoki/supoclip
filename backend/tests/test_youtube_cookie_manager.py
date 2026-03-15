import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yt_dlp

from src.services.youtube_cookie_manager import (
    YouTubeAuthUnavailableError,
    YouTubeCookieManager,
)
from src.youtube_auth_types import ResolvedYouTubeCookieContext
from src import youtube_utils


class _FakeYoutubeDL:
    download_calls: list[str] = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def download(self, urls):
        cookiefile = self.opts.get("cookiefile")
        self.__class__.download_calls.append(cookiefile or "anonymous")
        if cookiefile and cookiefile.endswith("first.txt"):
            raise yt_dlp.utils.DownloadError("Sign in to confirm you're not a bot")

        output = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")


class YouTubeCookieManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = YouTubeCookieManager()

    def test_classify_error_detects_youtube_auth_challenge(self):
        failure = self.manager.classify_error(
            "ERROR: [youtube] Sign in to confirm you're not a bot"
        )

        self.assertTrue(failure.is_auth_failure)
        self.assertEqual(failure.code, "youtube_auth_challenge")

    def test_legacy_fallback_context_uses_configured_cookie_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_cookie = Path(temp_dir) / "legacy.txt"
            legacy_cookie.write_text("# Netscape HTTP Cookie File\n")

            with patch.object(
                self.manager.config, "youtube_cookies_file", str(legacy_cookie)
            ):
                contexts = self.manager._legacy_fallback_contexts()

        self.assertEqual(len(contexts), 1)
        self.assertTrue(contexts[0].is_legacy_fallback)
        self.assertEqual(contexts[0].cookiefile_path, str(legacy_cookie))

    @patch.object(youtube_utils.cookie_manager, "mark_success_sync")
    @patch.object(youtube_utils.cookie_manager, "mark_transient_failure_sync")
    @patch.object(youtube_utils.cookie_manager, "mark_auth_failure_sync")
    @patch.object(youtube_utils.cookie_manager, "get_download_contexts_sync")
    @patch("src.youtube_utils.get_youtube_video_info")
    @patch("src.youtube_utils._get_local_video_dimensions", return_value=(1920, 1080))
    @patch("src.youtube_utils.yt_dlp.YoutubeDL", _FakeYoutubeDL)
    def test_download_retries_with_next_cookie_account_after_auth_failure(
        self,
        _mock_dimensions,
        mock_get_video_info,
        mock_get_contexts,
        mock_mark_auth_failure,
        _mock_mark_transient_failure,
        mock_mark_success,
    ):
        mock_get_video_info.return_value = {"title": "Test", "duration": 120}

        with tempfile.TemporaryDirectory() as temp_dir:
            first_cookie = Path(temp_dir) / "first.txt"
            second_cookie = Path(temp_dir) / "second.txt"
            first_cookie.write_text("# Netscape HTTP Cookie File\n")
            second_cookie.write_text("# Netscape HTTP Cookie File\n")

            mock_get_contexts.return_value = [
                ResolvedYouTubeCookieContext(
                    account_id="first",
                    label="First",
                    status="healthy",
                    cookiefile_path=str(first_cookie),
                    storage_state_path=None,
                ),
                ResolvedYouTubeCookieContext(
                    account_id="second",
                    label="Second",
                    status="healthy",
                    cookiefile_path=str(second_cookie),
                    storage_state_path=None,
                ),
            ]

            with patch.object(youtube_utils.config, "temp_dir", temp_dir):
                _FakeYoutubeDL.download_calls = []
                result = youtube_utils.download_youtube_video(
                    "https://www.youtube.com/watch?v=BaW_jenozKc",
                    task_id="task-123",
                )

        self.assertIsNotNone(result)
        self.assertEqual(
            _FakeYoutubeDL.download_calls,
            [str(first_cookie), str(second_cookie)],
        )
        mock_mark_auth_failure.assert_called_once()
        mock_mark_success.assert_called_once_with("second", task_id="task-123")

    @patch.object(youtube_utils.cookie_manager, "get_download_contexts_sync", return_value=[])
    def test_download_raises_when_no_cookie_contexts_are_available(self, _mock_contexts):
        with self.assertRaises(YouTubeAuthUnavailableError):
            youtube_utils.get_youtube_video_info(
                "https://www.youtube.com/watch?v=BaW_jenozKc"
            )


if __name__ == "__main__":
    unittest.main()
