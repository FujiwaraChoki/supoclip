"""Sections are fetched at the plan's height cap and reused only at or above it."""
import copy
import json
from pathlib import Path

import pytest

from src import youtube_proxy as proxy
from src import youtube_source as source
from src.config import Config, set_config_override
from tests.unit.test_youtube_audio_fallback import FORMATS, PROXY

VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = PROXY
    config.youtube_proxy_country = ""
    config.youtube_pot_provider_url = ""
    config.youtube_max_height = 1080
    config.youtube_section_padding_seconds = 0
    config.youtube_source_cache = True
    config.youtube_source_cache_ttl_seconds = 0
    monkeypatch.setattr(source, "_storage", lambda: None)
    monkeypatch.setattr(source, "assemble_timeline", _fake_assemble)
    set_config_override(config)
    yield tmp_path / "youtube-cache" / VIDEO_ID
    set_config_override(None)


def _fake_assemble(slices, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"+".join(piece.path.name.encode() for piece, _start, _end in slices))
    return source.build_time_map([(start, end) for _piece, start, end in slices])


def _store_sections(directory: Path, *entries: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (directory / entry["file"]).write_bytes(b"cached section")
    (directory / "manifest.json").write_text(json.dumps({
        "version": 1, "video_id": VIDEO_ID, "audio": None, "full": None,
        "audio_policy": source.AUDIO_POLICY, "ranges": list(entries),
    }))


def _recording_section(calls: list):
    def download(_url, _video_id, start, end, dest, **kwargs):
        calls.append({"start": start, "end": end, "height": kwargs.get("height")})
        dest.write_bytes(b"fresh section")
        return dest, start, end
    return download


def test_720p_plan_fetches_sections_at_720p(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(source, "download_section_file", _recording_section(calls))

    source.materialize_clip_source(
        URL, VIDEO_ID, [{"start_time": 0, "end_time": 150}], duration=600, quality="720",
    )

    assert [call["height"] for call in calls] == [720, 720, 720]
    manifest = json.loads((cache / "manifest.json").read_text())
    assert {item["height"] for item in manifest["ranges"]} == {720}


def test_full_quality_plans_keep_fetching_sections_at_the_global_cap(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(source, "download_section_file", _recording_section(calls))

    source.materialize_clip_source(URL, VIDEO_ID, [{"start_time": 0, "end_time": 50}], duration=600)

    assert [call["height"] for call in calls] == [1080]


def test_720p_section_is_not_reused_for_a_1080p_request(cache, monkeypatch):
    low = {"start": 0, "end": 60, "file": "r-0000000000-0000060000-h720.mp4", "bytes": 14, "height": 720}
    _store_sections(cache, low)
    calls = []
    monkeypatch.setattr(source, "download_section_file", _recording_section(calls))

    result = source.materialize_clip_source(
        URL, VIDEO_ID, [{"start_time": 10, "end_time": 50}], duration=600,
    )

    assert calls == [{"start": 10, "end": 50, "height": 1080}]
    assert b"h720" not in result.read_bytes()
    # The 720p file another job may still be reading is left in place.
    assert (cache / low["file"]).read_bytes() == b"cached section"
    manifest = json.loads((cache / "manifest.json").read_text())
    assert sorted(item["height"] for item in manifest["ranges"]) == [720, 1080]


@pytest.mark.parametrize("stored", [
    {"start": 0, "end": 60, "file": "r-0000000000-0000060000-h1080.mp4", "bytes": 14, "height": 1080},
    # Sections cached before heights were recorded were fetched at the global cap.
    {"start": 0, "end": 60, "file": "r-0000000000-0000060000.mp4", "bytes": 14},
])
@pytest.mark.parametrize("quality", [None, "720"])
def test_global_cap_sections_serve_every_plan(cache, monkeypatch, stored, quality):
    _store_sections(cache, stored)
    calls = []
    monkeypatch.setattr(source, "download_section_file", _recording_section(calls))

    result = source.materialize_clip_source(
        URL, VIDEO_ID, [{"start_time": 10, "end_time": 50}], duration=600, quality=quality,
    )

    assert calls == []
    assert result.read_bytes() == stored["file"].encode()


def test_section_download_picks_a_format_under_the_requested_height(tmp_path, monkeypatch):
    config = Config()
    config.youtube_proxy_url = PROXY
    config.youtube_proxy_country = ""
    config.youtube_pot_provider_url = ""
    config.youtube_max_height = 1080
    set_config_override(config)
    audio = [fmt for fmt in copy.deepcopy(FORMATS) if fmt.get("vcodec") == "none"]
    formats = audio + [
        {"format_id": f"v{height}", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "none",
         "ext": "mp4", "height": height, "tbr": height * 4,
         "url": f"https://rr1---sn-example.googlevideo.com/v{height}.m3u8"}
        for height in (720, 1080)
    ]
    chosen = []

    def expand(fmt, *_args):
        if fmt.get("vcodec") not in (None, "none"):
            chosen.append(fmt["height"])
            raise proxy.PartialDownloadUnavailable("stop after format selection")
        return fmt

    monkeypatch.setattr(proxy, "extract_video_info", lambda *_args: {"duration": 600, "formats": formats})
    monkeypatch.setattr(proxy, "expand_hls_format", expand)
    try:
        for height in (720, None):
            with pytest.raises(proxy.PartialDownloadUnavailable):
                proxy.download_section_file(
                    URL, VIDEO_ID, 0, 60, tmp_path / "section.mp4", task_id="task12345678", height=height,
                )
    finally:
        set_config_override(None)

    assert chosen == [720, 1080]
