"""The durable YouTube full-file cache never serves a lower quality than requested."""
import json
from pathlib import Path

import pytest

from src import youtube_source as source
from src.config import Config, set_config_override

VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = ""
    config.youtube_max_height = 1080
    config.youtube_source_cache = True
    config.youtube_source_cache_ttl_seconds = 0
    monkeypatch.setattr(source, "_storage", lambda: None)
    set_config_override(config)
    yield tmp_path / "youtube-cache" / VIDEO_ID
    set_config_override(None)


def _store(directory: Path, name: str, **manifest) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"cached " + name.encode())
    (directory / "manifest.json").write_text(json.dumps({
        "version": 1, "video_id": VIDEO_ID, "audio": None, "ranges": [],
        "audio_policy": source.AUDIO_POLICY, "full": name, **manifest,
    }))
    return path


def _downloader(staging: Path, calls: list):
    def download(**_kwargs):
        calls.append(1)
        staged = staging / f"{VIDEO_ID}.mp4"
        staged.write_bytes(b"fresh download")
        return staged
    return download


def test_720p_cache_is_not_reused_for_a_1080p_request(cache, tmp_path):
    low = _store(cache, "full-720.mp4", full_height=720)
    calls = []

    assert source.cached_full_media(VIDEO_ID) is None
    result = source.resolve_full_youtube_download(URL, VIDEO_ID, _downloader(tmp_path, calls))

    assert calls == [1]
    assert result.name == "full-1080.mp4" and result.read_bytes() == b"fresh download"
    manifest = json.loads((cache / "manifest.json").read_text())
    assert manifest["full"] == "full-1080.mp4" and manifest["full_height"] == 1080
    # The 720p file another job may still be reading is left untouched.
    assert low.read_bytes() == b"cached full-720.mp4"


def test_720p_cache_does_not_short_circuit_1080p_sections(cache, monkeypatch):
    _store(cache, "full-720.mp4", full_height=720)
    segments = [{"start_time": 10, "end_time": 20}]

    with pytest.raises(source.PartialDownloadUnavailable):
        source.materialize_clip_source(URL, VIDEO_ID, segments, duration=100)
    assert source.materialize_clip_source(
        URL, VIDEO_ID, segments, duration=100, quality="720",
    ).name == "full-720.mp4"


def test_1080p_cache_is_reused_for_a_720p_request(cache, tmp_path):
    high = _store(cache, "full-1080.mp4", full_height=1080)
    calls = []

    assert source.cached_full_media(VIDEO_ID, "720") == high
    assert source.resolve_full_youtube_download(
        URL, VIDEO_ID, _downloader(tmp_path, calls), quality="720",
    ) == high
    assert calls == []


def test_lower_download_finishing_later_keeps_the_better_file(cache, tmp_path):
    high = _store(cache, "full-1080.mp4", full_height=1080)
    staged = tmp_path / f"{VIDEO_ID}.mp4"
    staged.write_bytes(b"late 720p download")

    assert source.adopt_full_download(VIDEO_ID, staged, quality="720") == high
    assert not staged.exists()
    manifest = json.loads((cache / "manifest.json").read_text())
    assert manifest["full"] == "full-1080.mp4" and manifest["full_height"] == 1080


def test_upgrade_carries_the_transcript_forward(cache, tmp_path):
    low = _store(cache, "full-720.mp4", full_height=720)
    low.with_suffix(".transcript_cache.json").write_text('{"words": []}')
    staged = tmp_path / f"{VIDEO_ID}.mp4"
    staged.write_bytes(b"1080p download")

    result = source.adopt_full_download(VIDEO_ID, staged)

    assert result.name == "full-1080.mp4"
    assert result.with_suffix(".transcript_cache.json").read_text() == '{"words": []}'


@pytest.mark.parametrize(("probed", "reused"), [(1080, True), (720, False), (None, False)])
def test_legacy_manifest_without_height_is_probed(cache, monkeypatch, probed, reused):
    legacy = _store(cache, "full.mp4")

    def probe(_path):
        if probed is None:
            raise RuntimeError("unreadable")
        return (probed * 16 // 9, probed)

    monkeypatch.setattr("src.media.ffmpeg.ffprobe_video_size", probe)

    assert (source.cached_full_media(VIDEO_ID) == legacy) is reused
    # A legacy file that probes at 720p or more still serves a 720p plan.
    assert (source.cached_full_media(VIDEO_ID, "720") == legacy) is (probed is not None)
