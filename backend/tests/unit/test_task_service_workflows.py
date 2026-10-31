"""TaskService.process_task for workflows: cache keys, plan persistence, render loop, refund-safe errors."""

import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from src.clip_cleanup import normalize_clip_cleanup_settings
from src.clip_duration import clip_duration
from src.services import task_service as task_service_module
from src.services.task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)
from src.services.task_service import TaskService, _ProcessingPlanCache
from src.workflows.contracts import WorkflowOptions, WorkflowSpec
from src.workflows.errors import WorkflowError
from src.youtube_source import TimeMap, TimeSpan
from tests.unit.test_task_service import build_clip_result, build_task_service

URL = "upload://source.mp4"

# process_video_complete's keyword arguments before workflows existed (invariant I1).
LEGACY_PIPELINE_KWARGS = {
    "url", "source_type", "task_id", "font_family", "font_size", "font_color", "caption_template",
    "processing_mode", "output_format", "add_subtitles", "max_video_duration", "download_quality",
    "cached_transcript", "cached_analysis_json", "progress_callback", "should_cancel",
    "performance_context", "clip_length",
}


def _part(number, ranges, **extra):
    return {
        "start_time": "00:00", "end_time": "00:10", "source_ranges": ranges, "planned": True,
        "junction_fades": [0.1] * (len(ranges) - 1), "max_seconds": 155.0, "required": True,
        "text": f"Part {number} text", "hook_title": f"Part {number}: Title", "reasoning": "delivers",
        "relevance_score": 1.0, "virality_score": 0, "hook_score": 0, "engagement_score": 0,
        "value_score": 0, "shareability_score": 0, "hook_type": None, "workflow_role": "part",
        "part_number": number,
        "workflow_meta": {"planned": True, "ranges": ranges, "junction_fades": [0.1] * (len(ranges) - 1),
                          "max_seconds": 155.0, "series_title": "The story", "part_count": 2},
        **extra,
    }


SERIES_SEGMENTS = [_part(1, [[61.42, 133.9]]), _part(2, [[133.9, 170.0], [200.0, 260.5]])]
SERIES_PLAN = {"version": "wf-v1", "workflow": "series", "title": "The story", "parts": [
    {"part_number": 1, "title": "Start"}, {"part_number": 2, "title": "End"}]}

RECAP_RANGES = [[300.0, 320.0], [100.0, 130.0], [900.0, 925.0]]
RECAP_SEGMENT = {
    **_part(None, RECAP_RANGES), "workflow_role": "recap", "part_number": None, "max_seconds": 91.25,
    "junction_fades": [0.12, 0.12],
    "workflow_meta": {"planned": True, "ranges": RECAP_RANGES, "junction_fades": [0.12, 0.12], "max_seconds": 91.25,
                      "points": [{"label": "Intro", "kind": "opener", "range_index": 0, "output_start": None},
                                 {"label": "Money", "kind": "point", "range_index": 1, "output_start": None},
                                 {"label": "Takeaway", "kind": "closer", "range_index": 2, "output_start": None}]},
}
RECAP_PLAN = {"version": "wf-v1", "workflow": "recap", "title": "Recap", "points": [
    {"label": "Intro", "ranges": [RECAP_RANGES[0]], "output_start": None},
    {"label": "Money", "ranges": [RECAP_RANGES[1]], "output_start": None},
    {"label": "Takeaway", "ranges": [RECAP_RANGES[2]], "output_start": None}]}


def _workflow_service(workflow, segments, plan, options=None, events=None):
    """build_task_service with a workflow task row and a planned pipeline result."""
    service = build_task_service()
    events = events if events is not None else []
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "status": "queued", "workflow": workflow, "workflow_options": options or {}, "include_broll": False,
    })
    service.video_service.process_video_complete = AsyncMock(return_value={
        "clips": [], "segments_to_render": segments, "video_path": "/tmp/source.mp4", "segments": segments,
        "summary": "Summary", "key_topics": ["a"], "transcript": "Transcript",
        "analysis_json": json.dumps({"version": "wf-v1", "workflow": workflow}),
        "workflow_plan": plan,
    })

    async def saved(_db, _task_id, saved_plan):
        events.append(("save_plan", json.loads(json.dumps(saved_plan))))

    async def deleted(_db, _task_id):
        events.append(("delete_clips",))
        return 0

    async def status(_db, _task_id, status, **kwargs):
        events.append(("status", status, kwargs.get("progress_message")))
        return True

    service.task_repo.save_workflow_plan = AsyncMock(side_effect=saved)
    service.task_repo.merge_workflow_plan_error = AsyncMock(
        side_effect=lambda _db, _task_id, code, details: events.append(("plan_error", code, details))
    )
    service.clip_repo.delete_clips_by_task = AsyncMock(side_effect=deleted)
    service.task_repo.update_task_status = AsyncMock(side_effect=status)
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=None)
    service.video_service.create_single_clip = AsyncMock(
        side_effect=lambda *_args, **_kwargs: {**build_clip_result(), "group_output_starts": [0.0, 19.88, 49.76]}
    )
    return service, events


async def _run(service, **kwargs):
    return await service.process_task(task_id="task-1", url=URL, source_type="upload", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    {"status": "queued"},                                                # pre-migration row
    {"status": "queued", "workflow": "highlights", "workflow_options": {}},
    {"status": "queued", "workflow": "highlights", "workflow_options": {"topic": "ignored", "part_count": 4}},
])
async def test_highlights_without_instructions_is_unchanged(row):
    service = build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(return_value=row)
    service.task_repo.save_workflow_plan = AsyncMock()

    await _run(service, clip_length="medium")

    source_key = TaskService._build_cache_key(URL, "upload", "fast")
    analysis_key = TaskService._build_analysis_cache_key(source_key, "medium")
    assert [call.args[1] for call in service.cache_repo.get_cache.await_args_list] == [source_key, analysis_key]
    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert set(kwargs) == LEGACY_PIPELINE_KWARGS
    service._load_performance_context.assert_awaited_once()
    render = service.video_service.create_single_clip.await_args
    assert render.kwargs["max_clip_seconds"] == clip_duration("medium").maximum
    clip = service.clip_repo.create_clip.await_args.kwargs
    assert not {"workflow_role", "part_number", "workflow_meta"} & set(clip)
    service.task_repo.save_workflow_plan.assert_not_awaited()
    progress = [call.kwargs.get("progress_message") for call in service.task_repo.update_task_status.await_args_list]
    assert "Creating clip 1/1..." in progress


@pytest.mark.asyncio
async def test_series_uses_derived_plan_and_outline_keys():
    service, _ = _workflow_service("series", SERIES_SEGMENTS, SERIES_PLAN, {"part_count": 2})
    cleanup = {"cut_long_pauses": True, "pause_threshold_ms": 600, "remove_filler_words": True, "filtered_words": []}

    await _run(service, clip_length="short", cleanup_settings=cleanup)

    source_key = TaskService._build_cache_key(URL, "upload", "fast")
    spec = WorkflowSpec("series", WorkflowOptions(part_count=2), "short")
    config = service.config
    plan_key = hashlib.sha256(
        f"{source_key}|wf:{spec.fingerprint()}|llm:{config.llm}|{config.rerank_llm}".encode()
    ).hexdigest()
    outline_key = hashlib.sha256(
        f"{source_key}|outline-v1|{config.llm}|{config.rerank_llm}".encode()
    ).hexdigest()
    assert [call.args[1] for call in service.cache_repo.get_cache.await_args_list] == [source_key, plan_key]
    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["workflow_spec"] == spec
    assert kwargs["outline_cache_key"] == outline_key
    assert kwargs["cleanup_settings"] == normalize_clip_cleanup_settings(**cleanup)
    assert isinstance(kwargs["plan_cache"], _ProcessingPlanCache)
    # Series never reads the creator's metrics.
    service._load_performance_context.assert_not_awaited()
    assert kwargs["performance_context"] is None
    # The plan goes back to its own row; the source row keeps only the transcript.
    source_write, plan_write = service.cache_repo.upsert_cache.await_args_list
    assert source_write.kwargs["analysis_json"] is None
    assert plan_write.kwargs["cache_key"] == plan_key
    assert json.loads(plan_write.kwargs["analysis_json"])["workflow"] == "series"


@pytest.mark.asyncio
async def test_highlights_with_instructions_derives_its_key_and_keeps_metrics():
    service, _ = _workflow_service("highlights", [{"start_time": "00:00", "end_time": "00:10"}], None,
                                   {"instructions": "Only the funny bits"})

    await _run(service, user_id="user-1")

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["workflow_spec"].options.instructions == "Only the funny bits"
    service._load_performance_context.assert_awaited_once()
    source_key = TaskService._build_cache_key(URL, "upload", "fast")
    assert service.cache_repo.get_cache.await_args_list[1].args[1] != source_key


@pytest.mark.asyncio
async def test_topic_with_metrics_skips_the_plan_cache():
    service, _ = _workflow_service("topic", [_part(None, [[10.0, 40.0]], workflow_role="topic", required=False)],
                                   {"version": "wf-v1", "workflow": "topic"}, {"topic": "pricing"})
    service._load_performance_context = AsyncMock(return_value="history")
    service.cache_repo.get_cache = AsyncMock(return_value={"transcript_text": "T", "analysis_json": "{\"cached\":1}"})

    await _run(service, user_id="user-1")

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["performance_context"] == "history"
    assert kwargs["cached_analysis_json"] is None
    assert [call.kwargs["analysis_json"] for call in service.cache_repo.upsert_cache.await_args_list] == [None]


@pytest.mark.asyncio
async def test_plan_is_saved_before_clips_are_replaced_and_parts_render_in_order():
    service, events = _workflow_service("series", SERIES_SEGMENTS, SERIES_PLAN)

    await _run(service)

    assert events.index(("save_plan", SERIES_PLAN)) < events.index(("delete_clips",))
    progress = [event[2] for event in events if event[0] == "status"]
    assert "Creating part 1/2..." in progress and "Creating part 2/2..." in progress
    renders = service.video_service.create_single_clip.await_args_list
    assert [call.kwargs["max_clip_seconds"] for call in renders] == [155.0, 155.0]
    clips = [call.kwargs for call in service.clip_repo.create_clip.await_args_list]
    assert [(clip["clip_order"], clip["part_number"], clip["workflow_role"]) for clip in clips] == [
        (1, 1, "part"), (2, 2, "part"),
    ]
    # Labels show the source span on the original timeline, skips included.
    assert (clips[0]["start_time"], clips[0]["end_time"]) == ("01:01", "02:14")
    assert (clips[1]["start_time"], clips[1]["end_time"]) == ("02:14", "04:20")
    assert clips[1]["workflow_meta"]["series_title"] == "The story"


@pytest.mark.asyncio
async def test_required_output_retries_once_then_fails_with_a_refund():
    service, events = _workflow_service("series", SERIES_SEGMENTS, SERIES_PLAN)
    service.video_service.create_single_clip = AsyncMock(side_effect=[
        {**build_clip_result(), "group_output_starts": [0.0]}, None, None,
    ])
    service.clip_repo.create_clip = AsyncMock(return_value="clip-1")

    with pytest.raises(WorkflowError) as error:
        await _run(service)

    assert error.value.code == "render_incomplete"
    assert str(error.value) == "Part 2 couldn't be rendered. Please try again."
    assert service.video_service.create_single_clip.await_count == 3
    # Part 1 was saved; every clip goes before the error status, so the refund fires.
    tail = events[events.index(("plan_error", "render_incomplete", {})) - 1:]
    assert tail[0] == ("delete_clips",)
    assert tail[-1] == ("status", "error", "Part 2 couldn't be rendered. Please try again.")
    assert service.task_repo.update_task_runtime_metadata.await_args.kwargs["error_code"] == "render_incomplete"


@pytest.mark.asyncio
async def test_optional_topic_clip_is_skipped_without_a_retry():
    segments = [_part(None, [[10.0, 40.0]], workflow_role="topic", required=False),
                _part(None, [[100.0, 130.0]], workflow_role="topic", required=False)]
    service, _ = _workflow_service("topic", segments, {"version": "wf-v1", "workflow": "topic"}, {"topic": "x"})
    service.video_service.create_single_clip = AsyncMock(side_effect=[None, {**build_clip_result()}])

    result = await _run(service)

    assert result["clips_count"] == 1
    assert service.video_service.create_single_clip.await_count == 2


@pytest.mark.asyncio
async def test_planner_error_deletes_clips_records_details_and_uses_its_code():
    service, events = _workflow_service("topic", [], None, {"topic": "crypto"})
    message = "We couldn't find strong moments about crypto in this video."
    details = {"suggested_topics": ["pricing", "hiring"], "mentions": ["12:30"]}
    service.video_service.process_video_complete = AsyncMock(
        side_effect=WorkflowError("topic_not_found", message, details)
    )

    with pytest.raises(WorkflowError):
        await _run(service)

    assert events == [
        ("status", "processing", "Starting..."),
        ("delete_clips",),
        ("plan_error", "topic_not_found", details),
        ("status", "error", message),
    ]
    assert service.task_repo.update_task_runtime_metadata.await_args.kwargs["error_code"] == "topic_not_found"


@pytest.mark.asyncio
async def test_a_retry_clears_the_last_attempts_workflow_error():
    service, events = _workflow_service("series", SERIES_SEGMENTS, SERIES_PLAN)
    stale = {**SERIES_PLAN, "error": {"code": "render_incomplete", "details": {}}}
    service.task_repo.get_task_by_id.return_value = {
        **service.task_repo.get_task_by_id.return_value, "workflow_plan": stale,
    }
    service.video_service.process_video_complete = AsyncMock(side_effect=ValueError("Failed to download video"))

    with pytest.raises(ValueError):
        await _run(service)

    assert events[:2] == [("status", "processing", "Starting..."), ("save_plan", SERIES_PLAN)]
    assert not any(event[0] == "plan_error" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,deletes", [("series", True), ("recap", True), ("trailer", True),
                                              ("topic", False), ("highlights", False)])
async def test_any_failure_of_an_all_or_nothing_workflow_leaves_no_clips(workflow, deletes):
    service, events = _workflow_service(workflow, [], None)
    service.video_service.process_video_complete = AsyncMock(side_effect=ValueError("Failed to download video"))

    with pytest.raises(ValueError):
        await _run(service)

    assert (("delete_clips",) in events) is deletes
    service.task_repo.merge_workflow_plan_error.assert_not_awaited()
    assert service.task_repo.update_task_runtime_metadata.await_args.kwargs["error_code"] == "download_error"


@pytest.mark.asyncio
async def test_cancelled_series_keeps_its_finished_parts():
    service, events = _workflow_service("series", SERIES_SEGMENTS, SERIES_PLAN)
    service.video_service.process_video_complete = AsyncMock(side_effect=Exception("Task cancelled"))

    with pytest.raises(Exception, match="Task cancelled"):
        await _run(service)

    assert ("delete_clips",) not in events
    assert events[-1] == ("status", "cancelled", "Cancelled by user")


@pytest.mark.asyncio
async def test_recap_fills_output_starts_in_clip_meta_and_plan():
    service, events = _workflow_service("recap", [RECAP_SEGMENT], RECAP_PLAN)

    await _run(service)

    assert "Creating recap 1/1..." in [event[2] for event in events if event[0] == "status"]
    assert service.video_service.create_single_clip.await_args.kwargs["max_clip_seconds"] == 91.25
    clip = service.clip_repo.create_clip.await_args.kwargs
    assert [point["output_start"] for point in clip["workflow_meta"]["points"]] == [0.0, 19.88, 49.76]
    assert (clip["start_time"], clip["end_time"]) == ("01:40", "15:25")
    saved = [event[1] for event in events if event[0] == "save_plan"]
    assert len(saved) == 2
    assert [point["output_start"] for point in saved[-1]["points"]] == [0.0, 19.88, 49.76]
    # The segment the pipeline returned is not mutated.
    assert RECAP_SEGMENT["workflow_meta"]["points"][1]["output_start"] is None


@pytest.mark.asyncio
async def test_display_bounds_use_the_original_timeline_with_a_time_map(monkeypatch):
    service, _ = _workflow_service("series", SERIES_SEGMENTS[:1], SERIES_PLAN)
    time_map = TimeMap((TimeSpan(55.0, 140.0, 0.0),))
    monkeypatch.setattr(task_service_module, "load_time_map", lambda _path: time_map)
    restored = []

    def restore(clip_info, _map, original):
        restored.append(original)
        return {**clip_info, "start_time": original["start_time"], "end_time": original["end_time"]}

    monkeypatch.setattr(task_service_module, "restore_clip_timeline", restore)

    await _run(service)

    rendered = service.video_service.create_single_clip.await_args.args[1]
    assert rendered["source_ranges"] == [(pytest.approx(6.42), pytest.approx(78.9))]
    assert restored[0]["source_ranges"] == [[61.42, 133.9]]
    clip = service.clip_repo.create_clip.await_args.kwargs
    assert (clip["start_time"], clip["end_time"]) == ("01:01", "02:14")


@pytest.mark.asyncio
async def test_completion_email_names_what_was_made(monkeypatch):
    service, _ = _workflow_service("series", SERIES_SEGMENTS, SERIES_PLAN)
    service._send_completion_notification_if_needed = AsyncMock()

    await _run(service)

    service._send_completion_notification_if_needed.assert_awaited_once_with(
        task_id="task-1", clips_count=2, workflow="series", topic="",
    )


@pytest.mark.asyncio
async def test_plan_cache_reads_and_writes_processing_cache_rows():
    repo = AsyncMock()
    repo.get_cache = AsyncMock(side_effect=[{"analysis_json": "plan"}, None])
    cache = _ProcessingPlanCache(repo, "db", URL, "upload")

    assert await cache.load("key-1") == "plan"
    assert await cache.load("key-2") is None
    await cache.store("key-3", "outline")

    repo.upsert_cache.assert_awaited_once_with(
        "db", cache_key="key-3", source_url=URL, source_type="upload", analysis_json="outline",
    )


def _email(**kwargs):
    service = TaskCompletionEmailService.__new__(TaskCompletionEmailService)
    service.app_url = "https://app.example"
    return service._build_task_completed_email(
        recipient=TaskCompletionRecipient(email="a@example.com", first_name="Ana"),
        task_id="t1", source_title="My <talk>", **kwargs,
    )


def test_highlights_email_copy_is_unchanged():
    content = _email(clips_count=3)
    assert content.subject == "Your SupoClip clips are ready"
    assert content.html == (
        "<p>Hi Ana,</p><p>Your clips for <strong>My &lt;talk&gt;</strong> are ready.</p>"
        "<p>We generated 3 clips for you.</p>"
        '<p><a href="https://app.example/tasks/t1">Open your finished clips</a></p><p>Team SupoClip</p>'
    )
    assert content.text == (
        "Hi Ana,\n\nYour clips for My <talk> are ready.\nWe generated 3 clips for you.\n\n"
        "Open your finished clips: https://app.example/tasks/t1\n\nTeam SupoClip"
    )


@pytest.mark.parametrize("workflow,topic,subject", [
    ("series", "", "Your 4-part series is ready"),
    ("recap", "", "Your recap is ready"),
    ("trailer", "", "Your trailer is ready"),
    ("topic", "pricing", "Your clips about pricing are ready"),
])
def test_workflow_email_subjects(workflow, topic, subject):
    content = _email(clips_count=4 if workflow == "series" else 1, workflow=workflow, topic=topic)
    assert content.subject == subject
    assert "&lt;talk&gt;" in content.html


def test_trailer_email_reads_naturally():
    content = _email(clips_count=1, workflow="trailer")
    assert "Your trailer for My <talk> is ready.\nA short preview that makes people want to watch the whole video." in content.text
    assert "Open your trailer: https://app.example/tasks/t1" in content.text


@pytest.mark.asyncio
async def test_planner_progress_and_cache_never_share_the_session_at_once():
    import asyncio

    service, _ = _workflow_service("series", SERIES_SEGMENTS, SERIES_PLAN)
    in_flight, overlaps = [0], []

    def query(result):
        async def run(*_args, **_kwargs):
            in_flight[0] += 1
            overlaps.append(in_flight[0])
            await asyncio.sleep(0.01)
            in_flight[0] -= 1
            return result
        return run

    service.task_repo.update_task_status = AsyncMock(side_effect=query(True))
    service.cache_repo.get_cache = AsyncMock(side_effect=query(None))
    pipeline_result = service.video_service.process_video_complete.return_value

    async def concurrent_planner(**kwargs):
        await asyncio.gather(
            kwargs["progress_callback"](55, "Mapping the story..."),
            kwargs["plan_cache"].load("outline"),
            kwargs["plan_cache"].load("plan"),
        )
        return pipeline_result

    service.video_service.process_video_complete = AsyncMock(side_effect=concurrent_planner)

    await _run(service)

    assert max(overlaps) == 1
