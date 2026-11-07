"""VideoService: highlights selection golden, the workflow branch and per-workflow picture checks."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai import TranscriptAnalysis, TranscriptSegment, ViralityAnalysis
from src.services import video_service as video_service_module
from src.services.video_service import VideoService
from src.workflows.units import units_fingerprint as units_fingerprint_fn


def _words(*items):
    return [
        {"text": text, "start": int(start * 1000), "end": int(end * 1000), "confidence": 0.99}
        for text, start, end in items
    ]


# A few sentences with a weak "and so" lead-in, so the opening review trims one start.
WORDS = _words(
    ("Welcome", 0.2, 0.6), ("back", 0.6, 0.9), ("everyone.", 0.9, 1.5),
    ("And", 20.0, 20.2), ("so", 20.2, 20.5), ("um", 20.6, 20.9), ("yeah.", 21.0, 21.4),
    ("Most", 22.0, 22.3), ("people", 22.3, 22.6), ("never", 22.6, 22.9), ("save", 22.9, 23.2),
    ("money", 23.2, 23.5), ("because", 23.5, 23.8), ("they", 23.8, 24.0), ("spend", 24.0, 24.3),
    ("first.", 24.3, 24.8), ("That", 25.2, 25.5), ("is", 25.5, 25.7), ("the", 25.7, 25.8),
    ("whole", 25.8, 26.1), ("trick.", 26.1, 26.6),
    ("I", 60.0, 60.2), ("lost", 60.2, 60.5), ("forty", 60.5, 60.8), ("thousand", 60.8, 61.2),
    ("dollars", 61.2, 61.7), ("in", 61.7, 61.9), ("one", 61.9, 62.1), ("night.", 62.1, 62.6),
    ("It", 63.0, 63.2), ("was", 63.2, 63.4), ("brutal.", 63.4, 63.9),
    ("Here", 120.0, 120.3), ("is", 120.3, 120.5), ("what", 120.5, 120.8), ("I", 120.8, 120.9),
    ("learned.", 120.9, 121.5), ("Never", 122.0, 122.4), ("trade", 122.4, 122.8), ("tired.", 122.8, 123.4),
)

CACHED_SEGMENTS = [
    {
        "start_time": "00:20", "end_time": "00:50", "text": "And so um yeah. Most people never save.",
        "relevance_score": 0.9, "reasoning": "Money habit",
        "virality": {"total_score": 80, "hook_score": 20, "engagement_score": 20,
                     "value_score": 20, "shareability_score": 20, "hook_type": "statement"},
        "hook_title": "Why you never save", "hook_start_time": "00:22", "hook_end_time": "00:25",
    },
    {
        "start_time": "01:00", "end_time": "01:40", "text": "I lost forty thousand dollars in one night.",
        "relevance_score": 0.8, "reasoning": "Loss story",
        "virality": {"total_score": 70, "hook_score": 18, "engagement_score": 18,
                     "value_score": 17, "shareability_score": 17, "hook_type": "story"},
        "hook_title": "Forty grand gone",
    },
    {
        "start_time": "02:00", "end_time": "02:30", "text": "Here is what I learned.",
        "relevance_score": 0.7, "reasoning": "Lesson",
        "virality": {"total_score": 60, "hook_score": 15, "engagement_score": 15,
                     "value_score": 15, "shareability_score": 15, "hook_type": "none"},
        "opening_reviewed": True,
    },
]


def _analysis_object():
    def segment(payload):
        return TranscriptSegment(
            start_time=payload["start_time"], end_time=payload["end_time"], text=payload["text"],
            relevance_score=payload["relevance_score"], reasoning=payload["reasoning"],
            virality=ViralityAnalysis(**{k: v for k, v in payload["virality"].items() if k != "total_score"}),
            hook_title=payload.get("hook_title"),
        )

    return TranscriptAnalysis(
        most_relevant_segments=[segment(item) for item in CACHED_SEGMENTS],
        summary="Money lessons", key_topics=["saving", "loss"],
    )


# Word-timing reads the workflow branch runs off the event loop.
READS = (video_service_module.load_cached_transcript_data, units_fingerprint_fn)


def _legacy_environment(monkeypatch, tmp_path, *, analysis=None, max_clips=10):
    """process_video_complete on an upload with every side effect stubbed out."""
    source = tmp_path / "source.mp4"
    source.touch()
    source.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "words": WORDS, "utterances": []}), encoding="utf-8"
    )
    config = SimpleNamespace(max_video_duration=5400, clip_duration=30, max_clips=max_clips, fast_mode_max_clips=4)
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(video_service_module, "materialize_stored_upload", AsyncMock(return_value=source))
    monkeypatch.setattr(video_service_module, "upload_needs_remote_copy", AsyncMock(return_value=False))
    monkeypatch.setattr(VideoService, "resolve_local_video_path", staticmethod(lambda _url: source))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 900.0))
    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(lambda _path, segments: segments))
    calls = []

    async def fake_analyze(transcript, clip_signals=None, performance_context=None, clip_length="auto"):
        calls.append({"transcript": transcript, "performance_context": performance_context, "clip_length": clip_length})
        return analysis or _analysis_object()

    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(fake_analyze))

    async def threaded(func, *args, **kwargs):
        if func in (VideoService._drop_static_segments, *READS):
            return func(*args, **kwargs)
        return None

    monkeypatch.setattr(video_service_module, "run_in_thread", threaded)
    return source, calls


# Captured from the pipeline before _select_highlight_segments was extracted.
GOLDEN_CACHED_SEGMENTS = [
    {
        "start_time": "00:22", "end_time": "00:50",
        "text": "Most people never save money because they spend first. That is the whole trick.",
        "relevance_score": 0.9, "reasoning": "Money habit", "virality_score": 80, "hook_score": 20,
        "engagement_score": 20, "value_score": 20, "shareability_score": 20, "hook_type": "statement",
        "hook_title": "Why you never save", "hook_start_time": "00:22", "hook_end_time": "00:25",
        "opening_reviewed": True,
    },
    {
        "start_time": "01:00", "end_time": "01:40",
        "text": "I lost forty thousand dollars in one night. It was brutal.",
        "relevance_score": 0.8, "reasoning": "Loss story", "virality_score": 70, "hook_score": 18,
        "engagement_score": 18, "value_score": 17, "shareability_score": 17, "hook_type": "story",
        "hook_title": "Forty grand gone", "hook_start_time": None, "hook_end_time": None,
        "opening_reviewed": True,
    },
    {
        "start_time": "02:00", "end_time": "02:30", "text": "Here is what I learned. Never trade tired.",
        "relevance_score": 0.7, "reasoning": "Lesson", "virality_score": 60, "hook_score": 15,
        "engagement_score": 15, "value_score": 15, "shareability_score": 15, "hook_type": "none",
        "hook_title": None, "hook_start_time": None, "hook_end_time": None, "opening_reviewed": True,
    },
]


GOLDEN_FRESH_SEGMENTS = [
    {**GOLDEN_CACHED_SEGMENTS[1], "virality_score": 60},
    GOLDEN_CACHED_SEGMENTS[2],
]


@pytest.mark.asyncio
async def test_highlights_from_cached_analysis_match_the_golden_output(monkeypatch, tmp_path):
    _source, calls = _legacy_environment(monkeypatch, tmp_path)

    result = await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", processing_mode="balanced",
        cached_transcript="Existing transcript",
        cached_analysis_json=json.dumps(
            {"summary": "Cached summary", "key_topics": ["cached"], "most_relevant_segments": CACHED_SEGMENTS}
        ),
    )

    assert calls == []
    assert result["segments_to_render"] == GOLDEN_CACHED_SEGMENTS
    assert result["segments"] == GOLDEN_CACHED_SEGMENTS
    assert result["summary"] == "Cached summary"
    assert result["key_topics"] == ["cached"]
    assert json.loads(result["analysis_json"]) == {
        "summary": "Cached summary", "key_topics": ["cached"], "most_relevant_segments": GOLDEN_CACHED_SEGMENTS,
    }
    assert "workflow_plan" not in result


@pytest.mark.asyncio
async def test_highlights_from_fresh_analysis_match_the_golden_output(monkeypatch, tmp_path):
    _source, calls = _legacy_environment(monkeypatch, tmp_path, max_clips=2)

    result = await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", processing_mode="fast",
        cached_transcript="Existing transcript", performance_context="Hooks with numbers win.",
        clip_length="medium",
    )

    assert calls == [{"transcript": "Existing transcript", "performance_context": "Hooks with numbers win.",
                      "clip_length": "medium"}]
    # The shortened opening drops below the medium minimum, so the first clip
    # is demoted; max_clips=2 then keeps the other two.
    assert result["segments_to_render"] == GOLDEN_FRESH_SEGMENTS
    assert result["summary"] == "Money lessons"
    assert result["key_topics"] == ["saving", "loss"]


@pytest.mark.asyncio
async def test_highlights_fallback_window_matches_the_golden_output(monkeypatch, tmp_path):
    empty = SimpleNamespace(summary="Nothing", key_topics=[], most_relevant_segments=[])
    _legacy_environment(monkeypatch, tmp_path, analysis=empty)

    result = await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", cached_transcript="[00:00 - 00:01] hello", clip_length="short",
    )

    assert result["segments_to_render"] == [
        {
            "start_time": "00:00", "end_time": "00:18", "text": "[00:00 - 00:01] hello",
            "relevance_score": 0.25,
            "reasoning": (
                "AI analysis did not identify a strong standalone segment, "
                "so SupoClip generated the first available portion of the video."
            ),
            "virality_score": 0, "hook_score": 0, "engagement_score": 0, "value_score": 0,
            "shareability_score": 0, "hook_type": "fallback", "hook_title": None,
        }
    ]


# --- workflow branch -------------------------------------------------------

from src.workflows import pipeline as pipeline_module  # noqa: E402
from src.workflows import units as units_module  # noqa: E402
from src.workflows.contracts import WorkflowOptions, WorkflowResult, WorkflowSpec  # noqa: E402
from src.workflows.errors import WorkflowError  # noqa: E402


def _planned(ranges, role, **meta):
    return {
        "start_time": "00:20", "end_time": "00:26", "source_ranges": ranges, "planned": True,
        "junction_fades": [0.12] * (len(ranges) - 1), "max_seconds": 60.0, "required": role != "topic",
        "text": "Planned text", "workflow_role": role, "part_number": None,
        "workflow_meta": {"planned": True, "ranges": ranges, "junction_fades": [0.12] * (len(ranges) - 1), **meta},
    }


class _MemoryPlanCache:
    def __init__(self):
        self.rows = {}

    async def load(self, key):
        return self.rows.get(key)

    async def store(self, key, payload):
        self.rows[key] = payload


def _workflow_environment(monkeypatch, tmp_path, result, *, cached=None):
    """The legacy environment, with highlights selection forbidden and a fake planner."""
    source, _calls = _legacy_environment(monkeypatch, tmp_path)
    planned = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("workflows never run highlights selection")

    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(forbidden))
    monkeypatch.setattr(VideoService, "_refine_segment_openings", staticmethod(forbidden))
    monkeypatch.setattr(VideoService, "_build_fallback_segment", staticmethod(forbidden))

    async def fake_plan(ctx):
        planned.append(ctx)
        if isinstance(result, Exception):
            raise result
        await ctx.progress(54, "Mapping the story...")
        return result

    monkeypatch.setattr(pipeline_module, "plan_workflow", fake_plan)
    return source, planned


async def _run_workflow(spec, *, cached=None, progress=None, cleanup=None):
    return await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", task_id="task-1", cached_transcript="[00:20 - 00:26] A: Most people",
        cached_analysis_json=cached, progress_callback=progress, clip_length=spec.clip_length,
        workflow_spec=spec, plan_cache=_MemoryPlanCache(), outline_cache_key="outline-key",
        cleanup_settings=cleanup or {"cut_long_pauses": False},
    )


def _result(segments, plan, fingerprint="fp"):
    return WorkflowResult(segments=segments, plan=plan, summary="Summary", key_topics=["money"],
                          units_fingerprint=fingerprint)


SERIES = WorkflowSpec("series", WorkflowOptions(), "auto")


@pytest.mark.asyncio
async def test_series_is_planned_from_the_word_sidecar_and_returned_locked(monkeypatch, tmp_path):
    segments = [_planned([[20.0, 26.95]], "part"), _planned([[26.95, 64.25]], "part")]
    plan = {"version": "wf-v1", "workflow": "series", "title": "Money"}
    _source, planned = _workflow_environment(monkeypatch, tmp_path, _result(segments, plan))
    progress = []

    async def report(percent, message, status):
        progress.append((percent, message, status))

    result = await _run_workflow(SERIES, progress=report, cleanup={"cut_long_pauses": True})

    ctx = planned[0]
    assert ctx.spec == SERIES
    assert ctx.task_id == "task-1"
    assert ctx.transcript_data["words"][0]["text"] == "Welcome"
    assert ctx.source_seconds == 900.0
    assert ctx.cleanup_settings == {"cut_long_pauses": True}
    assert ctx.outline_cache_key == "outline-key"
    assert result["segments_to_render"] == segments
    assert result["workflow_plan"] == plan
    assert result["summary"] == "Summary" and result["key_topics"] == ["money"]
    cached = json.loads(result["analysis_json"])
    assert cached["workflow"] == "series" and cached["segments"] == segments
    assert (50, "Reading the whole video...", "processing") in progress
    assert (54, "Mapping the story...", "processing") in progress


@pytest.mark.asyncio
async def test_cached_plan_for_the_same_words_skips_the_planner(monkeypatch, tmp_path):
    segments = [_planned([[20.0, 26.95]], "part")]
    plan = {"version": "wf-v1", "workflow": "series", "title": "Money"}
    source, planned = _workflow_environment(monkeypatch, tmp_path, _result(segments, plan))
    fingerprint = units_module.units_fingerprint(
        json.loads(source.with_suffix(".transcript_cache.json").read_text()), "[00:20 - 00:26] A: Most people"
    )

    result = await _run_workflow(SERIES, cached=_result(segments, plan, fingerprint).to_cache_json())

    assert planned == []
    assert result["segments_to_render"] == segments


@pytest.mark.asyncio
async def test_a_plan_cached_for_other_words_is_replanned(monkeypatch, tmp_path):
    segments = [_planned([[20.0, 26.95]], "part")]
    plan = {"version": "wf-v1", "workflow": "series"}
    _source, planned = _workflow_environment(monkeypatch, tmp_path, _result(segments, plan))

    await _run_workflow(SERIES, cached=_result(segments, plan, "stale").to_cache_json())

    assert len(planned) == 1


@pytest.mark.asyncio
async def test_planner_errors_reach_the_task(monkeypatch, tmp_path):
    error = WorkflowError("no_story", "This video doesn't tell one story.")
    _workflow_environment(monkeypatch, tmp_path, error)

    with pytest.raises(WorkflowError) as raised:
        await _run_workflow(SERIES)

    assert raised.value is error


@pytest.mark.asyncio
async def test_planner_progress_checks_cancellation(monkeypatch, tmp_path):
    _source, planned = _workflow_environment(
        monkeypatch, tmp_path, _result([_planned([[20.0, 26.95]], "part")], {"workflow": "series"})
    )

    async def should_cancel():
        # The user cancels while the planner is running.
        return bool(planned)

    with pytest.raises(Exception, match="Task cancelled"):
        await VideoService.process_video_complete(
            "upload://source.mp4", "video_url", cached_transcript="T", should_cancel=should_cancel,
            workflow_spec=SERIES, plan_cache=_MemoryPlanCache(), outline_cache_key="k",
        )


@pytest.mark.asyncio
async def test_series_parts_with_a_still_picture_are_kept(monkeypatch, tmp_path):
    segments = [_planned([[20.0, 26.95]], "part"), _planned([[60.0, 64.25]], "part")]
    _workflow_environment(monkeypatch, tmp_path, _result(segments, {"workflow": "series"}))
    logged = []
    monkeypatch.setattr(VideoService, "_log_static_parts", staticmethod(lambda _path, parts: logged.append(parts)))

    async def threaded(func, *args, **kwargs):
        if func is VideoService._log_static_parts or func in READS:
            return func(*args, **kwargs)
        if func is VideoService._drop_static_segments:
            raise AssertionError("series parts are never dropped")
        return None

    monkeypatch.setattr(video_service_module, "run_in_thread", threaded)

    result = await _run_workflow(SERIES)

    assert logged == [segments]
    assert result["segments_to_render"] == segments


@pytest.mark.asyncio
async def test_topic_drops_static_moments_and_promotes_a_reserve(monkeypatch, tmp_path):
    segments = [
        _planned([[20.0, 26.95]], "topic", topic="money"),
        _planned([[60.0, 64.25]], "topic", topic="money"),
        _planned([[120.0, 123.7]], "topic", topic="money", reserve=True),
    ]
    _workflow_environment(monkeypatch, tmp_path, _result(segments, {"workflow": "topic"}))
    reviewed = []

    def drop_first(_path, candidates):
        reviewed.append(candidates)
        return candidates[1:]

    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(drop_first))

    result = await _run_workflow(WorkflowSpec("topic", WorkflowOptions(topic="money"), "auto"))

    assert len(reviewed[0]) == 3
    kept = result["segments_to_render"]
    assert [segment["source_ranges"] for segment in kept] == [[[60.0, 64.25]], [[120.0, 123.7]]]
    assert all("reserve" not in segment["workflow_meta"] for segment in kept)


@pytest.mark.asyncio
async def test_recap_static_ranges_are_dropped_and_the_plan_follows(monkeypatch, tmp_path):
    ranges = [[20.0, 40.0], [100.0, 120.0], [200.0, 230.0], [300.0, 325.0]]
    points = [
        {"label": "Intro", "kind": "opener", "range_index": 0, "droppable": False, "output_start": None},
        {"label": "Pricing", "kind": "point", "range_index": 1, "droppable": True, "output_start": None},
        {"label": "Hiring", "kind": "point", "range_index": 2, "droppable": False, "output_start": None},
        {"label": "Takeaway", "kind": "closer", "range_index": 3, "droppable": False, "output_start": None},
    ]
    segment = _planned(ranges, "recap", points=points)
    plan = {"workflow": "recap", "points": [{"label": p["label"], "ranges": [ranges[p["range_index"]]]} for p in points]}
    _workflow_environment(monkeypatch, tmp_path, _result([segment], plan))
    monkeypatch.setattr(
        VideoService, "_range_picture", staticmethod(lambda _path, rs: (rs[0][0] in (20.0, 100.0), False))
    )

    async def threaded(func, *args, **kwargs):
        return func(*args, **kwargs) if func is VideoService._drop_static_ranges or func in READS else None

    monkeypatch.setattr(video_service_module, "run_in_thread", threaded)

    result = await _run_workflow(WorkflowSpec("recap", WorkflowOptions(), "medium"))

    (rendered,) = result["segments_to_render"]
    # The droppable static point goes; the static opener stays.
    assert rendered["source_ranges"] == [[20.0, 40.0], [200.0, 230.0], [300.0, 325.0]]
    assert rendered["junction_fades"] == [0.12, 0.12]
    assert [(p["label"], p["range_index"]) for p in rendered["workflow_meta"]["points"]] == [
        ("Intro", 0), ("Hiring", 1), ("Takeaway", 2),
    ]
    assert rendered["workflow_meta"]["ranges"] == rendered["source_ranges"]
    assert [p["label"] for p in result["workflow_plan"]["points"]] == ["Intro", "Hiring", "Takeaway"]
    # The cached plan stays as the planner made it.
    assert len(json.loads(result["analysis_json"])["plan"]["points"]) == 4


@pytest.mark.asyncio
async def test_highlights_with_instructions_steer_the_analysis(monkeypatch, tmp_path):
    _legacy_environment(monkeypatch, tmp_path)
    seen = {}

    async def analyze(transcript, clip_signals=None, performance_context=None, clip_length="auto", instructions=None):
        seen["instructions"] = instructions
        return _analysis_object()

    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(analyze))

    result = await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", cached_transcript="T",
        workflow_spec=WorkflowSpec("highlights", WorkflowOptions(instructions="Only money stories"), "auto"),
        plan_cache=_MemoryPlanCache(), outline_cache_key="k",
    )

    assert seen["instructions"] == "Only money stories"
    assert "workflow_plan" not in result


# --- picture checks ----------------------------------------------------------


def _recap(ranges, roles=None, items_key="points"):
    if items_key == "points":
        items = [{"label": f"P{i}", "range_index": i, "droppable": role, "output_start": None}
                 for i, role in enumerate(roles)]
    else:
        items = [{"role": role, "range_index": i, "output_start": None} for i, role in enumerate(roles)]
    return {
        "start_time": "00:10", "end_time": "00:20", "source_ranges": ranges, "planned": True,
        "junction_fades": [0.04] * (len(ranges) - 1),
        "workflow_meta": {"planned": True, "ranges": ranges, "junction_fades": [0.04] * (len(ranges) - 1),
                          items_key: items},
    }


def _pictures(monkeypatch, static_starts, notice_starts=()):
    """Fake picture checks keyed by each range's start second."""
    monkeypatch.setattr(
        VideoService, "_range_picture",
        staticmethod(lambda _path, rs: (rs[0][0] in static_starts, rs[0][0] in notice_starts)),
    )


def test_trailer_keeps_its_cold_open_and_button_even_when_static(monkeypatch):
    _pictures(monkeypatch, {10.0, 50.0, 120.0, 150.0})
    segment = _recap([[10.0, 16.0], [50.0, 56.0], [80.0, 86.0], [120.0, 126.0], [150.0, 156.0]],
                     ["cold_open", "escalation", "context", "escalation", "button"], items_key="beats")

    result = VideoService._drop_static_ranges(None, segment, min_seconds=10.0)

    assert result["source_ranges"] == [[10.0, 16.0], [80.0, 86.0], [150.0, 156.0]]
    assert [(b["role"], b["range_index"]) for b in result["workflow_meta"]["beats"]] == [
        ("cold_open", 0), ("context", 1), ("button", 2),
    ]
    assert result["start_time"] == "00:10"


def test_an_all_static_source_keeps_every_range(monkeypatch):
    # An audio podcast over cover art: the picture says nothing about the passage.
    _pictures(monkeypatch, {10.0, 50.0, 80.0, 120.0})
    segment = _recap([[10.0, 16.0], [50.0, 56.0], [80.0, 86.0], [120.0, 126.0]], [False, True, True, False])

    assert VideoService._drop_static_ranges(None, segment, min_seconds=0.0) is segment


def test_interruption_notices_are_dropped_even_when_every_range_is_static(monkeypatch):
    _pictures(monkeypatch, {10.0, 50.0, 80.0, 120.0}, notice_starts={50.0})
    segment = _recap([[10.0, 16.0], [50.0, 56.0], [80.0, 86.0], [120.0, 126.0]], [False, True, False, False])

    result = VideoService._drop_static_ranges(None, segment, min_seconds=0.0)

    assert result["source_ranges"] == [[10.0, 16.0], [80.0, 86.0], [120.0, 126.0]]


def test_static_ranges_stay_when_the_edit_would_fall_below_budget(monkeypatch):
    _pictures(monkeypatch, {50.0})
    segment = _recap([[10.0, 16.0], [50.0, 56.0], [80.0, 86.0]], [False, True, False])

    assert VideoService._drop_static_ranges(None, segment, min_seconds=16.0) is segment


def test_budget_floor_counts_what_cleanup_leaves(monkeypatch, tmp_path):
    # 3 x 6s ranges; cleanup leaves 4s of each. Dropping one leaves ~8s, not 12s.
    _pictures(monkeypatch, {50.0})
    monkeypatch.setattr(
        video_service_module, "expand_ranges_with_groups",
        lambda _path, planned, _cleanup: (
            [(start, start + 4.0) for start, _ in planned], list(range(len(planned)))
        ),
    )
    segment = _recap([[10.0, 16.0], [50.0, 56.0], [80.0, 86.0]], [False, True, False])

    cleanup = {"cut_long_pauses": True}
    assert VideoService._drop_static_ranges(tmp_path / "v.mp4", segment, 10.0, cleanup) is segment
    dropped = VideoService._drop_static_ranges(tmp_path / "v.mp4", segment, 7.5, cleanup)
    assert dropped["source_ranges"] == [[10.0, 16.0], [80.0, 86.0]]


def test_a_two_range_edit_is_never_cut_further(monkeypatch):
    _pictures(monkeypatch, {10.0}, notice_starts={10.0})
    segment = _recap([[10.0, 16.0], [50.0, 56.0]], [True, True])

    assert VideoService._drop_static_ranges(None, segment, min_seconds=0.0) is segment


def test_dropping_the_first_range_moves_the_labels(monkeypatch):
    _pictures(monkeypatch, {10.0})
    segment = _recap([[10.0, 16.0], [65.0, 70.0], [80.0, 86.0]], [True, False, False])

    result = VideoService._drop_static_ranges(None, segment, min_seconds=0.0)

    assert result["source_ranges"] == [[65.0, 70.0], [80.0, 86.0]]
    assert (result["start_time"], result["end_time"]) == ("01:05", "01:10")
    assert result["junction_fades"] == [0.04]


def test_static_check_reads_planned_ranges_not_labels(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(video_service_module, "segment_static_fraction", lambda _path, ranges: seen.append(ranges) or 0.0)
    monkeypatch.setattr(video_service_module, "segment_shows_interruption_notice", lambda _path, _ranges: False)
    planned = {"start_time": "00:10", "end_time": "00:20", "source_ranges": [[10.4, 18.25]]}
    legacy = {"start_time": "00:30", "end_time": "00:40"}

    VideoService._drop_static_segments(tmp_path / "source.mp4", [planned, legacy])

    assert seen == [[(10.4, 18.25)], [(30.0, 40.0)]]
