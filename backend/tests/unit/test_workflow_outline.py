import json
from types import SimpleNamespace

import pytest

from src.workflows import outline as outline_module
from src.workflows.contracts import OUTLINE_VERSION
from src.workflows.errors import WorkflowError
from src.workflows.llm import PlanStats
from src.workflows.outline import (
    assemble_reduce,
    get_outline,
    normalize_outline,
    outline_mode,
    plan_windows,
)
from src.workflows.schemas import OutlineArc, OutlinePayoff, OutlineReduce, OutlineWindow
from src.workflows.units import build_units

from workflow_planner_fakes import (
    MemoryCache,
    ScriptedModel,
    chapter,
    make_outline,
    plan_context,
    point,
    story_data,
)


def _limit(monkeypatch, *, configured=None, model="anthropic:claude"):
    monkeypatch.setattr(outline_module, "get_config", lambda: SimpleNamespace(workflow_full_context_max_words=configured))
    monkeypatch.setattr(outline_module, "model_name", lambda structural: model)


def test_full_context_limit_by_provider_and_config(monkeypatch):
    _limit(monkeypatch, model="ollama:qwen")
    assert outline_module.full_context_word_limit() == 9000
    _limit(monkeypatch, model="openrouter:anthropic/claude-sonnet-5.5")
    assert outline_module.full_context_word_limit() == 40000
    _limit(monkeypatch, configured=1234, model="ollama:qwen")
    assert outline_module.full_context_word_limit() == 1234


def test_outline_mode_switches_to_long_past_the_word_limit(monkeypatch):
    index = build_units(story_data(100), "")
    size = outline_module.word_count(index)
    assert 1100 < size < 1400  # 1200 English words, measured in listing tokens
    _limit(monkeypatch, configured=int(size) + 1)
    assert outline_mode(index) == "full"
    _limit(monkeypatch, configured=int(size) - 1)
    assert outline_mode(index) == "long"


def test_non_latin_scripts_count_by_tokens_not_words():
    english = build_units({"words": [{"text": "Hello there friend.", "start": 0, "end": 900}]}, "")
    hindi = build_units({"words": [{"text": "नमस्ते दोस्त कैसे हो।", "start": 0, "end": 900}]}, "")
    assert outline_module.word_count(hindi) > 1.5 * outline_module.word_count(english)


def test_windows_are_consecutive_cover_everything_and_close_on_strong_cuts():
    index = build_units(story_data(500), "")  # ~38 min
    windows = plan_windows(index, max_words=100_000, max_seconds=20 * 60)

    assert windows[0][0] == 1 and windows[-1][1] == len(index)
    assert all(a[1] + 1 == b[0] for a, b in zip(windows, windows[1:]))
    for first, last in windows[:-1]:
        assert index.unit(last).end - index.unit(first).start >= 20 * 60
        assert index.boundary_strength(last) >= 0.75
    assert len(windows) == 2  # the 18-minute tail is long enough to stand alone


def test_a_short_tail_joins_the_last_window():
    index = build_units(story_data(300), "")  # ~22 min
    windows = plan_windows(index, max_words=100_000, max_seconds=20 * 60)
    assert windows == [(1, len(index))]


def test_word_budget_closes_windows_too():
    index = build_units(story_data(100), "")
    windows = plan_windows(index, max_words=300, max_seconds=10_000)
    assert len(windows) == 4
    assert all(sum(outline_module._unit_words(index, u) for u in range(a, b + 1)) >= 300 for a, b in windows[:-1])


def test_normalize_retiles_chapters_and_repairs_ranges():
    index = build_units(story_data(40), "")
    raw = make_outline(
        chapters=[chapter(30), chapter(10, "intro"), chapter(10), chapter(99), chapter(20, importance=9)],
        points=[point(25, 20, core=(5, 6)), point(8, 12, core=(9, 10)), point(8, 12, core=(9, 9))],
        arcs=[OutlineArc(first_unit=50, last_unit=3, title="Arc", strength=0, shape="s")],
        payoffs=[OutlinePayoff(first_unit=39, last_unit=45, what="the end")],
        key_topics=[" Pricing ", "pricing", "", "Hiring"],
    )

    outline = normalize_outline(raw, index)

    assert [c.end_unit for c in outline.chapters] == [10, 20, 30, 40]
    assert outline.chapters[0].kind == "intro" and outline.chapters[1].importance == 5
    first = outline.main_points[0]
    assert (first.first_unit, first.last_unit, first.core_first, first.core_last) == (20, 25, 20, 25)
    assert len(outline.main_points) == 2  # the duplicate span is dropped
    assert (outline.story_arcs[0].first_unit, outline.story_arcs[0].last_unit, outline.story_arcs[0].strength) == (3, 40, 1)
    assert (outline.payoffs[0].first_unit, outline.payoffs[0].last_unit) == (39, 40)
    assert outline.key_topics == ["Pricing", "Hiring"]


def test_no_chapters_become_one_chapter_over_everything():
    index = build_units(story_data(12), "")
    outline = normalize_outline(make_outline(chapters=[]), index)
    assert [(c.end_unit, c.kind) for c in outline.chapters] == [(12, "content")]


@pytest.mark.asyncio
async def test_full_mode_reads_once_and_caches_by_fingerprint(monkeypatch):
    _limit(monkeypatch)
    data = story_data(30)
    index = build_units(data, "")
    model = ScriptedModel({"SourceOutline": [make_outline(chapters=[chapter(30)])]}).install(monkeypatch)
    cache = MemoryCache()
    ctx = plan_context("recap", data, cache=cache)

    first = await get_outline(ctx, index, "English", PlanStats())
    second = await get_outline(ctx, index, "English", PlanStats())

    assert model.schemas() == ["SourceOutline"]
    assert first.mode == "full" and not first.cached and second.cached
    assert cache.stored == ["outline-key"]
    payload = json.loads(cache.items["outline-key"])
    assert payload["version"] == OUTLINE_VERSION and payload["units_fingerprint"] == index.fingerprint
    assert model.calls[0].structural
    assert "#1 0:01" in model.calls[0].prompt and "units #1–#30" in model.calls[0].prompt
    assert "Write titles, summaries and statements in English" in model.calls[0].system


@pytest.mark.asyncio
async def test_a_cached_outline_for_other_words_is_ignored(monkeypatch):
    _limit(monkeypatch)
    data = story_data(30)
    index = build_units(data, "")
    stale = json.dumps(
        {"version": OUTLINE_VERSION, "units_fingerprint": "other", "mode": "full",
         "outline": make_outline(chapters=[chapter(30)]).model_dump()}
    )
    model = ScriptedModel({"SourceOutline": [make_outline(chapters=[chapter(30)])]}).install(monkeypatch)
    ctx = plan_context("recap", data, cache=MemoryCache({"outline-key": stale}))

    result = await get_outline(ctx, index, "English", PlanStats())

    assert not result.cached and model.schemas() == ["SourceOutline"]


@pytest.mark.asyncio
async def test_a_cache_read_error_reads_the_video_again(monkeypatch):
    _limit(monkeypatch)
    data = story_data(30)
    index = build_units(data, "")
    model = ScriptedModel({"SourceOutline": [make_outline(chapters=[chapter(30)])]}).install(monkeypatch)
    cache = MemoryCache()

    async def broken(key):
        raise ConnectionError("db down")

    cache.load = broken
    result = await get_outline(plan_context("recap", data, cache=cache), index, "English", PlanStats())
    assert not result.cached and model.schemas() == ["SourceOutline"]


@pytest.mark.asyncio
async def test_full_mode_failure_after_its_retry_is_analysis_incomplete(monkeypatch):
    _limit(monkeypatch)
    data = story_data(30)
    ScriptedModel({"SourceOutline": [RuntimeError("bad json"), RuntimeError("bad json")]}).install(monkeypatch)
    cache = MemoryCache()

    with pytest.raises(WorkflowError) as error:
        await get_outline(plan_context("recap", data, cache=cache), build_units(data, ""), "English", PlanStats())

    assert error.value.code == "analysis_incomplete"
    assert cache.stored == []


def _window(low, high):
    middle = (low + high) // 2
    return OutlineWindow(
        analysis="",
        chapters=[chapter(middle, title=f"C{low}"), chapter(high, title=f"D{low}")],
        main_points=[point(low + 2, low + 4, importance=3, statement=f"point at {low}")],
        story_arcs=[OutlineArc(first_unit=low, last_unit=high, title="Arc", strength=3, shape="s")],
        payoffs=[OutlinePayoff(first_unit=high - 2, last_unit=high - 1, what=f"reveal {low}")],
        open_threads=[f"thread {low}"],
    )


@pytest.mark.asyncio
async def test_long_mode_maps_every_window_and_reduces_once(monkeypatch):
    _limit(monkeypatch, configured=10)
    data = story_data(500)
    index = build_units(data, "")
    windows = plan_windows(index, max_words=outline_module.WINDOW_MAX_WORDS)
    assert len(windows) == 2

    def map_answer(prompt):
        first = int(prompt.split("\n")[1].split(" ")[0][1:])
        low, high = next(window for window in windows if window[0] == first)
        return _window(low, high)

    reduce = OutlineReduce(
        analysis="", format="podcast", language="en", one_line="One", central_question="Q?", throughline="T",
        key_topics=["sleep"], chapter_importance=[1, 5, 2],
        main_point_order=[2, 9, 2],
        story_arcs=[OutlineArc(first_unit=windows[0][0] + 1, last_unit=windows[1][1] - 1, title="Whole", strength=5, shape="s")],
        payoffs=[OutlinePayoff(first_unit=windows[1][1] - 1, last_unit=windows[1][1] - 1, what="final reveal"),
                 OutlinePayoff(first_unit=5, last_unit=5, what="invented")],
    )
    model = ScriptedModel({"OutlineWindow": map_answer, "OutlineReduce": [reduce]}).install(monkeypatch)
    ctx = plan_context("series", data)

    result = await get_outline(ctx, index, "English", PlanStats())

    assert result.mode == "long"
    assert sorted(model.schemas()) == ["OutlineReduce", "OutlineWindow", "OutlineWindow"]
    assert [call.structural for call in model.calls if call.schema == "OutlineWindow"] == [False, False]
    reduce_call = next(call for call in model.calls if call.schema == "OutlineReduce")
    assert "C4 #" in reduce_call.prompt and "P2 #" in reduce_call.prompt and "thread 1" in reduce_call.prompt
    outline = result.outline
    assert [c.importance for c in outline.chapters] == [1, 5, 2, 3]
    assert outline.chapters[-1].end_unit == len(index)
    assert [p.statement for p in outline.main_points] == [f"point at {windows[1][0]}"]
    arc = outline.story_arcs[0]
    assert (arc.first_unit, arc.last_unit) == (windows[0][0], windows[1][1])
    assert [p.what for p in outline.payoffs] == [f"reveal {windows[1][0]}"]
    progress = [message for _, message in ctx.messages]
    assert progress[0] == "Mapping the video (0/2)..." and "Mapping the video (2/2)..." in progress


@pytest.mark.asyncio
async def test_a_window_that_fails_twice_fails_the_outline(monkeypatch):
    _limit(monkeypatch, configured=10)
    data = story_data(700)
    calls = {"count": 0}

    def flaky(prompt):
        calls["count"] += 1
        if prompt.startswith("<transcript>\n#1 "):
            return RuntimeError("model error")
        return _window(1, 2)

    ScriptedModel({"OutlineWindow": flaky}).install(monkeypatch)
    with pytest.raises(WorkflowError) as error:
        await get_outline(plan_context("series", data), build_units(data, ""), "English", PlanStats())
    assert error.value.code == "analysis_incomplete"
    assert "couldn't read the whole video" in error.value.message


def test_reduce_falls_back_to_importance_order_without_a_point_order():
    index = build_units(story_data(20), "")
    points = [point(2, 3, importance=2, statement="minor"), point(5, 6, importance=5, statement="major")]
    reduce = OutlineReduce(
        analysis="", format="other", language="en", one_line="", central_question="",
        throughline="", key_topics=[], chapter_importance=[], main_point_order=[],
        story_arcs=[], payoffs=[],
    )
    outline = assemble_reduce(reduce, index, [chapter(20)], [(1, 20)], points, [], [])
    assert [p.statement for p in outline.main_points] == ["major", "minor"]
