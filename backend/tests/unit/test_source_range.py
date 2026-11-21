"""Clip part of a long video: admission, billing, caches, downloads and the pipeline."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src import source_range as module
from src import stream_source, youtube_proxy, youtube_source
from src.api.routes import tasks
from src.config import Config
from src.services import video_service as video_module
from src.services.task_service import TaskService
from src.services.video_service import VideoService
from src.source_range import (
    MIN_SOURCE_RANGE_SECONDS,
    RequestedRange,
    SourceRange,
    SourceRangeError,
    parse_requested_range,
    resolve_source_range,
    shift_segment,
)
from src.youtube_source import build_time_map, copy_transcript_sidecar, load_time_map, save_time_map

YOUTUBE_URL = "https://youtu.be/abcdefghijk"
TWITCH_URL = "https://www.twitch.tv/videos/123456"
EIGHT_HOURS = 8 * 3600


# ---------------------------------------------------------------- validation

def test_no_range_requested_is_none():
    assert parse_requested_range({"url": YOUTUBE_URL}) is None
    assert parse_requested_range({"url": YOUTUBE_URL, "start_seconds": None, "end_seconds": ""}) is None


@pytest.mark.parametrize(
    "source, message",
    [
        ({"start_seconds": "abc"}, "must be a number"),
        ({"start_seconds": True}, "must be a number"),
        ({"end_seconds": -5}, "zero or more"),
        ({"start_seconds": float("nan")}, "zero or more"),
        ({"start_seconds": 600, "end_seconds": 600}, "must come after"),
    ],
)
def test_malformed_ranges_are_rejected(source, message):
    with pytest.raises(SourceRangeError, match=message) as error:
        parse_requested_range(source)
    assert error.value.to_detail()["code"] == "INVALID_SOURCE_RANGE"
    assert error.value.to_detail()["min_range_seconds"] == MIN_SOURCE_RANGE_SECONDS


def test_range_defaults_and_rounds_to_whole_seconds():
    with pytest.raises(SourceRangeError, match="Your plan allows up to 120 minutes"):
        resolve_source_range(RequestedRange(3600.4, None), EIGHT_HOURS, 7200)
    part = resolve_source_range(RequestedRange(None, 5400), EIGHT_HOURS, 7200)
    assert (part.start, part.end, part.length, part.source_duration) == (0, 5400.0, 5400.0, EIGHT_HOURS)
    part = resolve_source_range(RequestedRange(3600.4, 9000), EIGHT_HOURS, 7200)
    assert part.start == 3600


@pytest.mark.parametrize(
    "requested, message",
    [
        (RequestedRange(EIGHT_HOURS + 10, None), "starts after the video ends"),
        (RequestedRange(0, EIGHT_HOURS + 60), "ends after the video ends"),
        (RequestedRange(100, 130), "at least 1 minute"),
        (RequestedRange(0, 7300), "Pick a shorter part"),
    ],
)
def test_ranges_are_checked_against_duration_and_plan(requested, message):
    with pytest.raises(SourceRangeError, match=message) as error:
        resolve_source_range(requested, EIGHT_HOURS, 7200)
    detail = error.value.to_detail()
    assert detail["duration_seconds"] == EIGHT_HOURS
    assert detail["max_duration_seconds"] == 7200


def test_end_a_little_past_the_probed_length_is_clamped():
    part = resolve_source_range(RequestedRange(3600, 7201.5), 7200.2, 7200)
    assert part.end == pytest.approx(7200.2)


def test_whole_video_range_is_no_range():
    assert resolve_source_range(RequestedRange(0, 3000), 3000.3, 7200) is None


def test_metadata_round_trip_and_cache_suffix():
    part = SourceRange(3600, 9000.0, EIGHT_HOURS)
    stored = part.to_metadata()
    assert stored == {"start_seconds": 3600, "end_seconds": 9000.0, "source_duration_seconds": EIGHT_HOURS}
    assert SourceRange.from_metadata({"source_range": stored}) == part
    assert SourceRange.from_metadata({}) is None
    assert SourceRange.from_metadata({"source_range": {"start_seconds": 10, "end_seconds": 5}}) is None
    assert part.cache_suffix() != SourceRange(3600, 9001.0).cache_suffix()


def test_public_fields_and_media_duration():
    metadata = {"source_range": SourceRange(60, 7260.0, EIGHT_HOURS).to_metadata()}
    assert module.public_range_fields(metadata) == {"source_start_seconds": 60, "source_end_seconds": 7260.0}
    assert module.public_range_fields({}) == {"source_start_seconds": None, "source_end_seconds": None}
    # A part's task stores the charged part; streams need the whole VOD's length.
    assert module.media_duration({"source_duration_seconds": 7200}, metadata) == EIGHT_HOURS
    assert module.media_duration({"source_duration_seconds": 7200}, {}) == 7200


def test_shift_segment_moves_every_time_onto_the_source():
    shifted = shift_segment(
        {
            "start_time": "01:05", "end_time": "01:40", "hook_start_time": "01:10", "hook_end_time": None,
            "source_ranges": [[0.0, 45.0]], "text": "x",
        },
        3600,
    )
    assert shifted["start_time"] == "61:05"
    assert shifted["end_time"] == "61:40"
    assert shifted["hook_start_time"] == "61:10"
    assert shifted["hook_end_time"] is None
    assert shifted["source_ranges"] == [[3600.0, 3645.0]]
    assert shifted["text"] == "x"


# ---------------------------------------------------------------- admission and billing

@pytest.fixture
def creation(monkeypatch):
    billing = {"max_youtube_duration_seconds": 7200, "max_upload_duration_seconds": 7200}
    monkeypatch.setattr(tasks.BillingService, "assert_can_create_task", AsyncMock(return_value=billing))
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    saved = AsyncMock()
    monkeypatch.setattr(tasks, "_save_task_source_metadata", saved)
    service = SimpleNamespace(
        video_service=VideoService(),
        create_task_with_source=AsyncMock(return_value="task-1"),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    queue = SimpleNamespace(enqueue_processing_job=AsyncMock(return_value="job-1"))

    def request(source, **body):
        return SimpleNamespace(
            json=AsyncMock(return_value={"source": source, **body}),
            app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
        )

    return request, service, queue, saved


@pytest.mark.asyncio
async def test_whole_long_video_is_rejected_with_a_range_offer(monkeypatch, creation):
    request, service, _queue, _saved = creation
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": EIGHT_HOURS}))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request({"url": YOUTUBE_URL}), db=None)
    detail = error.value.detail
    assert error.value.status_code == 422
    assert detail["code"] == "VIDEO_TOO_LONG"
    assert detail["duration_seconds"] == EIGHT_HOURS
    assert detail["max_duration_seconds"] == 7200
    assert detail["range_allowed"] is True
    assert "Pick a part up to 120 minutes" in detail["message"]
    service.create_task_with_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_part_of_a_long_video_is_admitted_and_charged_for_the_part(monkeypatch, creation):
    request, service, queue, saved = creation
    monkeypatch.setattr(
        tasks, "async_get_youtube_video_info",
        AsyncMock(return_value={"duration": EIGHT_HOURS, "title": "Stream"}),
    )

    result = await tasks.create_task(
        request({"url": YOUTUBE_URL, "start_seconds": 3600, "end_seconds": 10800}), db=None,
    )

    assert result["task_id"] == "task-1"
    assert service.create_task_with_source.await_args.kwargs["source_duration_seconds"] == 7200
    # The allowance check inside the guard is sized to the part, not the stream.
    assert tasks.BillingService.assert_can_create_task.await_args.args[-1] == 7200
    metadata = saved.await_args.args[1]
    assert metadata["source_range"] == {
        "start_seconds": 3600, "end_seconds": 10800.0, "source_duration_seconds": EIGHT_HOURS,
    }
    queue.enqueue_processing_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_part_longer_than_the_plan_is_rejected_before_any_charge(monkeypatch, creation):
    request, service, _queue, _saved = creation
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": EIGHT_HOURS}))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request({"url": YOUTUBE_URL, "start_seconds": 0, "end_seconds": 7300}), db=None)
    assert error.value.detail["code"] == "INVALID_SOURCE_RANGE"
    assert error.value.detail["max_duration_seconds"] == 7200
    # Only the up-front plan check ran; nothing was charged against the guard.
    assert tasks.BillingService.assert_can_create_task.await_count == 1
    service.create_task_with_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_range_is_rejected_before_any_lookup(monkeypatch, creation):
    request, service, _queue, _saved = creation
    info = AsyncMock()
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", info)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request({"url": YOUTUBE_URL, "start_seconds": "soon"}), db=None)
    assert error.value.detail["code"] == "INVALID_SOURCE_RANGE"
    info.assert_not_awaited()


@pytest.mark.asyncio
async def test_range_only_works_with_highlights(monkeypatch, creation):
    request, _service, _queue, _saved = creation
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": EIGHT_HOURS}))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            request({"url": YOUTUBE_URL, "start_seconds": 0, "end_seconds": 3600}, workflow="recap"), db=None,
        )
    assert error.value.detail["code"] == "INVALID_SOURCE_RANGE"
    assert "highlights" in error.value.detail["message"]


@pytest.mark.asyncio
async def test_twitch_part_uses_the_range_length(monkeypatch, creation):
    request, service, _queue, _saved = creation
    preflight = AsyncMock(return_value={"duration": EIGHT_HOURS, "title": "VOD"})
    monkeypatch.setattr(tasks, "_preflight_stream_source", preflight)
    await tasks.create_task(request({"url": TWITCH_URL, "start_seconds": 600, "end_seconds": 4200}), db=None)
    # The whole VOD is not held against the plan limit.
    assert preflight.await_args.args[2] is None
    assert service.create_task_with_source.await_args.kwargs["source_duration_seconds"] == 3600


@pytest.mark.asyncio
async def test_twitch_whole_vod_over_the_limit_offers_a_range(monkeypatch):
    monkeypatch.setattr(tasks, "run_in_thread", AsyncMock(return_value={"duration": EIGHT_HOURS}))
    with pytest.raises(HTTPException) as error:
        await tasks._preflight_stream_source(TWITCH_URL, "twitch", 7200)
    assert error.value.detail["range_allowed"] is True
    assert error.value.detail["duration_seconds"] == EIGHT_HOURS
    assert "Twitch video" in error.value.detail["message"]


@pytest.mark.asyncio
async def test_upload_over_the_limit_offers_a_range(monkeypatch):
    monkeypatch.setattr(tasks, "run_in_thread", AsyncMock(return_value=float(EIGHT_HOURS)))
    with pytest.raises(HTTPException) as error:
        await tasks._preflight_upload_source("upload://video.mp4", "user-1", 7200)
    assert error.value.detail["code"] == "VIDEO_TOO_LONG"
    assert error.value.detail["range_allowed"] is True
    assert await tasks._preflight_upload_source("upload://video.mp4", "user-1", None) == EIGHT_HOURS


# ---------------------------------------------------------------- cache keys

def test_whole_video_cache_key_is_unchanged():
    import hashlib

    from src.services.task_service import TRANSCRIPT_ANALYSIS_CACHE_VERSION

    expected = hashlib.sha256(
        f"youtube|fast|{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{YOUTUBE_URL}".encode()
    ).hexdigest()
    assert TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast") == expected
    assert TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast", None, None) == expected


def test_each_part_gets_its_own_transcript_cache():
    whole = TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast")
    first = TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast", None, SourceRange(0, 7200.0))
    second = TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast", None, SourceRange(7200, 14400.0))
    again = TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast", None, SourceRange(0, 7200.0, 9.0))
    assert len({whole, first, second}) == 3
    assert first == again


# ---------------------------------------------------------------- media helpers

def _media(path: Path, seconds: int, *, video: bool = True) -> Path:
    inputs = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        inputs = ["-f", "lavfi", "-i", f"testsrc=size=160x90:rate=10:duration={seconds}", *inputs]
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs, "-shortest", "-c:a", "aac",
         *(["-c:v", "libx264", "-preset", "ultrafast"] if video else []), str(path)],
        check=True,
    )
    return path


def _duration(path: Path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


def test_cut_audio_range_writes_the_part_and_its_timeline(tmp_path):
    source = _media(tmp_path / "source.mp4", 30)
    part = SourceRange(10, 20.0)
    cut = module.cut_audio_range(source, tmp_path / "part" / "analysis.m4a", part)
    assert _duration(cut) == pytest.approx(10.0, abs=0.1)
    time_map = load_time_map(cut)
    assert time_map.to_original(0.0) == 10.0
    assert time_map.to_original(5.0) == 15.0


def test_cut_audio_range_accounts_for_a_section_that_starts_early(tmp_path):
    section = _media(tmp_path / "section.m4a", 20, video=False)
    # The section holds 3590-3610 of the source; the part is 3600-3605.
    cut = module.cut_audio_range(section, tmp_path / "a.m4a", SourceRange(3600, 3605.0), offset=3590.0)
    assert _duration(cut) == pytest.approx(5.0, abs=0.1)
    assert load_time_map(cut).to_original(0.0) == 3600.0


def test_part_transcript_is_copied_onto_the_source_timeline(tmp_path):
    audio = tmp_path / "analysis.m4a"
    audio.write_bytes(b"a")
    save_time_map(audio, SourceRange(3600, 4200.0).time_map())
    audio.with_suffix(".transcript_cache.json").write_text(json.dumps({
        "words": [{"text": "hi", "start": 61000, "end": 61500}],
        "utterances": [{"start": 61000, "end": 61500, "words": [{"text": "hi", "start": 61000, "end": 61500}]}],
    }))
    full = tmp_path / "render.mp4"
    full.write_bytes(b"v")
    copy_transcript_sidecar(audio, full)
    words = json.loads(full.with_suffix(".transcript_cache.json").read_text())["words"]
    assert words == [{"text": "hi", "start": 3661000, "end": 3661500}]

    stitched = tmp_path / "sections.mp4"
    stitched.write_bytes(b"v")
    copy_transcript_sidecar(audio, stitched, build_time_map([(3660.0, 3670.0)]))
    payload = json.loads(stitched.with_suffix(".transcript_cache.json").read_text())
    assert payload["words"] == [{"text": "hi", "start": 1000, "end": 1500}]
    assert payload["utterances"][0]["start"] == 1000


def test_whole_audio_sidecar_copy_is_unchanged(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"a")
    audio.with_suffix(".transcript_cache.json").write_text(json.dumps({"words": [{"text": "hi", "start": 61000, "end": 61500}]}))
    full = tmp_path / "full.mp4"
    copy_transcript_sidecar(audio, full)
    assert json.loads(full.with_suffix(".transcript_cache.json").read_text())["words"][0]["start"] == 61000


# ---------------------------------------------------------------- YouTube download of the part

def test_youtube_part_downloads_only_its_audio_fragments(tmp_path, monkeypatch):
    fragments = [{"url": f"https://media/{i}", "duration": 10.0} for i in range(1000)]
    fmt = {"format_id": "233", "protocol": "m3u8_native", "url": "https://media/playlist", "fragments": fragments}
    monkeypatch.setattr(youtube_proxy, "extract_video_info", lambda *_a: {"formats": [fmt], "duration": 10000})
    monkeypatch.setattr(youtube_proxy, "pick_hls_audio_format", lambda *_a, **_k: fmt)
    monkeypatch.setattr(youtube_proxy, "expand_hls_format", lambda f, *_a: f)
    monkeypatch.setattr(youtube_proxy, "contain_raw_aac", lambda path: path)
    downloaded = {}

    def native(options, url, video_id, chosen, dest_stem, selected=None):
        downloaded["fragments"] = selected
        path = Path(f"{dest_stem}.m4a")
        path.write_bytes(b"x" * 100)
        return path

    monkeypatch.setattr(youtube_proxy, "native_download_format", native)
    options = {"_youtube_deadline": 1e12, "_youtube_overall_deadline": 1e12}
    path, start, end = youtube_proxy._download_audio_section_attempt(
        YOUTUBE_URL, "abcdefghijk", 3600.0, 3720.0, tmp_path / "stem",
        task_id="t", budget=youtube_proxy.ByteBudget(10_000), options=options,
    )
    assert (start, end) == (3600.0, 3720.0)
    # 12 ten-second fragments, never the whole 10 000 s stream.
    assert [f["url"] for f in downloaded["fragments"]] == [f"https://media/{i}" for i in range(360, 372)]
    assert path.exists()


def test_youtube_part_without_timed_audio_refuses_an_ffmpeg_section(tmp_path, monkeypatch):
    fmt = {"format_id": "140", "protocol": "https", "url": "https://media/file"}
    monkeypatch.setattr(youtube_proxy, "extract_video_info", lambda *_a: {"formats": [fmt], "duration": 10000})
    monkeypatch.setattr(youtube_proxy, "pick_hls_audio_format", lambda *_a, **_k: None)
    with pytest.raises(youtube_proxy.PartialDownloadUnavailable):
        youtube_proxy._download_audio_section_attempt(
            YOUTUBE_URL, "abcdefghijk", 0.0, 60.0, tmp_path / "stem",
            task_id="t", budget=youtube_proxy.ByteBudget(10_000), options={},
        )


@pytest.fixture
def yt_cache(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = "http://user:pass@proxy.invalid:1/{session}"
    monkeypatch.setattr(youtube_source, "get_config", lambda: config)
    monkeypatch.setattr(youtube_source, "_restore_remote_cache", lambda *_a: None)
    return config


def test_range_audio_reuses_cached_full_audio(tmp_path, monkeypatch, yt_cache):
    cached = _media(tmp_path / "cached-audio.m4a", 30, video=False)
    monkeypatch.setattr(youtube_source, "_audio_path", lambda *_a: cached)
    section = MagicMock()
    monkeypatch.setattr(youtube_source, "download_audio_section", section)
    path = youtube_source.download_range_audio(
        YOUTUBE_URL, "abcdefghijk", SourceRange(5, 15.0), tmp_path / "work" / "analysis.m4a",
    )
    section.assert_not_called()
    assert _duration(path) == pytest.approx(10.0, abs=0.1)
    assert load_time_map(path).to_original(0.0) == 5.0


def test_range_audio_fetches_the_section_then_falls_back_to_full_audio(tmp_path, monkeypatch, yt_cache):
    monkeypatch.setattr(youtube_source, "_audio_path", lambda *_a: None)
    section_file = _media(tmp_path / "section.m4a", 20, video=False)
    monkeypatch.setattr(
        youtube_source, "download_audio_section",
        MagicMock(return_value=(section_file, 3595.0, 3615.0)),
    )
    full = MagicMock()
    monkeypatch.setattr(youtube_source, "download_audio_for_job", full)
    path = youtube_source.download_range_audio(
        YOUTUBE_URL, "abcdefghijk", SourceRange(3600, 3610.0), tmp_path / "w1" / "analysis.m4a",
    )
    full.assert_not_called()
    assert youtube_source.download_audio_section.call_args.args[2:4] == (3600.0, 3610.0)
    assert _duration(path) == pytest.approx(10.0, abs=0.1)

    monkeypatch.setattr(
        youtube_source, "download_audio_section",
        MagicMock(side_effect=youtube_proxy.PartialDownloadUnavailable("no timed fragments")),
    )
    whole = _media(tmp_path / "whole.m4a", 30, video=False)
    monkeypatch.setattr(youtube_source, "download_audio_for_job", MagicMock(return_value=whole))
    path = youtube_source.download_range_audio(
        YOUTUBE_URL, "abcdefghijk", SourceRange(10, 20.0), tmp_path / "w2" / "analysis.m4a",
    )
    assert _duration(path) == pytest.approx(10.0, abs=0.1)


# ---------------------------------------------------------------- Twitch/Kick download of the part

def test_stream_part_downloads_audio_for_the_range_without_the_plan_cap(monkeypatch):
    calls = []
    monkeypatch.setattr(stream_source, "_download", lambda url, **kwargs: calls.append(kwargs) or Path("a.m4a"))
    stream_source.download_stream_range_audio(
        TWITCH_URL, SourceRange(3600, 10800.0, EIGHT_HOURS), "task1", operation="job",
    )
    assert calls == [{
        "audio": True, "ranges": [(3600.0, 10800.0)], "expected_duration": EIGHT_HOURS,
        "max_duration": None, "task_id": "task1", "operation": "job",
    }]


def test_stream_part_from_a_whole_download_is_cut_as_audio(tmp_path):
    vod = _media(tmp_path / "vod.mp4", 30)
    path = stream_source._from_local(vod, tmp_path, audio=True, ranges=[(5.0, 25.0)])
    assert _duration(path) == pytest.approx(20.0, abs=0.1)
    assert load_time_map(path).to_original(0.0) == 5.0


def test_stream_audio_hls_sections_ask_for_audio_renditions():
    options = stream_source._ydl_options("direct", Path("x"), audio=True, media=True)
    assert options["format"].startswith("bestaudio")


# ---------------------------------------------------------------- pipeline

class _Analysis:
    summary = "Summary"
    key_topics = ["topic"]

    def __init__(self, segments):
        self.most_relevant_segments = segments


def _segment(start, end):
    from src.ai import TranscriptSegment
    return TranscriptSegment(
        start_time=start, end_time=end, text="hello world", relevance_score=0.9, reasoning="good",
        hook_title=None, hook_start_time=None, hook_end_time=None,
    )


@pytest.fixture
def pipeline_config(monkeypatch, tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_section_padding_seconds = 0.0
    monkeypatch.setattr(video_module, "get_config", lambda: config)
    monkeypatch.setattr(stream_source, "get_config", lambda: config)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    stream_source._operations.clear()
    yield config
    stream_source._operations.clear()


def _transcribe_part(monkeypatch, segments, sources):
    async def transcript(path, processing_mode="fast", source_url=None):
        sources.append(source_url)
        Path(path).with_suffix(".transcript_cache.json").write_text(json.dumps({
            "words": [{"text": "hello", "start": 61000, "end": 61500}], "utterances": [],
        }))
        return "[01:01 - 01:02] hello world"

    monkeypatch.setattr(VideoService, "generate_transcript", staticmethod(transcript))
    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(AsyncMock(return_value=_Analysis(segments))))
    monkeypatch.setattr(video_module, "build_clip_signal_summary", lambda *_: None)


async def test_stream_part_is_analysed_alone_and_clipped_on_the_vod_timeline(pipeline_config, monkeypatch):
    part = SourceRange(3600, 4200.0, EIGHT_HOURS)
    sections = {}

    def range_audio(url, source_range, task_id, *, operation=None):
        path = Path(pipeline_config.temp_dir) / "stream-work" / task_id / operation / "c" / "selected-audio.m4a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"a")
        save_time_map(path, source_range.time_map())
        return path

    def section_download(url, segments, duration, task_id, *, max_duration=None, operation=None):
        sections.update(segments=segments, duration=duration, max_duration=max_duration)
        path = Path(pipeline_config.temp_dir) / "stream-work" / task_id / operation / "c" / "selected-segments.mkv"
        path.write_bytes(b"v")
        save_time_map(path, build_time_map([(3660.0, 3670.0)]))
        return path

    monkeypatch.setattr(video_module, "download_stream_range_audio", range_audio)
    monkeypatch.setattr(video_module, "download_stream_audio", lambda *a, **k: pytest.fail("whole audio"))
    monkeypatch.setattr(video_module, "download_stream_sections", section_download)
    monkeypatch.setattr(video_module, "download_stream_video", lambda *a, **k: pytest.fail("full download"))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 600.0))
    sources = []
    _transcribe_part(monkeypatch, [_segment("01:00", "01:10")], sources)

    result = await VideoService.process_video_complete(
        TWITCH_URL, "twitch", task_id="task1", max_video_duration=7200, source_range=part,
    )

    segment = result["segments_to_render"][0]
    assert (segment["start_time"], segment["end_time"]) == ("61:00", "61:10")
    assert sections["segments"][0]["start_time"] == "61:00"
    # Padding is clamped to the VOD, never to the part's own length.
    assert sections["duration"] == EIGHT_HOURS
    assert sections["max_duration"] is None
    words = json.loads(Path(result["video_path"]).with_suffix(".transcript_cache.json").read_text())["words"]
    assert words == [{"text": "hello", "start": 1000, "end": 1500}]
    assert result["source_range_dir"]


async def test_part_longer_than_the_plan_fails_before_download(pipeline_config, monkeypatch):
    monkeypatch.setattr(video_module, "download_stream_range_audio", lambda *a, **k: pytest.fail("download"))
    with pytest.raises(Exception, match="selected part is too long"):
        await VideoService.process_video_complete(
            TWITCH_URL, "twitch", task_id="task1", max_video_duration=3600,
            source_range=SourceRange(0, 7200.0, EIGHT_HOURS),
        )


async def test_upload_part_is_trimmed_before_transcription(pipeline_config, monkeypatch, tmp_path):
    upload = _media(tmp_path / "upload.mp4", 40)
    monkeypatch.setattr(VideoService, "resolve_local_video_path", staticmethod(lambda _url: upload))
    monkeypatch.setattr(video_module, "materialize_stored_upload", AsyncMock(return_value=upload))
    monkeypatch.setattr(video_module, "upload_needs_remote_copy", AsyncMock(return_value=False))
    monkeypatch.setattr(VideoService, "prepare_source", staticmethod(AsyncMock(side_effect=lambda path: path)))
    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(lambda _path, segments: segments))
    transcribed = []

    async def transcript(path, processing_mode="fast", source_url=None):
        transcribed.append(_duration(Path(path)))
        Path(path).with_suffix(".transcript_cache.json").write_text(json.dumps({
            "words": [{"text": "hello", "start": 2000, "end": 2500}], "utterances": [],
        }))
        return "[00:02 - 00:03] hello world"

    monkeypatch.setattr(VideoService, "generate_transcript", staticmethod(transcript))
    monkeypatch.setattr(
        VideoService, "analyze_transcript", staticmethod(AsyncMock(return_value=_Analysis([_segment("00:01", "00:09")]))),
    )
    monkeypatch.setattr(video_module, "build_clip_signal_summary", lambda *_: None)

    result = await VideoService.process_video_complete(
        "upload://upload.mp4", "upload", task_id="task1", max_video_duration=7200,
        source_range=SourceRange(20, 40.0, 40.0),
    )

    # Only the part's 20 s were transcribed; the clip plays 21-29 s of the upload.
    assert transcribed == [pytest.approx(20.0, abs=0.2)]
    segment = result["segments_to_render"][0]
    assert (segment["start_time"], segment["end_time"]) == ("00:21", "00:29")
    render = Path(result["video_path"])
    assert render != upload and render.samefile(upload)
    assert json.loads(render.with_suffix(".transcript_cache.json").read_text())["words"][0]["start"] == 22000
    # The shared upload never receives the part's word timings.
    assert not upload.with_suffix(".transcript_cache.json").exists()


async def test_youtube_part_uses_its_audio_and_skips_whole_video_captions(pipeline_config, monkeypatch):
    pipeline_config.youtube_proxy_url = "http://user:pass@proxy.invalid:1/{session}"
    part = SourceRange(3600, 4200.0, EIGHT_HOURS)
    monkeypatch.setattr(
        video_module, "async_get_youtube_video_info", AsyncMock(return_value={"duration": EIGHT_HOURS}),
    )
    monkeypatch.setattr(video_module, "cached_full_media", lambda *_a: None)
    calls = {}

    def range_audio(url, video_id, source_range, dest, *, task_id=None, budget=None):
        calls["range"] = (source_range.start, source_range.end)
        dest.write_bytes(b"a")
        save_time_map(dest, source_range.time_map())
        return dest

    def sections(url, video_id, segments, *, duration=None, audio_path=None, **_kwargs):
        calls.update(segments=segments, duration=duration)
        path = Path(pipeline_config.temp_dir) / "youtube-work" / "x" / "render.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"v")
        time_map = build_time_map([(3660.0, 3670.0)])
        save_time_map(path, time_map)
        copy_transcript_sidecar(audio_path, path, time_map)
        return path

    monkeypatch.setattr(video_module, "download_range_audio", range_audio)
    monkeypatch.setattr(video_module, "download_analysis_source", lambda *a, **k: pytest.fail("whole audio"))
    monkeypatch.setattr(video_module, "materialize_clip_source", sections)
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 600.0))
    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(lambda _path, segments: segments))
    sources = []
    _transcribe_part(monkeypatch, [_segment("01:00", "01:10")], sources)

    result = await VideoService.process_video_complete(
        "https://www.youtube.com/watch?v=abcdefghijk", "youtube", task_id="task1",
        max_video_duration=7200, source_range=part,
    )

    assert calls["range"] == (3600, 4200.0)
    assert calls["segments"][0]["start_time"] == "61:00"
    assert calls["duration"] == EIGHT_HOURS
    # YouTube's captions cover the whole video, so the part is transcribed itself.
    assert sources == [None]
    words = json.loads(Path(result["video_path"]).with_suffix(".transcript_cache.json").read_text())["words"]
    assert words == [{"text": "hello", "start": 1000, "end": 1500}]


# ---------------------------------------------------------------- worker: resume and caches

@pytest.mark.parametrize("stored", [None, SourceRange(600, 4200.0, EIGHT_HOURS)])
async def test_worker_reads_the_part_from_the_task_and_keeps_whole_video_keys(stored):
    from tests.unit.test_task_service import build_clip_result, build_task_service

    service = build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(return_value={"status": "queued"})
    service.task_repo.load_source_metadata = AsyncMock(
        return_value={"source_range": stored.to_metadata()} if stored else {}
    )
    service.cache_repo.get_cache = AsyncMock(return_value={"transcript_text": "T", "analysis_json": "{}"})
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=None)
    service.video_service.create_single_clip = AsyncMock(return_value=build_clip_result())
    segments = [{"start_time": "11:00", "end_time": "11:30", "text": "x"}]
    service.video_service.process_video_complete = AsyncMock(return_value={
        "clips": [], "segments_to_render": segments, "video_path": "/tmp/source.mp4", "segments": segments,
        "summary": "S", "key_topics": [], "transcript": "T", "analysis_json": "{}",
        "source_range_dir": "/tmp/part" if stored else None,
    })

    # The first run, retries and resumes all read the part from the task row.
    await service.process_task(task_id="task-1", url=YOUTUBE_URL, source_type="youtube")

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    source_key = TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast", None, stored)
    assert service.cache_repo.get_cache.await_args_list[0].args[1] == source_key
    write = service.cache_repo.upsert_cache.await_args_list[0].kwargs
    if stored is None:
        assert "source_range" not in kwargs
        assert kwargs["cached_analysis_json"] == "{}"
        assert write["analysis_json"] == "{}"
    else:
        assert kwargs["source_range"] == stored
        # A part's analysis is on the source timeline already: never read or stored.
        assert kwargs["cached_analysis_json"] is None
        assert write["analysis_json"] is None
        assert write["cache_key"] == source_key


async def test_range_transcript_for_editing_comes_from_the_checkpoint(tmp_path):
    part = SourceRange(3600, 4200.0)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: {"words": [{"text": "hi", "start": 61000, "end": 61500}], "utterances": []},
    )))
    original = await module.range_transcript(db, "task-1", {"source_range": part.to_metadata()})
    assert original["words"] == [{"text": "hi", "start": 3661000, "end": 3661500}]
    stitched = tmp_path / "clean.mp4"
    save_time_map(stitched, build_time_map([(3660.0, 3670.0)]))
    assert module.transcript_on_media(original, stitched)["words"][0]["start"] == 1000
    assert await module.range_transcript(db, "task-1", {}) is None


# ---------------------------------------------------------------- clip edits on a part's task

def _checkpoint_db(words):
    return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: {"words": words, "utterances": []},
    )))


def test_part_words_go_on_a_private_link_never_beside_the_shared_file(tmp_path):
    root = tmp_path / "source-ranges"
    stitched = tmp_path / "youtube-cache" / "render.mp4"
    stitched.parent.mkdir()
    stitched.write_bytes(b"v")
    save_time_map(stitched, build_time_map([(3660.0, 3670.0)]))
    original = {"words": [{"text": "hi", "start": 3661000, "end": 3661500}], "utterances": []}

    link = module.link_with_part_words(stitched, original, root)

    assert link.samefile(stitched)
    assert load_time_map(link).to_original(1.0) == 3661.0
    assert json.loads(link.with_suffix(".transcript_cache.json").read_text())["words"][0]["start"] == 1000
    assert not stitched.with_suffix(".transcript_cache.json").exists()
    module.release_part_link(stitched, root)  # not a link: left alone
    assert stitched.exists()
    module.release_part_link(link, root)
    assert not link.parent.exists() and stitched.exists()


async def test_clip_edit_on_a_whole_video_task_is_unchanged(tmp_path):
    service = TaskService.__new__(TaskService)
    service.config = SimpleNamespace(temp_dir=str(tmp_path))
    service.db = _checkpoint_db([{"text": "hi", "start": 1000, "end": 1500}])
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical"})
    source = tmp_path / "source.mp4"
    source.write_bytes(b"v")
    assert await service._with_part_words("task", source) == source
    service.db.execute.assert_not_awaited()

    # A file that already has word timings keeps them, part or not.
    source.with_suffix(".transcript_cache.json").write_text(json.dumps({"words": [{"text": "x", "start": 0, "end": 1}]}))
    service._load_task_source_settings = AsyncMock(
        return_value={"source_range": SourceRange(3600, 4200.0).to_metadata()}
    )
    assert await service._with_part_words("task", source) == source


async def test_regenerating_a_part_tasks_clips_keeps_subtitle_timings(isolated_clip_edits, pipeline_config, monkeypatch, tmp_path):
    from src.services import clip_service

    sections = []

    def section_download(url, segments, duration, task_id, **kwargs):
        sections.append(duration)
        path = Path(pipeline_config.temp_dir) / "stream-work" / task_id / "edit-1" / "c" / "selected-segments.mkv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"v")
        save_time_map(path, build_time_map([(3660.0, 3675.0)]))
        return path

    monkeypatch.setattr(clip_service, "download_stream_sections", section_download)
    part = SourceRange(3600, 4200.0, EIGHT_HOURS)
    clip = {"id": "clip", "task_id": "task", "file_path": str(tmp_path / "clip.mp4"),
            "start_time": "61:00", "end_time": "61:10", "text": "hello"}
    # Analysis words are on the part's own timeline: 61 s into the part is 3661 s into the VOD.
    service = TaskService(db=_checkpoint_db([{"text": "hello", "start": 61000, "end": 61500}]), config=pipeline_config)
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "source_url": TWITCH_URL, "source_type": "twitch", "source_duration_seconds": 600,
    })
    service.task_repo.load_source_metadata = AsyncMock(return_value={"source_range": part.to_metadata()})
    service.task_repo.update_task_clips = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[clip])
    service.clip_repo.delete_clips_by_task = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="clip")
    service._load_task_source_settings = AsyncMock(return_value={
        "output_format": "vertical", "add_subtitles": True, "source_range": part.to_metadata(),
    })
    rendered = {}

    async def render(video_path, segments, *args, **kwargs):
        rendered["words"] = json.loads(Path(video_path).with_suffix(".transcript_cache.json").read_text())["words"]
        rendered["path"] = Path(video_path)
        return [{"clip_id": 1, "filename": "new.mp4", "path": "new.mp4",
                 "start_time": "00:00", "end_time": "00:10", "duration": 10.0}]

    service.video_service.create_video_clips = render

    await service.regenerate_all_clips_for_task("task", None, None, None, "default")

    # The VOD's length for the section download, and the words on the sections' timeline.
    assert sections == [EIGHT_HOURS]
    assert rendered["words"] == [{"text": "hello", "start": 1000, "end": 1500}]
    # The private link and the stream download are both gone afterwards.
    assert not rendered["path"].exists()
    assert not (Path(pipeline_config.temp_dir) / "stream-work" / "task").exists()


def test_resolve_source_range_allows_long_part_when_uncapped():
    part = resolve_source_range(RequestedRange(600.0, 15000.0), EIGHT_HOURS, None)
    assert part == SourceRange(600.0, 15000.0, EIGHT_HOURS)
