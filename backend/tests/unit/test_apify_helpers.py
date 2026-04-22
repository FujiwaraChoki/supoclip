"""Tests for pure helpers in ``src/apify_youtube_downloader.py``.

Focuses on the recursive URL extractor + file-extension inference — the
``download_video_via_apify`` entry point itself is already covered by
``tests/unit/test_apify_youtube_downloader.py``.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.apify_youtube_downloader import (
    ALLOWED_APIFY_QUALITIES,
    _extract_download_url,
    _infer_file_extension,
    normalize_apify_quality,
)


class TestNormalizeApifyQuality:
    @pytest.mark.parametrize("value", sorted(ALLOWED_APIFY_QUALITIES))
    def test_valid(self, value):
        assert normalize_apify_quality(value) == value

    def test_trims_whitespace(self):
        assert normalize_apify_quality("  720  ") == "720"

    @pytest.mark.parametrize("value", [None, "", "4k", "2160", "garbage"])
    def test_invalid_fallback(self, value):
        assert normalize_apify_quality(value) == "1080"


class TestExtractDownloadUrl:
    def test_direct_download_url_field(self):
        assert _extract_download_url({"downloadUrl": "https://x.com/a.mp4"}) == "https://x.com/a.mp4"

    def test_non_http_direct_ignored(self):
        # downloadUrl that's not http(s) → keeps looking
        assert _extract_download_url({"downloadUrl": "ftp://x.com/a.mp4"}) is None

    def test_any_download_flavored_key(self):
        assert _extract_download_url({"video_download_link": "https://x.com/a"}) == "https://x.com/a"

    def test_nested_dict(self):
        payload = {"result": {"nested": {"downloadUrl": "https://y/z.mp4"}}}
        assert _extract_download_url(payload) == "https://y/z.mp4"

    def test_list_of_items(self):
        payload = [
            {"noop": 1},
            {"downloadUrl": "https://pick.me/v.mp4"},
        ]
        assert _extract_download_url(payload) == "https://pick.me/v.mp4"

    def test_none_returned_for_empty(self):
        assert _extract_download_url({}) is None
        assert _extract_download_url([]) is None
        assert _extract_download_url(None) is None
        assert _extract_download_url("scalar") is None


class TestInferFileExtension:
    def _response(self, headers):
        resp = MagicMock()
        resp.headers = headers
        return resp

    def test_content_disposition_filename(self):
        resp = self._response({"Content-Disposition": 'attachment; filename="video.mkv"'})
        assert _infer_file_extension(resp, "https://x/y") == ".mkv"

    def test_content_disposition_utf8_filename(self):
        resp = self._response({"Content-Disposition": "attachment; filename*=UTF-8''my%20clip.webm"})
        assert _infer_file_extension(resp, "https://x/y") == ".webm"

    def test_content_type_fallback(self):
        resp = self._response({"Content-Type": "video/mp4"})
        ext = _infer_file_extension(resp, "https://x/y")
        assert ext == ".mp4"

    def test_url_path_suffix_fallback(self):
        resp = self._response({})
        assert _infer_file_extension(resp, "https://x/path/video.avi?token=123") == ".avi"

    def test_default_mp4_when_unknown(self):
        resp = self._response({})
        assert _infer_file_extension(resp, "https://x/unknown-endpoint") == ".mp4"

    def test_content_type_with_parameters(self):
        # "video/mp4; codecs=avc1" → ignores parameters, uses "video/mp4"
        resp = self._response({"Content-Type": "video/mp4; codecs=avc1"})
        assert _infer_file_extension(resp, "https://x/y") == ".mp4"
