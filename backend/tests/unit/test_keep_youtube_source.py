"""KEEP_YOUTUBE_SOURCE keeps the downloaded video so the editor does not download it again."""

from pathlib import Path

import pytest

from src.config import Config
from src.services.task_service import TaskService


VIDEO_ID = "abcdefghijk"
SOURCE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def downloaded_source(tmp_path: Path) -> Path:
    source = tmp_path / f"{VIDEO_ID}.mp4"
    source.write_bytes(b"source video")
    return source


def use_config(tmp_path, monkeypatch, *, keep: bool) -> None:
    config = Config()
    config.temp_dir = str(tmp_path)
    config.keep_youtube_source = keep
    monkeypatch.setattr("src.services.task_service.get_config", lambda: config)
    monkeypatch.setattr("src.youtube_utils.get_config", lambda: config)
    monkeypatch.setattr(
        "src.services.task_service.MediaStorage",
        lambda: type("Storage", (), {"save_youtube_metadata": lambda *_: None})(),
    )


def test_youtube_source_is_deleted_by_default(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, keep=False)
    source = downloaded_source(tmp_path)

    TaskService._cleanup_source_video(source, "youtube", SOURCE_URL)

    assert not source.exists()


def test_youtube_source_is_kept_when_enabled(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, keep=True)
    source = downloaded_source(tmp_path)

    TaskService._cleanup_source_video(source, "youtube", SOURCE_URL)

    assert source.read_bytes() == b"source video"


@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("false", False), ("0", False),
    ("true", True), ("TRUE", True), ("1", True), ("yes", True),
])
def test_keep_youtube_source_setting(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("KEEP_YOUTUBE_SOURCE", raising=False)
    else:
        monkeypatch.setenv("KEEP_YOUTUBE_SOURCE", value)

    assert Config().keep_youtube_source is expected
