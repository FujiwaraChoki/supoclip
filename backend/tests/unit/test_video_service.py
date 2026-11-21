import json
from types import SimpleNamespace

import pytest

from src.services import video_service as video_service_module
from src.services.video_service import VideoService, resolve_upload_for_edit


@pytest.fixture(autouse=True)
def isolated_upload_lookup(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(video_service_module, "materialize_stored_upload", AsyncMock(return_value=None))
    monkeypatch.setattr(video_service_module, "upload_needs_remote_copy", AsyncMock(return_value=False))


class _EmptyAnalysis:
    summary = "No strong standalone segment found"
    key_topics = []
    most_relevant_segments = []


@pytest.mark.asyncio
async def test_process_video_complete_uses_fallback_when_ai_selects_no_segments(
    monkeypatch, tmp_path
):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")

    config = SimpleNamespace(
        max_video_duration=5400,
        clip_duration=30,
        max_clips=10,
        fast_mode_max_clips=4,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        VideoService,
        "resolve_local_video_path",
        staticmethod(lambda _url: video_path),
    )
    monkeypatch.setattr(
        VideoService,
        "_get_file_duration",
        staticmethod(lambda _path: 42.0),
    )

    async def fake_generate_transcript(
        _video_path, processing_mode="balanced", source_url=None
    ):
        return "[00:00 - 00:01] hello"

    async def fake_analyze_transcript(_transcript, clip_signals=None, performance_context=None, clip_length="auto"):
        return _EmptyAnalysis()

    async def fake_run_in_thread(_func, *_args, **_kwargs):
        if _func == VideoService._drop_static_segments:
            return _args[1]
        return None

    monkeypatch.setattr(
        VideoService,
        "generate_transcript",
        staticmethod(fake_generate_transcript),
    )
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        staticmethod(fake_analyze_transcript),
    )
    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)

    result = await VideoService.process_video_complete(
        url="upload://source.mp4",
        source_type="video_url",
        processing_mode="fast",
    )

    assert result["segments_to_render"] == [
        {
            "start_time": "00:00",
            # The video is 42 s long: the segment spans all of it and the render picks the 30 s (or a chapter).
            "end_time": "00:42",
            "source_ranges": [[0.0, 42.0]],
            "fallback_seconds": 30.0,
            "text": "[00:00 - 00:01] hello",
            "relevance_score": 0.25,
            "reasoning": (
                "AI analysis did not identify a strong standalone segment, "
                "so SupoClip generated the first available portion of the video."
            ),
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": "fallback",
            "hook_title": None,
        }
    ]
    analysis = json.loads(result["analysis_json"])
    assert analysis["most_relevant_segments"] == result["segments_to_render"]


def test_fallback_segment_caps_to_video_duration():
    segment = VideoService._build_fallback_segment(
        video_duration=12.0,
        transcript="short transcript",
        target_duration=30,
    )

    assert segment["start_time"] == "00:00"
    assert segment["end_time"] == "00:12"
    assert segment["hook_type"] == "fallback"


def test_fallback_segment_keeps_a_fractional_source_end():
    # A 24.6s source used to get a "00:25" end, which rendered and stored a
    # 0-25s range that editor preparation then rejected as uncovered.
    segment = VideoService._build_fallback_segment(
        video_duration=24.6,
        transcript="short transcript",
        target_duration=30,
    )

    assert segment["source_ranges"] == [[0.0, 24.6]]
    assert segment["end_time"] == "00:24"


@pytest.mark.asyncio
async def test_fallback_text_is_the_words_its_exact_range_plays(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    config = SimpleNamespace(
        max_video_duration=5400, clip_duration=30, max_clips=10, fast_mode_max_clips=4,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(VideoService, "resolve_local_video_path", staticmethod(lambda _url: source))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 24.6))
    words = {"words": [
        {"text": "Hello", "start": 0, "end": 500},
        {"text": "there.", "start": 600, "end": 1200},
        {"text": "Gone", "start": 24700, "end": 25000},
    ]}
    monkeypatch.setattr(video_service_module, "load_cached_transcript_data", lambda _path: words)

    async def empty_analysis(*_args, **_kwargs):
        return _EmptyAnalysis()

    async def fake_run_in_thread(func, *args, **_kwargs):
        if func == VideoService._drop_static_segments:
            return args[1]
        return None

    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(empty_analysis))
    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)

    result = await VideoService.process_video_complete(
        url="upload://source.mp4",
        source_type="video_url",
        processing_mode="fast",
        cached_transcript="[00:00 - 00:01] Hello there.",
    )

    [segment] = result["segments_to_render"]
    assert segment["source_ranges"] == [[0.0, 24.6]]
    assert segment["end_time"] == "00:24"
    assert segment["text"] == "Hello there."


def test_fallback_render_ranges_stay_inside_a_fractional_source(monkeypatch, tmp_path):
    from src.media.render_ranges import resolve_render_ranges
    from src.youtube_source import SourceCoverageError, local_ranges_for

    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _path: 24.6)
    segment = VideoService._build_fallback_segment(24.6, "short transcript", 30)

    resolved = resolve_render_ranges(source, segment, {}, 60.0)

    assert resolved.keep_ranges == [(0.0, 24.6)]
    assert resolved.bounds == (0.0, 24.6)
    # The stored range passes the editor's strict coverage check...
    assert local_ranges_for(source, resolved.keep_ranges, strict=True) == [(0.0, 24.6)]
    # The rounded range older fallbacks stored means the file's end...
    assert local_ranges_for(source, [(0.0, 25.0)], strict=True) == [(0.0, 24.6)]
    # ...while a file that ends well before the range is still rejected.
    with pytest.raises(SourceCoverageError):
        local_ranges_for(source, [(0.0, 26.0)], strict=True)


@pytest.mark.asyncio
async def test_cached_text_regenerates_missing_word_timings(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from src.config import Config
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(video_service_module, "get_config", Config)
    monkeypatch.setattr(VideoService, "resolve_local_video_path", lambda _: source)
    monkeypatch.setattr(VideoService, "_get_file_duration", lambda _: 19)
    monkeypatch.setattr(video_service_module, "load_cached_transcript_data", lambda _: None)
    transcribe = AsyncMock(return_value="Restored transcript")
    monkeypatch.setattr(VideoService, "generate_transcript", transcribe)
    analysis = json.dumps({"most_relevant_segments": [{"start_time": "00:00", "end_time": "00:17", "text": "Cached segment"}], "summary": "Test", "key_topics": []})
    await VideoService.process_video_complete(url="upload://source.mp4", source_type="video_url", cached_transcript="Cached transcript", cached_analysis_json=analysis)
    transcribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_picture_review_backfills_before_enforcing_one_clip_limit(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.touch()
    source.with_suffix(".transcript_cache.json").write_text(json.dumps({"words": [{"text": "A", "start": 0, "end": 100}]}))
    monkeypatch.setattr(video_service_module, "get_config", lambda: SimpleNamespace(max_video_duration=5400, clip_duration=30, max_clips=1, fast_mode_max_clips=1))
    monkeypatch.setattr(VideoService, "resolve_local_video_path", staticmethod(lambda _: source))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _: 120))
    candidates = [{"start_time": "00:00", "end_time": "00:30", "text": "These images cannot be shown."},
                  {"start_time": "00:50", "end_time": "01:20", "text": "A complete useful explanation."}]
    seen = []
    def review(_source, segments):
        seen.extend(segments)
        return segments[1:]
    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(review))
    monkeypatch.setattr(VideoService, "_refine_segment_openings", staticmethod(lambda s, *_: s))
    async def threaded(func, *args, **kwargs):
        return func(*args, **kwargs) if func == VideoService._drop_static_segments else None
    monkeypatch.setattr(video_service_module, "run_in_thread", threaded)
    result = await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", processing_mode="fast", cached_transcript="Existing transcript", cached_analysis_json=json.dumps({"most_relevant_segments": candidates}))
    assert len(seen) == 2
    assert len(result["segments_to_render"]) == 1
    assert result["segments_to_render"][0]["start_time"] == "00:50"


@pytest.mark.asyncio
async def test_resolve_stored_upload_keeps_a_local_file(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    async def fail_materialize(_url):
        raise AssertionError("durable lookup is unused when the local file exists")

    monkeypatch.setattr(video_service_module, "materialize_stored_upload", fail_materialize)
    service = VideoService()
    service.resolve_local_video_path = lambda _url: source
    assert await service.resolve_stored_upload("upload://source.mp4") == source


@pytest.mark.asyncio
async def test_resolve_stored_upload_uses_the_durable_object(monkeypatch, tmp_path):
    missing = tmp_path / "missing.mp4"
    restored = tmp_path / "restored.mp4"
    restored.write_bytes(b"from-s3")

    async def materialize(url):
        assert url == "upload://missing.mp4"
        return restored

    monkeypatch.setattr(video_service_module, "materialize_stored_upload", materialize)
    service = VideoService()
    service.resolve_local_video_path = lambda _url: missing
    assert await service.resolve_stored_upload("upload://missing.mp4") == restored


@pytest.mark.asyncio
async def test_resolve_stored_upload_leaves_s3_urls_on_the_local_resolver(monkeypatch, tmp_path):
    remote = tmp_path / "remote.mp4"

    async def fail_materialize(_url):
        raise AssertionError("upload://s3/ is resolved by the storage client")

    monkeypatch.setattr(video_service_module, "materialize_stored_upload", fail_materialize)
    service = VideoService()
    service.resolve_local_video_path = lambda _url: remote
    assert await service.resolve_stored_upload("upload://s3/user/source.mp4") == remote


@pytest.mark.asyncio
async def test_resolve_upload_for_edit_keeps_a_local_only_fake(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    fake = SimpleNamespace(resolve_local_video_path=lambda _url: source)
    assert await resolve_upload_for_edit(fake, "upload://source.mp4") == source


def _cached_highlights_setup(monkeypatch, tmp_path):
    """A source with word timings and a renderer that keeps every candidate, for runs that start from a cache."""
    source = tmp_path / "source.mp4"
    source.touch()
    source.with_suffix(".transcript_cache.json").write_text(json.dumps({"words": [{"text": "A", "start": 0, "end": 100}]}))
    monkeypatch.setattr(video_service_module, "get_config", lambda: SimpleNamespace(
        max_video_duration=5400, clip_duration=30, max_clips=10, fast_mode_max_clips=10))
    monkeypatch.setattr(VideoService, "resolve_local_video_path", staticmethod(lambda _: source))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _: 600))
    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(lambda _source, segments: segments))
    monkeypatch.setattr(VideoService, "_refine_segment_openings", staticmethod(lambda s, *_: s))

    async def threaded(func, *args, **kwargs):
        return func(*args, **kwargs) if func == VideoService._drop_static_segments else None
    monkeypatch.setattr(video_service_module, "run_in_thread", threaded)
    return source


# What a finished run saves to the shared analysis cache: the render payload, scores as top-level keys.
_CACHED_SEGMENT = {
    "start_time": "02:00", "end_time": "02:26", "text": "The launch went sideways.", "relevance_score": 0.9,
    "reasoning": "A complete beat.", "virality_score": 84, "hook_score": 22, "engagement_score": 21,
    "value_score": 20, "shareability_score": 21, "hook_type": "statement",
    "hook_title": "The launch that went sideways", "hook_start_time": None, "hook_end_time": None,
}


@pytest.mark.asyncio
async def test_clips_from_a_cached_analysis_keep_their_scores(monkeypatch, tmp_path):
    """Regression: a run that reused another run's analysis saved every clip with score 0 and no hook type."""
    _cached_highlights_setup(monkeypatch, tmp_path)
    second = {**_CACHED_SEGMENT, "start_time": "05:00", "end_time": "05:30", "virality_score": 61, "hook_type": "question"}
    result = await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", processing_mode="fast", cached_transcript="Existing transcript",
        cached_analysis_json=json.dumps({"summary": "s", "key_topics": [], "most_relevant_segments": [_CACHED_SEGMENT, second]}))
    rendered = result["segments_to_render"]
    assert [(s["virality_score"], s["hook_type"]) for s in rendered] == [(84, "statement"), (61, "question")]
    assert (rendered[0]["hook_score"], rendered[0]["engagement_score"], rendered[0]["value_score"],
            rendered[0]["shareability_score"]) == (22, 21, 20, 21)
    # What this run writes back for the next one still carries them.
    saved = json.loads(result["analysis_json"])["most_relevant_segments"]
    assert [s["virality_score"] for s in saved] == [84, 61]


@pytest.mark.asyncio
async def test_a_cached_analysis_that_lost_its_scores_is_analysed_again(monkeypatch, tmp_path):
    """Cache entries written while scores were dropped hold zeros; they must not keep producing unscored clips."""
    from unittest.mock import AsyncMock
    _cached_highlights_setup(monkeypatch, tmp_path)
    zeroed = [{**_CACHED_SEGMENT, "virality_score": 0, "hook_score": 0, "engagement_score": 0, "value_score": 0,
               "shareability_score": 0, "hook_type": None}]
    fresh = SimpleNamespace(summary="s", key_topics=[], most_relevant_segments=[{
        "start_time": "02:00", "end_time": "02:26", "text": "The launch went sideways.", "relevance_score": 0.9, "reasoning": "r",
        "hook_title": "The launch went sideways", "virality": {"total_score": 77, "hook_score": 20, "engagement_score": 19,
                                                         "value_score": 19, "shareability_score": 19, "hook_type": "story"}}])
    analyse = AsyncMock(return_value=fresh)
    monkeypatch.setattr(VideoService, "analyze_transcript", analyse)
    result = await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", processing_mode="fast", cached_transcript="Existing transcript",
        cached_analysis_json=json.dumps({"summary": "s", "key_topics": [], "most_relevant_segments": zeroed}))
    analyse.assert_awaited_once()
    assert [(s["virality_score"], s["hook_type"]) for s in result["segments_to_render"]] == [(77, "story")]


def test_stored_segment_scores_reads_both_stored_shapes():
    from src.services.video_service import cached_scores_were_lost, stored_segment_scores
    nested = {"virality": {"total_score": 70, "hook_score": 18, "engagement_score": 18, "value_score": 17,
                           "shareability_score": 17, "hook_type": "contrast"}}
    assert stored_segment_scores(nested)["virality_score"] == 70
    assert stored_segment_scores(nested)["hook_type"] == "contrast"
    assert stored_segment_scores(_CACHED_SEGMENT)["virality_score"] == 84
    assert stored_segment_scores({"start_time": "00:00"}) == {
        "virality_score": 0, "hook_score": 0, "engagement_score": 0, "value_score": 0, "shareability_score": 0,
        "hook_type": None}
    # Unscored entries from before scores existed, and starter clips, are not "lost scores".
    assert not cached_scores_were_lost([{"start_time": "00:00", "end_time": "00:17", "text": "Cached segment"}])
    assert not cached_scores_were_lost([{"virality_score": 0, "hook_type": "fallback"}])
    assert not cached_scores_were_lost([_CACHED_SEGMENT, {"virality_score": 0, "hook_type": None}])
    assert cached_scores_were_lost([{"virality_score": 0, "hook_type": None}, {"virality_score": 0}])


@pytest.mark.asyncio
@pytest.mark.parametrize("monetization_enabled,expect_rejected", [(False, False), (True, True)])
async def test_process_video_complete_skips_duration_limit_when_self_hosted(
    monkeypatch, tmp_path, monetization_enabled, expect_rejected
):
    from unittest.mock import AsyncMock

    source = tmp_path / "long-source.mp4"
    source.write_bytes(b"placeholder")
    source.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"words": [{"text": "hello", "start": 0, "end": 500}]})
    )
    config = SimpleNamespace(
        self_host=not monetization_enabled,
        monetization_enabled=monetization_enabled,
        max_video_duration=5400,
        clip_duration=30,
        max_clips=10,
        fast_mode_max_clips=4,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        video_service_module,
        "async_get_youtube_video_info",
        AsyncMock(return_value={"duration": 21600, "title": "Six Hour Stream"}),
    )
    monkeypatch.setattr(VideoService, "download_video", AsyncMock(return_value=source))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 21600.0))
    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(lambda _src, segs: segs))
    monkeypatch.setattr(VideoService, "_refine_segment_openings", staticmethod(lambda s, *_: s))
    monkeypatch.setattr(video_service_module, "cached_full_media", lambda *_a, **_k: None)

    async def threaded(func, *args, **kwargs):
        return func(*args, **kwargs) if func == VideoService._drop_static_segments else None

    monkeypatch.setattr(video_service_module, "run_in_thread", threaded)
    analysis = json.dumps({
        "most_relevant_segments": [{"start_time": "00:00", "end_time": "00:30", "text": "hello"}],
        "summary": "Test",
        "key_topics": [],
    })

    if expect_rejected:
        with pytest.raises(Exception, match="Video is too long"):
            await VideoService.process_video_complete(
                url="https://www.youtube.com/watch?v=abcdefghijk",
                source_type="youtube",
                cached_transcript="Existing transcript",
                cached_analysis_json=analysis,
            )
    else:
        result = await VideoService.process_video_complete(
            url="https://www.youtube.com/watch?v=abcdefghijk",
            source_type="youtube",
            cached_transcript="Existing transcript",
            cached_analysis_json=analysis,
        )
        assert len(result["segments_to_render"]) == 1
