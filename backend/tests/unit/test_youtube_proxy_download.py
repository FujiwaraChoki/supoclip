"""Proxy yt-dlp downloads: sticky sessions, 1080p, sections, and the source cache."""

import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import Config, set_config_override
from src.observability import JsonLogFormatter
from src.youtube_proxy import (
    ByteBudget,
    DownloadBudgetExceeded,
    audio_format_selector,
    base_proxy_params,
    download_options,
    normalize_country,
    render_proxy_url,
    pick_video_format,
    select_fragments_for_window,
    sticky_session_id,
    video_format_selector,
    log_youtube_attempt,
)
from src.youtube_source import (
    build_time_map,
    load_time_map,
    localize_segment,
    materialize_clip_source,
    merge_ranges,
    missing_intervals,
    restore_clip_timeline,
    save_time_map,
    shift_transcript,
)
from src.youtube_utils import (
    YOUTUBE_DOWNLOAD_PROVIDER_APIFY,
    YOUTUBE_DOWNLOAD_PROVIDER_YTDLP,
    _provider_order,
)
from src.clip_source_map import load_clip_source_ranges, save_clip_source_ranges


PROXY = "http://user-session-{session}:pass@gw.example:823"
VIDEO_ID = "abcdefghijk"


def _config(tmp_path: Path) -> Config:
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = PROXY
    config.youtube_proxy_country = ""
    config.youtube_pot_provider_url = ""
    config.youtube_max_height = 1080
    config.youtube_section_padding_seconds = 10
    config.youtube_source_cache = True
    config.youtube_download_max_bytes = 2_000_000_000
    config.youtube_download_provider = YOUTUBE_DOWNLOAD_PROVIDER_YTDLP
    set_config_override(config)
    return config


def test_sticky_session_and_country_substitution():
    session = sticky_session_id("task-1234-abcd-extra-chars-that-are-long")
    assert session.isalnum() and 8 <= len(session) <= 24
    rendered = render_proxy_url(
        "http://user-country-{country}-session-{session}:pass@gw.example:823",
        session,
        normalize_country("US"),
    )
    assert rendered == f"http://user-country-us-session-{session}:pass@gw.example:823"
    assert normalize_country("germany") == ""
    assert "{country}" not in render_proxy_url(
        "http://user-country-{country}-session-{session}:pass@gw.example:823",
        session,
        normalize_country("germany"),
    )
    with pytest.raises(Exception):
        render_proxy_url(PROXY, "short")


def test_proxy_options_force_ipv4_and_skip_player_clients(tmp_path):
    config = _config(tmp_path)
    try:
        options = download_options("task12345678", VIDEO_ID, ByteBudget(1000))
        assert options["source_address"] == "0.0.0.0"
        assert not options.get("nocheckcertificate", False)
        assert "download_ranges" not in options
        assert "player_client" not in json.dumps(options.get("extractor_args", {}))
        assert options["proxy"].startswith("http://user-session-")
        assert "pass@" in options["proxy"]

        config.youtube_pot_provider_url = "http://bgutil:4416"
        params = base_proxy_params("task12345678", VIDEO_ID, config)
        assert params["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == ["http://bgutil:4416"]
        bare = base_proxy_params("task12345678", VIDEO_ID, _without_pot(config))
        assert "extractor_args" not in bare
    finally:
        set_config_override(None)


def _without_pot(config: Config) -> Config:
    config.youtube_pot_provider_url = ""
    return config


def test_format_selector_never_exceeds_1080(tmp_path):
    config = _config(tmp_path)
    try:
        assert video_format_selector() == "bv*[height<=1080]+ba/b[height<=1080]"
        assert "140" in audio_format_selector()
        config.youtube_max_height = 720
        assert video_format_selector() == "bv*[height<=720]+ba/b[height<=720]"
        config.youtube_max_height = 2160
        assert "1080" in video_format_selector()
        assert "2160" not in video_format_selector()
    finally:
        set_config_override(None)


def test_provider_order_puts_proxy_first():
    class Settings:
        youtube_proxy_url = ""
        youtube_download_provider = YOUTUBE_DOWNLOAD_PROVIDER_YTDLP

    settings = Settings()
    assert _provider_order(settings, skip_proxy=False) == [
        YOUTUBE_DOWNLOAD_PROVIDER_YTDLP,
        "rapidapi",
        YOUTUBE_DOWNLOAD_PROVIDER_APIFY,
    ]
    settings.youtube_proxy_url = PROXY
    assert _provider_order(settings, skip_proxy=False) == ["yt_dlp_proxy", "rapidapi", "apify", "yt_dlp"]
    assert _provider_order(settings, skip_proxy=True) == ["rapidapi", "apify", "yt_dlp"]


def test_timed_fragments_win_over_byte_range_chunks():
    untimed = {
        "vcodec": "avc1",
        "height": 1080,
        "ext": "mp4",
        "tbr": 8000,
        "protocol": "http_dash_segments",
        "fragments": [{"url": "byte-0"}, {"url": "byte-1"}],
    }
    timed = {
        "vcodec": "avc1",
        "height": 1080,
        "ext": "mp4",
        "tbr": 1500,
        "protocol": "http_dash_segments",
        "fragments": [{"url": "init"}, {"url": "seg", "duration": 5}],
    }
    chosen = pick_video_format([untimed, timed])
    assert chosen is not None
    assert chosen["tbr"] == 1500


def test_fragment_window_keeps_init_and_rejects_untimed():
    fragments = [
        {"url": "init"},
        {"duration": 10, "url": "a"},
        {"duration": 10, "url": "b"},
        {"duration": 10, "url": "c"},
    ]
    selected, start, end = select_fragments_for_window(fragments, 12, 25)
    assert (start, end) == (10, 30)
    assert selected[0]["url"] == "init"
    assert [item["url"] for item in selected] == ["init", "b", "c"]
    assert select_fragments_for_window([{"url": "x"}, {"url": "y"}], 0, 5) is None


def test_ranges_pad_merge_and_report_gaps():
    assert merge_ranges([(10, 20), (18, 30)], padding=10) == [(0, 40)]
    assert merge_ranges([(0, 5), (100, 110)], padding=2, duration=108) == [(0, 7), (98, 108)]
    assert missing_intervals([(0, 30), (40, 50)], [(10, 45)]) == [(30, 40)]
    assert missing_intervals([(0, 50)], [(10, 20)]) == []


def test_time_map_and_transcript_shift_roundtrip(tmp_path):
    time_map = build_time_map([(100, 130), (200, 220)])
    assert time_map.to_local(110) == pytest.approx(10)
    assert time_map.to_original(10) == pytest.approx(110)
    assert time_map.to_local(205) == pytest.approx(35)
    media = tmp_path / "render.mp4"
    media.write_bytes(b"video")
    save_time_map(media, time_map)
    loaded = load_time_map(media)
    assert loaded is not None
    assert loaded.to_original(35) == pytest.approx(205)

    shifted = shift_transcript(
        {
            "words": [
                {"text": "keep", "start": 110_000, "end": 112_000},
                {"text": "drop", "start": 50_000, "end": 51_000},
            ],
            "utterances": [],
        },
        time_map,
    )
    assert [word["text"] for word in shifted["words"]] == ["keep"]
    assert shifted["words"][0]["start"] == 10_000
    assert shifted["words"][0]["end"] == 12_000


def test_localize_and_restore_source_map(tmp_path):
    time_map = build_time_map([(100, 160)])
    segment = {"start_time": "01:40", "end_time": "02:20", "keep_ranges": [(100.0, 140.0)]}
    local = localize_segment(segment, time_map)
    assert local["start_time"] == "00:00"
    assert local["end_time"] == "00:40"
    assert local["keep_ranges"] == [(0.0, 40.0)]

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    save_clip_source_ranges(clip, local["keep_ranges"])
    clip_info = {
        "path": str(clip),
        "start_time": local["start_time"],
        "end_time": local["end_time"],
        "keep_ranges": local["keep_ranges"],
    }
    restored = restore_clip_timeline(clip_info, time_map, segment)
    assert restored["start_time"] == "01:40"
    assert restored["end_time"] == "02:20"
    assert load_clip_source_ranges(clip) == [(100.0, 140.0)]
    assert restored["keep_ranges"] == [(100.0, 140.0)]


def test_cached_full_file_skips_the_downloader(tmp_path, monkeypatch):
    _config(tmp_path)
    try:
        directory = tmp_path / "youtube-cache" / VIDEO_ID
        directory.mkdir(parents=True)
        full = directory / "full.mp4"
        full.write_bytes(b"full-video")
        (directory / "manifest.json").write_text(json.dumps({
            "version": 1,
            "video_id": VIDEO_ID,
            "audio": None,
            "full": "full.mp4",
            "ranges": [],
        }))

        def forbidden(*_args, **_kwargs):
            raise AssertionError("section download should not run for a cached full file")

        monkeypatch.setattr("src.youtube_source.download_section_file", forbidden)
        result = materialize_clip_source(
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            VIDEO_ID,
            [{"start_time": 20, "end_time": 40}],
        )
        assert result == full
    finally:
        set_config_override(None)


def test_extending_a_range_fetches_only_the_gap(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.youtube_section_padding_seconds = 0
    directory = tmp_path / "youtube-cache" / VIDEO_ID
    directory.mkdir(parents=True)
    piece = directory / "r-000000000-000030000.mp4"
    piece.write_bytes(b"cached")
    (directory / "manifest.json").write_text(json.dumps({
        "version": 1,
        "video_id": VIDEO_ID,
        "audio": None,
        "full": None,
        "ranges": [{"start": 0, "end": 30, "file": piece.name, "bytes": 6}],
        "audio_policy": "original-v1",
    }))
    calls = []

    def fake_section(_url, _video_id, start, end, dest, **_kwargs):
        calls.append((start, end))
        dest.write_bytes(b"gap")
        return dest, start, end

    def fake_assemble(slices, dest):
        dest.write_bytes(b"render")
        time_map = build_time_map([(start, end) for _piece, start, end in slices])
        save_time_map(dest, time_map)
        return time_map

    monkeypatch.setattr("src.youtube_source.download_section_file", fake_section)
    monkeypatch.setattr("src.youtube_source.assemble_timeline", fake_assemble)
    try:
        result = materialize_clip_source(
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            VIDEO_ID,
            [{"start_time": 20, "end_time": 50}],
            duration=120,
        )
        assert calls == [(30.0, 50.0)]
        assert result.name == "render.mp4"
        loaded = load_time_map(result)
        assert loaded is not None
        assert loaded.to_local(20) == pytest.approx(0)
        assert loaded.to_local(50) == pytest.approx(30)
    finally:
        set_config_override(None)


def test_byte_budget_is_shared_and_attempt_logs_are_structured(caplog):
    budget = ByteBudget(100)
    budget.add(40)
    budget.add(60)
    with pytest.raises(DownloadBudgetExceeded):
        budget.add(1)

    caplog.set_level(logging.INFO)
    log_youtube_attempt(
        provider="yt_dlp_proxy",
        video_id=VIDEO_ID,
        kind="audio",
        outcome="success",
        nbytes=1200,
        task_id="task-1",
    )
    record = caplog.records[-1]
    assert record.youtube_provider == "yt_dlp_proxy"
    assert record.youtube_bytes == 1200
    assert record.youtube_outcome == "success"
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["youtube_provider"] == "yt_dlp_proxy"
    assert payload["youtube_bytes"] == 1200
    assert payload["youtube_download_kind"] == "audio"
    assert "task_id" in payload


def test_assemble_timeline_uses_local_files_only(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    from src.youtube_source import CachedPiece, assemble_timeline

    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    for path, color in ((first, "black"), (second, "white")):
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin",
                "-f", "lavfi", "-i", f"color=c={color}:s=64x64:d=1",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not path.exists():
            pytest.skip("ffmpeg could not build a fixture clip")
    dest = tmp_path / "render.mp4"
    time_map = assemble_timeline(
        [
            (CachedPiece(10, 11, first), 10, 11),
            (CachedPiece(40, 41, second), 40, 41),
        ],
        dest,
    )
    assert dest.exists() and dest.stat().st_size > 0
    assert time_map.to_local(10) == pytest.approx(0)
    assert time_map.to_local(40.5) == pytest.approx(1.5)


def test_proxy_failure_then_router_timeout_reaches_apify_before_direct(tmp_path, monkeypatch):
    from src import youtube_utils as utils
    from src.rapidapi_youtube_downloader import RapidAPIDownloadError
    from src.youtube_proxy import DownloadDeadlineExceeded
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = PROXY
    config.youtube_download_provider = 'yt_dlp'  # Legacy setting cannot reorder backups.
    config.rapidapi_youtube_api_key = 'test-key'
    config.apify_api_token = 'test-token'
    calls = []

    def fail_proxy(*a, **kw):
        calls.append('proxy')
        raise DownloadDeadlineExceeded('proxy timed out')

    def fail_router(*a, **kw):
        calls.append('router')
        assert kw['timeout_seconds'] == 300
        raise RapidAPIDownloadError('router timed out')

    def apify(*a, **kw):
        calls.append('apify')
        target = tmp_path / 'complete.mp4'
        target.write_bytes(b'complete')
        return target

    monkeypatch.setattr(utils, 'download_full_with_proxy', fail_proxy)
    monkeypatch.setattr(utils, 'download_video_via_rapidapi', fail_router)
    monkeypatch.setattr(utils, '_resolve_apify_quality', lambda *a: '1080')
    monkeypatch.setattr(utils, 'download_youtube_video_with_apify', apify)
    monkeypatch.setattr(utils, '_get_local_video_dimensions', lambda *a: (1920, 1080))
    monkeypatch.setattr(utils, '_download_youtube_video_with_ytdlp',
                        lambda *a, **kw: pytest.fail('Direct yt-dlp ran before the paid backups'))
    set_config_override(config)
    try:
        assert utils.download_youtube_video('https://youtube.com/watch?v=abcdefghijk').is_file()
        assert calls == ['proxy', 'router', 'apify']
    finally:
        set_config_override(None)


def test_proxy_deadline_stops_new_requests_and_slow_progress(monkeypatch):
    from src import youtube_proxy as proxy
    from unittest.mock import MagicMock
    from yt_dlp.networking import Request
    now = [100.0]
    monkeypatch.setattr(proxy.time, 'monotonic', lambda: now[0])
    client = MagicMock()
    client.__enter__.return_value = client
    original_open = client.urlopen
    monkeypatch.setattr('yt_dlp.YoutubeDL', lambda *a: client)
    with proxy._proxy_client({'_youtube_deadline': 105}) as bounded:
        request = Request('https://manifest.googlevideo.com/video')
        bounded.urlopen(request)
        assert request.extensions['timeout'] == 5
        now[0] = 104
    assert original_open.call_count == 1
    # An expired attempt never opens another socket, even with no byte progress.
    with pytest.raises(proxy.DownloadDeadlineExceeded):
        with proxy._proxy_client({'_youtube_deadline': 103}):
            pytest.fail('Expired downloader entered')
    monkeypatch.setattr(proxy, 'base_proxy_params', lambda *a: {})
    options = proxy.download_options(None, 'abcdefghijk', ByteBudget(10000))
    now[0] += 301
    with pytest.raises(proxy.DownloadDeadlineExceeded):
        options['progress_hooks'][0]({'status': 'downloading', 'downloaded_bytes': 0})
