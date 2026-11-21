"""Clip part of a long video with every workflow: series, topic, recap and trailer.

The part is planned on its own timeline (sized by its length, its own outline
cache row) and the planned cuts, plan included, move onto the source's.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks
from src.services import video_service as video_module
from src.services.task_service import TaskService
from src.services.video_service import VideoService
from src.source_range import SourceRange, shift_plan
from src.workflows import pipeline as pipeline_module
from src.workflows.contracts import WorkflowOptions, WorkflowResult, WorkflowSpec
from src.workflows.units import units_fingerprint
from tests.unit.test_source_range import EIGHT_HOURS, YOUTUBE_URL, creation  # noqa: F401  (fixture)

PART = SourceRange(3600, 4200.0, EIGHT_HOURS)
WORDS = [
    {"text": "Most", "start": 20000, "end": 20400, "speaker": "A"},
    {"text": "people", "start": 20400, "end": 26000, "speaker": "A"},
]


def _spec(workflow):
    options = WorkflowOptions(topic="money") if workflow == "topic" else WorkflowOptions()
    return WorkflowSpec(workflow, options, "auto")


def _planned(ranges, role, **meta):
    return {
        "start_time": "00:20", "end_time": "00:26", "source_ranges": ranges, "planned": True,
        "junction_fades": [0.12] * (len(ranges) - 1), "max_seconds": 60.0, "required": role != "topic",
        "text": "Planned text", "workflow_role": role, "part_number": None,
        "workflow_meta": {"planned": True, "ranges": ranges, **meta},
    }


# What each planner returns, on the part's own timeline (0 = the part's start).
PLANS = {
    "series": (
        [_planned([[20.0, 80.0]], "part", first_unit=0, last_unit=3), _planned([[80.0, 150.0]], "part", first_unit=4, last_unit=9)],
        {"version": "wf-v1", "workflow": "series", "title": "Story", "skips": [[2, 2]], "parts": [
            {"part_number": 1, "first_unit": 0, "last_unit": 3, "ranges": [[20.0, 80.0]], "seconds": 60.0},
            {"part_number": 2, "first_unit": 4, "last_unit": 9, "ranges": [[80.0, 150.0]], "seconds": 70.0}]},
    ),
    "topic": (
        [_planned([[100.0, 140.0]], "topic", topic="money")],
        {"version": "wf-v1", "workflow": "topic", "title": "money", "topic": {"query": "money", "found": 1}},
    ),
    "recap": (
        [_planned([[10.0, 30.0], [200.0, 230.0], [500.0, 540.0]], "recap", points=[
            {"label": "Intro", "range_index": 0, "output_start": None},
            {"label": "Money", "range_index": 1, "output_start": None},
            {"label": "Takeaway", "range_index": 2, "output_start": None}])],
        {"version": "wf-v1", "workflow": "recap", "title": "Recap", "seconds": 90.0, "points": [
            {"label": "Intro", "ranges": [[10.0, 30.0]], "output_start": None},
            {"label": "Money", "ranges": [[200.0, 230.0]], "output_start": None},
            {"label": "Takeaway", "ranges": [[500.0, 540.0]], "output_start": None}]},
    ),
    "trailer": (
        [_planned([[40.0, 52.0], [300.0, 315.0]], "trailer", beats=[
            {"role": "hook", "range_index": 0, "output_start": None},
            {"role": "tension", "range_index": 1, "output_start": None}])],
        {"version": "wf-v1", "workflow": "trailer", "title": "Trailer", "seconds": 27.0, "beats": [
            {"role": "hook", "ranges": [[40.0, 52.0]], "output_start": None, "why": "x"},
            {"role": "tension", "ranges": [[300.0, 315.0]], "output_start": None, "why": "y"}]},
    ),
}
WORKFLOWS = sorted(PLANS)


def _part_environment(monkeypatch, tmp_path, workflow):
    """An upload with a picked part, every side effect stubbed and a fake planner."""
    source = tmp_path / "source.mp4"
    source.touch()
    config = SimpleNamespace(
        max_video_duration=7200, clip_duration=30, max_clips=10, fast_mode_max_clips=4, temp_dir=str(tmp_path),
    )
    monkeypatch.setattr(video_module, "get_config", lambda: config)
    monkeypatch.setattr(video_module, "materialize_stored_upload", AsyncMock(return_value=source))
    monkeypatch.setattr(video_module, "upload_needs_remote_copy", AsyncMock(return_value=False))
    monkeypatch.setattr(VideoService, "resolve_local_video_path", staticmethod(lambda _url: source))
    monkeypatch.setattr(VideoService, "prepare_source", staticmethod(AsyncMock(side_effect=lambda path: path)))
    cut_from = []

    def cut(full, dest, part, **_kwargs):
        cut_from.append((full, part))
        dest.write_bytes(b"a")
        dest.with_suffix(".transcript_cache.json").write_text(json.dumps({"version": 2, "words": WORDS, "utterances": []}))
        return dest

    durations = {}
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda path: durations.setdefault("probed", 600.0)))
    monkeypatch.setattr(VideoService, "_drop_static_segments", staticmethod(lambda _path, segments: segments))
    monkeypatch.setattr(VideoService, "_drop_static_ranges", staticmethod(lambda _path, segment, *_a: segment))
    monkeypatch.setattr(VideoService, "_log_static_parts", staticmethod(lambda *_a: None))
    runs = {
        cut: cut,
        video_module._private_link: lambda path, _dir: path,
        video_module.load_cached_transcript_data: video_module.load_cached_transcript_data,
        units_fingerprint: units_fingerprint,
        VideoService._drop_static_segments: VideoService._drop_static_segments,
        VideoService._drop_static_ranges: VideoService._drop_static_ranges,
    }
    monkeypatch.setattr(video_module, "cut_audio_range", cut)

    async def threaded(func, *args, **kwargs):
        run = runs.get(func)
        return run(*args, **kwargs) if run else None

    monkeypatch.setattr(video_module, "run_in_thread", threaded)
    segments, plan = PLANS[workflow]
    planned = []

    async def fake_plan(ctx):
        planned.append(ctx)
        return WorkflowResult(segments=json.loads(json.dumps(segments)), plan=json.loads(json.dumps(plan)),
                              summary="S", key_topics=["k"], units_fingerprint="fp")

    monkeypatch.setattr(pipeline_module, "plan_workflow", fake_plan)
    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(lambda *_a, **_k: pytest.fail("highlights selection")))
    return planned, cut_from


class _MemoryPlanCache:
    def __init__(self):
        self.rows = {}

    async def load(self, key):
        return self.rows.get(key)

    async def store(self, key, payload):
        self.rows[key] = payload


async def _run_part(workflow, *, checkpoint=None):
    spec = _spec(workflow)
    return await VideoService.process_video_complete(
        "upload://source.mp4", "video_url", task_id="task-1", cached_transcript="[00:20 - 00:26] A: Most people",
        clip_length=spec.clip_length, workflow_spec=spec, plan_cache=_MemoryPlanCache(),
        outline_cache_key="outline-part", cleanup_settings={"cut_long_pauses": False},
        max_video_duration=7200, source_range=PART, checkpoint=checkpoint,
    )


def _shifted(ranges):
    return [[start + PART.start, end + PART.start] for start, end in ranges]


# ---------------------------------------------------------------- pipeline, per workflow

@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_each_workflow_plans_the_part_alone_and_cuts_on_the_source_timeline(monkeypatch, tmp_path, workflow):
    planned, cut_from = _part_environment(monkeypatch, tmp_path, workflow)
    segments, plan = PLANS[workflow]

    result = await _run_part(workflow)

    ctx = planned[0]
    # Sized from the part (10 min), never the 8-hour stream.
    assert ctx.source_seconds == 600.0
    assert ctx.outline_cache_key == "outline-part"
    assert ctx.transcript_data["words"] == WORDS
    assert cut_from and cut_from[0][1] == PART
    rendered = result["segments_to_render"]
    assert [s["source_ranges"] for s in rendered] == [_shifted(s["source_ranges"]) for s in segments]
    assert rendered[0]["start_time"] == "60:20"
    saved = result["workflow_plan"]
    for key in ("parts", "points", "beats"):
        if key in plan:
            # Every item survives the static-range sync, on the source's timeline.
            assert [item["ranges"] for item in saved[key]] == [_shifted(item["ranges"]) for item in plan[key]]
    if workflow == "series":
        # Unit ids and skips are positions in the part's transcript, not times.
        assert [p["first_unit"] for p in saved["parts"]] == [0, 4]
        assert saved["skips"] == [[2, 2]]
        assert [s["workflow_meta"]["first_unit"] for s in rendered] == [0, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_a_retry_of_a_part_reuses_its_frozen_cuts_without_moving_them_again(monkeypatch, tmp_path, workflow):
    planned, _cut = _part_environment(monkeypatch, tmp_path, workflow)
    first = await _run_part(workflow)
    frozen = {
        "segments": first["segments_to_render"], "plan": first["workflow_plan"],
        "summary": "S", "key_topics": ["k"], "analysis_json": "{}",
    }

    again = await _run_part(workflow, checkpoint={
        "transcript": "[00:20 - 00:26] A: Most people",
        "transcript_data": {"version": 2, "words": WORDS, "utterances": []},
        "workflow_final": json.loads(json.dumps(frozen)),
    })

    assert len(planned) == 1
    assert again["segments_to_render"] == frozen["segments"]
    assert again["workflow_plan"] == frozen["plan"]


def test_shift_plan_moves_only_source_times():
    plan = {"title": "T", "seconds": 90.0, "skips": [[1, 2]],
            "points": [{"label": "A", "ranges": [[1.5, 2.25]], "output_start": 3.0}, "not-a-dict"],
            "parts": [{"part_number": 1, "first_unit": 5, "ranges": [[0.0, 10.0], [12.0, 20.0]], "seconds": 18.0}]}
    moved = shift_plan(plan, 3600)
    assert moved["points"][0] == {"label": "A", "ranges": [[3601.5, 3602.25]], "output_start": 3.0}
    assert moved["points"][1] == "not-a-dict"
    assert moved["parts"][0]["ranges"] == [[3600.0, 3610.0], [3612.0, 3620.0]]
    assert (moved["parts"][0]["first_unit"], moved["parts"][0]["seconds"], moved["seconds"], moved["skips"]) == (5, 18.0, 90.0, [[1, 2]])
    # The original plan is untouched; no offset or no plan is a no-op.
    assert plan["points"][0]["ranges"] == [[1.5, 2.25]]
    assert shift_plan(plan, 0) is plan
    assert shift_plan(None, 60) is None


# ---------------------------------------------------------------- caches: a part never reads another part's outline

@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_each_part_has_its_own_outline_and_plan_rows(workflow):
    from src.config import Config

    config = Config()
    spec = _spec(workflow)

    def keys(part):
        base = TaskService._build_cache_key(YOUTUBE_URL, "youtube", "fast", None, part)
        return (
            base,
            TaskService._build_outline_cache_key(base, config),
            TaskService._build_workflow_cache_key(base, spec, config, {}),
        )

    whole, first, second = keys(None), keys(SourceRange(0, 3600.0, EIGHT_HOURS)), keys(SourceRange(3600, 7200.0, EIGHT_HOURS))
    for row in range(3):
        assert len({whole[row], first[row], second[row]}) == 3
    # The same part asked for again (any recorded length) finds its own rows.
    assert keys(SourceRange(0, 3600.0, 1.0)) == first


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_worker_gives_a_part_its_own_outline_key_and_never_its_shared_analysis(workflow):
    from tests.unit.test_task_service import build_clip_result, build_task_service

    segments, plan = PLANS[workflow]
    keys = {}
    for part in (SourceRange(0, 3600.0, EIGHT_HOURS), SourceRange(3600, 7200.0, EIGHT_HOURS)):
        service = build_task_service()
        options = {"topic": "money"} if workflow == "topic" else {}
        service.task_repo.get_task_by_id = AsyncMock(return_value={
            "status": "queued", "workflow": workflow, "workflow_options": options, "include_broll": False,
        })
        service.task_repo.load_source_metadata = AsyncMock(return_value={"source_range": part.to_metadata()})
        service.cache_repo.get_cache = AsyncMock(return_value={"transcript_text": "T", "analysis_json": "{\"plan\": 1}"})
        service.clip_repo.get_clip_by_id = AsyncMock(return_value=None)
        service.video_service.create_single_clip = AsyncMock(return_value=build_clip_result())
        shifted = [{**s, "source_ranges": _shifted(s["source_ranges"])} for s in segments]
        service.video_service.process_video_complete = AsyncMock(return_value={
            "clips": [], "segments_to_render": shifted, "video_path": "/tmp/source.mp4", "segments": shifted,
            "summary": "S", "key_topics": [], "transcript": "T", "analysis_json": "{}",
            "workflow_plan": shift_plan(plan, part.start), "source_range_dir": "/tmp/part",
        })
        await service.process_task(task_id="task-1", url=YOUTUBE_URL, source_type="youtube")
        kwargs = service.video_service.process_video_complete.await_args.kwargs
        assert kwargs["source_range"] == part
        assert kwargs["cached_analysis_json"] is None
        keys[part.start] = kwargs["outline_cache_key"]
    assert keys[0] != keys[3600]


# ---------------------------------------------------------------- admission and billing

@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_every_workflow_takes_a_part_and_charges_for_it(monkeypatch, creation, workflow):  # noqa: F811
    request, service, queue, saved = creation
    monkeypatch.setattr(tasks, "workflow_allowed_for", AsyncMock(return_value=True))
    monkeypatch.setattr(tasks, "client_platform", lambda _request: "web")
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": EIGHT_HOURS, "title": "Stream"}))
    body = {"workflow": workflow, **({"workflow_options": {"topic": "money"}} if workflow == "topic" else {})}

    await tasks.create_task(request({"url": YOUTUBE_URL, "start_seconds": 3600, "end_seconds": 10800}, **body), db=None)

    kwargs = service.create_task_with_source.await_args.kwargs
    assert kwargs["workflow"] == workflow
    assert kwargs["source_duration_seconds"] == 7200
    assert tasks.BillingService.assert_can_create_task.await_args.args[-1] == 7200
    assert saved.await_args.args[1]["source_range"]["start_seconds"] == 3600
    queue.enqueue_processing_job.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow, code", [
    ("series", "SOURCE_TOO_SHORT_FOR_SERIES"),
    ("recap", "SOURCE_TOO_SHORT_FOR_RECAP"),
    ("trailer", "SOURCE_TOO_SHORT_FOR_TRAILER"),
])
async def test_a_part_too_short_for_the_workflow_is_refused_before_any_charge(monkeypatch, creation, workflow, code):  # noqa: F811
    request, service, _queue, _saved = creation
    monkeypatch.setattr(tasks, "workflow_allowed_for", AsyncMock(return_value=True))
    monkeypatch.setattr(tasks, "client_platform", lambda _request: "web")
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": EIGHT_HOURS}))

    with pytest.raises(HTTPException) as error:
        # Ninety seconds of an 8-hour stream: long enough for highlights, not for this workflow.
        await tasks.create_task(request({"url": YOUTUBE_URL, "start_seconds": 3600, "end_seconds": 3690}, workflow=workflow), db=None)

    assert error.value.detail["code"] == code
    service.create_task_with_source.assert_not_awaited()
