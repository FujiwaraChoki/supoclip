"""A 2160p output cap must not reuse media cached under the default 1080p cap."""
import pytest

from src import youtube_source as source
from src.config import Config, set_config_override


def _config(tmp_path, cap):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.max_output_height = cap
    return config


@pytest.fixture(autouse=True)
def _local_only(monkeypatch):
    monkeypatch.setattr(source, "_storage", lambda: None)
    yield
    set_config_override(None)


def test_default_cap_reuses_existing_cache_at_bare_id(tmp_path):
    set_config_override(_config(tmp_path, 1080))
    staged = tmp_path / "in.mp4"
    staged.write_bytes(b"1080p media")
    cached = source.adopt_full_download("abcdefghijk", staged)
    assert cached.parent == tmp_path / source.CACHE_DIRNAME / "abcdefghijk"
    assert source.cached_full_media("abcdefghijk") == cached


def test_2160_cap_does_not_reuse_1080_cache(tmp_path):
    set_config_override(_config(tmp_path, 1080))
    staged = tmp_path / "in.mp4"
    staged.write_bytes(b"1080p media")
    cached = source.adopt_full_download("abcdefghijk", staged)

    set_config_override(_config(tmp_path, 2160))
    assert source.cached_full_media("abcdefghijk") is None
    staged.write_bytes(b"4k media")
    fresh = source.adopt_full_download("abcdefghijk", staged)
    assert fresh != cached and fresh.read_bytes() == b"4k media"
    assert source.cached_full_media("abcdefghijk") == fresh

    set_config_override(_config(tmp_path, 1080))
    assert source.cached_full_media("abcdefghijk") == cached
