"""Live streams: URL detection, the part a request clips, and finding the recording."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from src import live_source, stream_source
from src.config import Config
from src.live_source import LiveSourceError, LiveStream, live_channel_url, window_for
from src.source_range import SourceRange, resolve_source_range, segment_end_seconds


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.twitch.tv/SomeStreamer", ("twitch", "https://www.twitch.tv/somestreamer")),
        ("https://m.twitch.tv/somestreamer/", ("twitch", "https://www.twitch.tv/somestreamer")),
        ("https://kick.com/some-streamer", ("kick", "https://kick.com/some-streamer")),
        ("https://www.youtube.com/@creator/live", ("youtube", "https://www.youtube.com/@creator/live")),
        ("https://youtube.com/channel/UC1234567890abcdef/live", ("youtube", "https://www.youtube.com/channel/UC1234567890abcdef/live")),
        ("https://www.twitch.tv/videos/123456", None),
        ("https://www.twitch.tv/directory", None),
        ("https://kick.com/browse", None),
        ("https://kick.com/streamer?clip=clip_01ABC", None),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
        ("https://example.com/streamer", None),
        ("ftp://twitch.tv/streamer", None),
    ],
)
def test_live_channel_urls(url, expected):
    assert live_channel_url(url) == expected


def _stream(recorded: float, platform: str = "twitch", **kwargs) -> LiveStream:
    return LiveStream(
        platform=platform, channel_url="https://www.twitch.tv/streamer", channel="Streamer",
        recording_url="https://www.twitch.tv/videos/1", recording_id="1", recorded_seconds=recorded, **kwargs,
    )


def test_window_is_the_newest_part_before_the_live_edge():
    window = window_for(_stream(7200.0), window_seconds=1800)
    # Twitch keeps the last ~10 s out: those segments may not be published yet.
    assert (window.start, window.end, window.billed_seconds, window.fresh_after) == (5390, 7190.0, 1800.0, None)


def test_window_longer_than_the_stream_starts_at_zero_and_bills_what_exists():
    window = window_for(_stream(600.0), window_seconds=3600)
    assert window.start == 0 and window.end == 590.0 and window.billed_seconds == 590.0


def test_window_is_capped_by_the_plan():
    window = window_for(_stream(20000.0), window_seconds=6 * 3600, max_seconds=10800)
    assert window.end - window.start == pytest.approx(10800.0, abs=1)


def test_follow_part_overlaps_the_previous_one_but_bills_only_new_stream():
    window = window_for(_stream(5410.0), from_seconds=3600.0)
    assert window.start == 3510  # 90 s overlap (LIVE_FOLLOW_OVERLAP_SECONDS)
    assert window.end == 5400.0
    assert window.billed_seconds == pytest.approx(1800.0)
    assert window.fresh_after == 3600.0


def test_follow_part_after_a_long_gap_is_capped_and_continues_next_time():
    window = window_for(_stream(30000.0), from_seconds=1800.0, max_seconds=10800)
    assert window.end == pytest.approx(window.start + 10800)


def test_final_part_reaches_the_end_of_the_finished_recording():
    window = window_for(_stream(4000.0, final=True), from_seconds=3600.0)
    assert window.end == 4000.0


def test_a_stream_younger_than_a_minute_is_refused_with_a_wait():
    with pytest.raises(LiveSourceError) as error:
        window_for(_stream(40.0), window_seconds=1800)
    assert error.value.code == "LIVE_TOO_SHORT"
    assert error.value.detail["retry_after_seconds"] >= 30


def test_live_range_keeps_the_whole_recording_so_far_and_round_trips():
    # "Everything so far" of a growing recording is still a fixed part of it.
    assert resolve_source_range(_requested(0, 600), 600.0, None) is None
    kept = resolve_source_range(_requested(0, 600), 600.0, None, keep_whole=True)
    assert kept is not None and kept.start == 0 and kept.end == 600.0
    live = SourceRange(3510, 5400.0, 5410.0, live=True, fresh_after=3600.0)
    metadata = {"source_range": live.to_metadata()}
    assert metadata["source_range"]["live"] is True
    assert SourceRange.from_metadata(metadata) == live
    # Ranges without the live keys read back exactly as before.
    plain = SourceRange(10, 100.0, 1000.0)
    assert plain.to_metadata() == {"start_seconds": 10, "end_seconds": 100.0, "source_duration_seconds": 1000.0}
    assert SourceRange.from_metadata({"source_range": plain.to_metadata()}) == plain


def _requested(start, end):
    from src.source_range import RequestedRange

    return RequestedRange(float(start), float(end))


def test_segment_end_reads_ranges_or_clock():
    assert segment_end_seconds({"start_time": "01:00", "end_time": "01:30"}) == 90.0
    assert segment_end_seconds({"end_time": "00:10", "source_ranges": [[3600.0, 3610.0], [3620.0, 3650.0]]}) == 3650.0


# ------------------------------------------------------------ resolution


def _extracts(monkeypatch, answers: dict):
    calls = []

    def extract(url, platform, deadline, **extra):
        calls.append((url, extra))
        key = (url, bool(extra.get("live_from_start")))
        answer = answers.get(key, answers.get(url))
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise AssertionError(f"unexpected extract {key}")
        return answer

    monkeypatch.setattr(live_source, "_stream_extract", extract)
    return calls


def test_twitch_live_channel_resolves_to_its_in_progress_vod(monkeypatch):
    now = time.time()
    _extracts(monkeypatch, {
        ("https://www.twitch.tv/streamer", False): {"is_live": True, "timestamp": now - 2400, "uploader": "Streamer", "description": "GTA night"},
        ("https://www.twitch.tv/streamer", True): {"_type": "url", "url": "https://www.twitch.tv/videos/1000000001"},
        "https://www.twitch.tv/videos/1000000001": {"duration": 2390, "is_live": True},
    })
    stream = live_source.resolve_live_stream("https://twitch.tv/Streamer")
    assert stream.platform == "twitch"
    assert stream.recording_url == "https://www.twitch.tv/videos/1000000001"
    assert stream.recorded_seconds == 2390
    assert stream.title == "GTA night"
    assert stream.public()["elapsed_seconds"] == 2390


def test_twitch_channel_without_saved_broadcasts_says_so(monkeypatch):
    _extracts(monkeypatch, {
        ("https://www.twitch.tv/streamer", False): {"is_live": True, "timestamp": time.time() - 60, "uploader": "Streamer"},
        # yt-dlp falls back to the live stream itself when no VOD started with it.
        ("https://www.twitch.tv/streamer", True): {"is_live": True, "formats": []},
    })
    with pytest.raises(LiveSourceError) as error:
        live_source.resolve_live_stream("https://www.twitch.tv/streamer")
    assert error.value.code == "LIVE_NO_RECORDING"
    assert "past broadcasts" in error.value.message


def test_offline_channel_is_not_live(monkeypatch):
    _extracts(monkeypatch, {
        ("https://www.twitch.tv/streamer", False): LiveSourceError("STREAM_NOT_LIVE", "not live"),
    })
    with pytest.raises(LiveSourceError) as error:
        live_source.resolve_live_stream("https://www.twitch.tv/streamer")
    assert error.value.code == "STREAM_NOT_LIVE"
    assert "isn't live right now" in error.value.message


def test_provider_failure_is_a_retryable_503(monkeypatch):
    _extracts(monkeypatch, {("https://kick.com/streamer", False): RuntimeError("proxy down")})
    with pytest.raises(LiveSourceError) as error:
        live_source.resolve_live_stream("https://kick.com/streamer")
    assert error.value.code == "LIVE_UNAVAILABLE" and error.value.status == 503


def test_kick_live_channel_resolves_to_the_vod_that_started_with_the_stream(monkeypatch):
    started = time.time() - 5600
    vod = "00000000-0000-4000-8000-000000000001"
    _extracts(monkeypatch, {
        ("https://kick.com/streamer", False): {"live_status": "is_live", "timestamp": started, "uploader": "Streamer", "title": "Live"},
        "https://kick.com/streamer/videos": {"entries": [
            {"url": f"https://kick.com/streamer/videos/{vod}#__youtubedl_smuggle=x", "timestamp": started - 3},
            {"url": "https://kick.com/streamer/videos/00000000-0000-4000-8000-000000000002", "timestamp": started - 90000},
        ]},
        f"https://kick.com/streamer/videos/{vod}": {"formats": [{"protocol": "m3u8_native", "url": "https://cdn/160.m3u8", "tbr": 230}]},
    })
    monkeypatch.setattr(live_source, "_playlist_seconds", lambda info, deadline: 5585.0)
    stream = live_source.resolve_live_stream("https://kick.com/streamer")
    assert stream.recording_url == f"https://kick.com/streamer/videos/{vod}"
    assert stream.recorded_seconds == 5585.0


def test_youtube_live_status_comes_from_the_data_api_without_the_proxy(monkeypatch):
    config = Config()
    config.youtube_data_api_key = "key"
    monkeypatch.setattr(live_source, "get_config", lambda: config)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"items": [{
                "snippet": {"title": "Podcast live", "channelTitle": "Host", "liveBroadcastContent": "live"},
                "liveStreamingDetails": {"actualStartTime": "2026-11-28T10:00:00Z"},
            }]}

    monkeypatch.setattr(live_source.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(live_source, "_youtube_ytdlp", lambda *a: pytest.fail("no proxy metadata needed"))
    stream = live_source.resolve_live_stream("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert stream.platform == "youtube"
    assert stream.recording_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert stream.channel == "Host"
    assert stream.recorded_seconds > 0


def test_finished_youtube_video_is_not_live(monkeypatch):
    config = Config()
    config.youtube_data_api_key = "key"
    monkeypatch.setattr(live_source, "get_config", lambda: config)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"items": [{"snippet": {"liveBroadcastContent": "none", "channelTitle": "Host"}}]}

    monkeypatch.setattr(live_source.requests, "get", lambda *a, **k: Response())
    with pytest.raises(LiveSourceError) as error:
        live_source.resolve_live_stream("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert error.value.code == "STREAM_NOT_LIVE"


def test_disabled_platform_is_refused(monkeypatch):
    config = Config()
    config.live_clipping_platforms = {"twitch"}
    monkeypatch.setattr(live_source, "get_config", lambda: config)
    with pytest.raises(LiveSourceError) as error:
        live_source.resolve_live_stream("https://kick.com/streamer")
    assert error.value.code == "LIVE_PLATFORM_UNAVAILABLE"


def test_playlist_seconds_sums_the_segments_so_far(monkeypatch):
    playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10.0,\n0.ts\n#EXTINF:10.0,\n1.ts\n#EXTINF:4.5,\n2.ts\n"

    class Raw:
        def read(self, *_a, **_k):
            return playlist.encode()

    class Response:
        raw = Raw()

        def raise_for_status(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(live_source.requests, "get", lambda *a, **k: Response())
    info = {"formats": [{"protocol": "m3u8_native", "url": "https://cdn/a.m3u8", "tbr": 230}]}
    assert live_source._playlist_seconds(info, time.monotonic() + 10) == 24.5


# --------------------------------------------------------- downloads


def test_live_vod_downloads_skip_actors_and_try_direct_first(monkeypatch):
    config = Config()
    config.twitch_download_providers = "proxy,direct,apify_twitch"
    config.kick_download_providers = "proxy,direct_retry,apify_kick_hls,apify_kick_mp4"
    config.live_prefer_direct = True
    monkeypatch.setattr(stream_source, "get_config", lambda: config)
    assert stream_source._live_provider_order("twitch") == ["direct", "proxy"]
    assert stream_source._live_provider_order("kick") == ["direct_retry", "proxy"]
    config.live_prefer_direct = False
    assert stream_source._live_provider_order("twitch") == ["proxy", "direct"]


def test_live_range_marks_the_operation_live(monkeypatch):
    calls = []
    monkeypatch.setattr(stream_source, "_download", lambda url, **kwargs: calls.append(kwargs) or Path("a.m4a"))
    stream_source.download_stream_range_audio(
        "https://www.twitch.tv/videos/1", SourceRange(10, 200.0, 300.0, live=True), "task1", operation="job",
    )
    assert calls[0]["live"] is True
