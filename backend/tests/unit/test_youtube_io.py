"""Tests for ``src/_youtube_io.py``."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src._youtube_io import (
    _build_info_options,
    _empty_video_info,
    _get_local_video_dimensions,
    _remove_cached_downloads,
)


class TestBuildInfoOptions:
    def test_has_required_keys(self):
        opts = _build_info_options()
        assert opts["quiet"] is True
        assert opts["skip_download"] is True
        assert opts["no_warnings"] is True
        assert "http_headers" in opts
        assert "User-Agent" in opts["http_headers"]

    def test_socket_timeout_sensible(self):
        assert _build_info_options()["socket_timeout"] >= 1


class TestEmptyVideoInfo:
    def test_default_shape(self):
        info = _empty_video_info()
        assert info["id"] is None
        assert info["title"] is None
        assert info["description"] == ""
        assert info["duration"] is None

    def test_preserves_video_id(self):
        info = _empty_video_info("abc123")
        assert info["id"] == "abc123"

    def test_all_numeric_fields_none(self):
        info = _empty_video_info()
        for k in ("duration", "view_count", "like_count", "fps", "filesize"):
            assert info[k] is None


class TestGetLocalVideoDimensions:
    def test_returns_zero_on_error(self, tmp_path):
        missing = tmp_path / "missing.mp4"
        # ffprobe is not guaranteed to exist in the test env, and even if it
        # does, a non-existent file should make it fail. Either path returns
        # (0, 0) by design.
        assert _get_local_video_dimensions(missing) == (0, 0)

    def test_parses_ffprobe_output(self, monkeypatch):
        class FakeResult:
            stdout = "1920x1080\n"

        def fake_run(*args, **kwargs):
            return FakeResult()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _get_local_video_dimensions(Path("anything.mp4")) == (1920, 1080)

    def test_malformed_output_returns_zero(self, monkeypatch):
        class FakeResult:
            stdout = "garbage"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
        assert _get_local_video_dimensions(Path("x.mp4")) == (0, 0)


class TestRemoveCachedDownloads:
    def test_noop_when_no_matches(self, tmp_path):
        _remove_cached_downloads(tmp_path, "missing-id")
        # Just asserting it doesn't raise.

    def test_removes_video_files(self, tmp_path):
        video_id = "abc123"
        targets = [
            tmp_path / f"{video_id}.mp4",
            tmp_path / f"{video_id}.mkv",
            tmp_path / f"{video_id}.webm",
        ]
        keep = tmp_path / f"{video_id}.txt"
        for p in targets + [keep]:
            p.write_bytes(b"\x00")

        _remove_cached_downloads(tmp_path, video_id)

        for p in targets:
            assert not p.exists(), f"{p.name} should have been removed"
        assert keep.exists(), "non-video files should not be removed"

    def test_is_resilient_to_unlink_errors(self, tmp_path, monkeypatch):
        video_id = "abc123"
        f = tmp_path / f"{video_id}.mp4"
        f.write_bytes(b"\x00")

        original_unlink = Path.unlink

        def flaky_unlink(self, *args, **kwargs):
            if self == f:
                raise PermissionError("locked")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        # Must not raise
        _remove_cached_downloads(tmp_path, video_id)
