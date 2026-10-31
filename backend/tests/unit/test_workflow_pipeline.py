import asyncio
from types import SimpleNamespace

import pytest

from src.repositories.edit_transaction import TaskCancelled
from src.workflows import pipeline
from src.workflows.contracts import WorkflowResult
from src.workflows.errors import WorkflowError
from src.workflows.outline import OutlineResult
from src.workflows.planning import plan_language
from src.workflows.schemas import SeriesCritique, SeriesDraft, SeriesPartDraft
from src.workflows.units import build_units, units_fingerprint

from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, story_data


def _stub_outline(monkeypatch, outline=None):
    seen = {}

    async def fake_outline(ctx, index, language_name, stats):
        seen["language_name"] = language_name
        return OutlineResult(outline=outline or make_outline(chapters=[chapter(len(index))], language="en"), mode="full", cached=True)

    monkeypatch.setattr(pipeline, "get_outline", fake_outline)
    return seen


def _planner(calls, segments=None):
    async def planner(pi):
        calls.append(pi)
        return segments if segments is not None else [{"source_ranges": [[1.0, 2.0]]}], {"workflow": pi.ctx.spec.workflow, "notes": []}

    return planner


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", ["series", "topic", "recap", "trailer"])
async def test_plan_workflow_dispatches_and_wraps_the_result(monkeypatch, workflow):
    calls = []
    _stub_outline(monkeypatch)
    monkeypatch.setitem(pipeline.PLANNERS, workflow, _planner(calls))
    data = story_data(60)
    ctx = plan_context(workflow, data, topic="sleep" if workflow == "topic" else "")

    result = await pipeline.plan_workflow(ctx)

    assert len(calls) == 1 and calls[0].ctx is ctx
    assert result.units_fingerprint == units_fingerprint(data, "")
    assert result.summary == "A video about building things." and result.key_topics == ["pricing", "hiring"]
    assert result.plan["stats"]["llm_calls"] == 0 and "plan_seconds" in result.plan["stats"]
    assert ctx.messages[0] == (51, "Reading the whole video...")


@pytest.mark.asyncio
async def test_highlights_is_never_planned_here():
    with pytest.raises(ValueError):
        await pipeline.plan_workflow(plan_context("highlights", story_data(5)))


@pytest.mark.asyncio
async def test_runtime_minimum_applies_when_the_api_had_no_duration(monkeypatch):
    monkeypatch.setitem(pipeline.PLANNERS, "recap", _planner([]))
    with pytest.raises(WorkflowError) as error:
        await pipeline.plan_workflow(plan_context("recap", story_data(20), source_seconds=100.0))
    assert error.value.code == "source_too_short"


@pytest.mark.asyncio
async def test_a_plan_past_the_timeout_is_plan_failed(monkeypatch):
    _stub_outline(monkeypatch)
    monkeypatch.setattr(pipeline, "get_config", lambda: SimpleNamespace(workflow_plan_timeout_seconds=0.05))

    async def slow(pi):
        await asyncio.sleep(5)

    monkeypatch.setitem(pipeline.PLANNERS, "series", slow)
    with pytest.raises(WorkflowError) as error:
        await pipeline.plan_workflow(plan_context("series", story_data(60)))
    assert error.value.code == "plan_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("raised", [RuntimeError("provider exploded"), KeyError("bug")])
async def test_unexpected_failures_become_plan_failed(monkeypatch, raised):
    _stub_outline(monkeypatch)

    async def broken(pi):
        raise raised

    monkeypatch.setitem(pipeline.PLANNERS, "recap", broken)
    with pytest.raises(WorkflowError) as error:
        await pipeline.plan_workflow(plan_context("recap", story_data(60)))
    assert error.value.code == "plan_failed"
    assert error.value.message == "The AI editor couldn't finish this plan. Please try again."


@pytest.mark.asyncio
@pytest.mark.parametrize("raised", [TaskCancelled(), Exception("Task cancelled"), WorkflowError("no_story", "No story.")])
async def test_cancellation_and_workflow_errors_pass_through(monkeypatch, raised):
    _stub_outline(monkeypatch)

    async def stop(pi):
        raise raised

    monkeypatch.setitem(pipeline.PLANNERS, "series", stop)
    with pytest.raises(type(raised)) as error:
        await pipeline.plan_workflow(plan_context("series", story_data(60)))
    assert error.value is raised


@pytest.mark.asyncio
async def test_an_empty_plan_is_never_returned(monkeypatch):
    _stub_outline(monkeypatch)
    monkeypatch.setitem(pipeline.PLANNERS, "topic", _planner([], segments=[]))
    with pytest.raises(WorkflowError) as error:
        await pipeline.plan_workflow(plan_context("topic", story_data(60), topic="x"))
    assert error.value.code == "plan_failed"


def test_measured_language_wins_over_the_outline():
    index = build_units(story_data(3, language=None), "")
    assert plan_language(index, {"language_code": "de_de"}, "en") == "de"
    assert plan_language(index, {"language_code": None}, "es") == "es"
    assert plan_language(index, None, "klingon") is None  # Latin script alone says nothing


@pytest.mark.asyncio
async def test_the_outline_prompt_language_comes_from_the_transcript(monkeypatch):
    seen = _stub_outline(monkeypatch)
    monkeypatch.setitem(pipeline.PLANNERS, "series", _planner([]))
    await pipeline.plan_workflow(plan_context("series", story_data(60, language="de")))
    assert seen["language_name"] == "German"


@pytest.mark.asyncio
async def test_a_whole_series_plan_round_trips_through_the_plan_cache(monkeypatch):
    data = story_data(120)
    outline = make_outline(chapters=[chapter(4, "intro", 1), chapter(120, "story")])
    draft = SeriesDraft(
        story_analysis="", series_title="Saved by a phone call", start_unit=5, skips=[],
        parts=[SeriesPartDraft(end_unit=end, title=f"Part title {n}", delivers="d", ends_on="" if n == 4 else "q", post_caption="c")
               for n, end in enumerate([30, 60, 90, 120], start=1)],
    )
    model = ScriptedModel(
        {"SourceOutline": [outline], "SeriesDraft": [draft], "SeriesCritique": [SeriesCritique(viewer_notes="", verdict="ship", fixes=[])]}
    ).install(monkeypatch)
    ctx = plan_context("series", data)

    result = await pipeline.plan_workflow(ctx)

    assert model.schemas() == ["SourceOutline", "SeriesDraft", "SeriesCritique"]
    assert [segment["hook_title"] for segment in result.segments][:2] == ["Part 1: Part title 1", "Part 2: Part title 2"]
    assert result.plan["stats"]["llm_calls"] == 3
    assert ctx.plan_cache.stored == ["outline-key"]
    restored = WorkflowResult.from_cache_json(result.to_cache_json(), "series", result.units_fingerprint)
    assert restored is not None and restored.segments == result.segments and restored.plan == result.plan
    assert WorkflowResult.from_cache_json(result.to_cache_json(), "recap", result.units_fingerprint) is None
    assert WorkflowResult.from_cache_json(result.to_cache_json(), "series", "other") is None
