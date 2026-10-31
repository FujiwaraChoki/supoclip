import pytest

from src.workflows.errors import WorkflowError
from src.workflows.schemas import (
    OutlineArc,
    PartCopy,
    SeriesCopy,
    SeriesCritique,
    SeriesDraft,
    SeriesFix,
    SeriesPartDraft,
    SeriesScope,
    SeriesSkip,
)
from src.workflows.series import plan_series
from src.workflows.units import format_clock

from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, plan_input, story_data

ENDS = [24, 44, 64, 84, 104, 116]


def _draft(start=5, ends=ENDS, skips=(), last_ends_on=""):
    parts = [
        SeriesPartDraft(
            end_unit=end, title=f"Title {number}", delivers=f"delivers {number}",
            ends_on="" if number == len(ends) else f"question {number}", post_caption=f"caption {number}",
        )
        for number, end in enumerate(ends, start=1)
    ]
    if last_ends_on:
        parts[-1].ends_on = last_ends_on
    return SeriesDraft(
        story_analysis="setup, turn, payoff", series_title="The bank calls back", start_unit=start,
        skips=[SeriesSkip(first_unit=a, last_unit=b, reason="tangent") for a, b in skips], parts=parts,
    )


SHIP = SeriesCritique(viewer_notes="works", verdict="ship", fixes=[])


def _whole_story(monkeypatch, answers, *, sentences=120, chapters=None, language="en", **ctx_kwargs):
    data = story_data(sentences, questions=(24, 44))
    outline = make_outline(chapters=chapters or [chapter(4, "intro", 1), chapter(116, "story"), chapter(sentences, "outro", 1)])
    model = ScriptedModel(answers).install(monkeypatch)
    pi = plan_input(plan_context("series", data, **ctx_kwargs), outline, language=language)
    return pi, model


@pytest.mark.asyncio
async def test_whole_story_series_maps_parts_to_locked_segments(monkeypatch):
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [SHIP]})

    segments, plan = await plan_series(pi)

    assert model.schemas() == ["SeriesDraft", "SeriesCritique"]
    assert [segment["part_number"] for segment in segments] == [1, 2, 3, 4, 5, 6]
    assert [part["last_unit"] for part in plan["parts"]] == ENDS
    assert plan["parts"][0]["first_unit"] == 5
    index = pi.index
    for segment, part in zip(segments, plan["parts"]):
        assert segment["planned"] and segment["required"] and segment["workflow_role"] == "part"
        assert segment["max_seconds"] == 155.0
        assert segment["junction_fades"] == []
        assert segment["source_ranges"] == [[round(index.cut_in[part["first_unit"]], 3), round(index.cut_out[part["last_unit"]], 3)]]
        assert 45 <= part["seconds"] <= 150
        meta = segment["workflow_meta"]
        assert meta["planned"] and meta["ranges"] == segment["source_ranges"] and meta["part_count"] == 6
        assert meta["series_title"] == "The bank calls back"
    assert segments[0]["hook_title"] == "Part 1: Title 1"
    assert segments[0]["workflow_meta"]["ends_on"] == "question 1"
    assert segments[-1]["workflow_meta"]["ends_on"] == ""
    assert segments[0]["start_time"] == "00:" + f"{round(index.cut_in[5]):02d}"
    # Adjacent parts share one exact cut: the next part starts on the following unit.
    for left, right in zip(plan["parts"], plan["parts"][1:]):
        assert right["first_unit"] == left["last_unit"] + 1
    assert plan["title"] == "The bank calls back" and plan["workflow"] == "series"
    progress = [message for _, message in pi.ctx.messages]
    assert progress == ["Mapping the story...", "Planning parts...", "Checking every cut..."]


@pytest.mark.asyncio
async def test_draft_prompt_carries_the_contract_material_and_instructions(monkeypatch):
    pi, model = _whole_story(
        monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [SHIP]}, instructions="Focus on the bank"
    )
    await plan_series(pi)

    call = model.calls[0]
    assert call.structural
    assert "Choose how many parts the story needs, between 2 and" in call.system
    assert "45-150s of speech, ideally 60-100s" in call.system
    assert "The material is units #5–#116" in call.prompt
    assert "<creator_instructions>\nFocus on the bank\n</creator_instructions>" in call.prompt
    assert "#4 " not in call.prompt.split("<transcript>")[1]


@pytest.mark.asyncio
async def test_skips_become_ranges_with_soft_junctions(monkeypatch):
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft(skips=[(50, 52)])], "SeriesCritique": [SHIP]})

    segments, plan = await plan_series(pi)

    third = segments[2]
    index = pi.index
    assert third["source_ranges"] == [
        [round(index.cut_in[45], 3), round(index.cut_out[49], 3)],
        [round(index.cut_in[53], 3), round(index.cut_out[64], 3)],
    ]
    assert third["junction_fades"] == [0.1]
    assert plan["skips"] == [[50, 52]]
    assert "S50 " not in third["text"] and "S53 " in third["text"]


@pytest.mark.asyncio
async def test_a_broken_draft_gets_one_repair_with_the_exact_issues(monkeypatch):
    broken = _draft(ends=[24, 20, 116], last_ends_on="what next?")
    pi, model = _whole_story(
        monkeypatch, {"SeriesDraft": [broken, _draft()], "SeriesCritique": [SHIP]}
    )

    segments, _ = await plan_series(pi)

    assert model.schemas() == ["SeriesDraft", "SeriesDraft", "SeriesCritique"]
    repair = model.calls[1].prompt
    assert "Part 2 ends at #20, which is not after part 1's end #24." in repair
    assert "Part 3 (the last part) must have an empty ends_on." in repair
    assert "Part 3 is " in repair and "Move its end between #" in repair
    assert len(segments) == 6


@pytest.mark.asyncio
async def test_critique_moves_an_end_and_moved_parts_get_new_copy(monkeypatch):
    critique = SeriesCritique(
        viewer_notes="part 2 runs past its cliffhanger",
        verdict="fix",
        fixes=[SeriesFix(part=2, action="end_earlier", unit=42, last_unit=-1, new_title="", reason="ends after the answer")],
    )
    copy = SeriesCopy(
        series_title="New title",
        parts=[PartCopy(part=n, title=f"Fresh {n}", delivers="d", ends_on="q", post_caption="c") for n in range(1, 7)],
    )
    pi, model = _whole_story(
        monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique], "SeriesCopy": [copy]}
    )

    segments, plan = await plan_series(pi)

    assert model.schemas() == ["SeriesDraft", "SeriesCritique", "SeriesCopy"]
    assert plan["parts"][1]["last_unit"] == 42
    assert plan["parts"][2]["first_unit"] == 43
    assert segments[1]["hook_title"] == "Part 2: Fresh 2"
    assert segments[-1]["workflow_meta"]["ends_on"] == ""  # the last part never ends on a question
    assert plan["title"] == "New title"
    assert pi.stats.critique_fixes == 1
    critique_prompt = model.calls[1].prompt
    assert '=== PART 1 (' in critique_prompt and '"Title 1" — delivers: delivers 1 — ends on: question 1 ===' in critique_prompt


@pytest.mark.asyncio
async def test_invalid_critique_fixes_are_ignored(monkeypatch):
    critique = SeriesCritique(
        viewer_notes="",
        verdict="fix",
        fixes=[
            SeriesFix(part=2, action="end_later", unit=60, last_unit=-1, new_title="", reason="too far"),
            SeriesFix(part=6, action="end_earlier", unit=114, last_unit=-1, new_title="", reason="last part"),
            SeriesFix(part=9, action="retitle", unit=-1, last_unit=-1, new_title="x", reason="no such part"),
            SeriesFix(part=3, action="retitle", unit=-1, last_unit=-1, new_title="Part 3: The loan", reason="spoils"),
        ],
    )
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique]})

    segments, plan = await plan_series(pi)

    assert [part["last_unit"] for part in plan["parts"]] == ENDS
    assert "SeriesCopy" not in model.schemas()
    assert segments[2]["hook_title"] == "Part 3: The loan"


@pytest.mark.asyncio
async def test_critique_titles_survive_a_failed_retitle(monkeypatch):
    critique = SeriesCritique(
        viewer_notes="",
        verdict="fix",
        fixes=[
            SeriesFix(part=2, action="end_earlier", unit=47, last_unit=-1, new_title="", reason="past the cliffhanger"),
            SeriesFix(part=1, action="retitle", unit=-1, last_unit=-1, new_title="The loan nobody wanted", reason="vague"),
        ],
    )
    pi, model = _whole_story(
        monkeypatch,
        {"SeriesDraft": [_draft(ends=[24, 50, 70, 90, 116])], "SeriesCritique": [critique], "SeriesCopy": [RuntimeError("down")]},
    )

    segments, plan = await plan_series(pi)

    assert model.schemas() == ["SeriesDraft", "SeriesCritique", "SeriesCopy"]
    assert plan["parts"][1]["last_unit"] == 47
    assert segments[0]["hook_title"] == "Part 1: The loan nobody wanted"
    assert all(segment["hook_title"].startswith(f"Part {n}") for n, segment in enumerate(segments, start=1))


@pytest.mark.asyncio
async def test_sponsor_reads_inside_the_story_are_forced_skips(monkeypatch):
    chapters = [chapter(4, "intro", 1), chapter(40, "story"), chapter(43, "sponsor"), chapter(116, "story"), chapter(120, "outro", 1)]
    pi, model = _whole_story(
        monkeypatch,
        {"SeriesDraft": [_draft(ends=[24, 50, 70, 90, 116])], "SeriesCritique": [SHIP]},
        chapters=chapters,
    )

    segments, plan = await plan_series(pi)

    assert "Forced skips (already removed, do not use): #41–#43" in model.calls[0].prompt
    second = segments[1]
    assert len(second["source_ranges"]) == 2 and "S42 " not in second["text"]
    assert {"code": "SPONSOR_SKIPPED", "params": {}} in plan["notes"]


@pytest.mark.asyncio
async def test_long_videos_pick_one_arc_sized_for_the_parts(monkeypatch):
    data = story_data(400)  # ~30 min of speech
    outline = make_outline(
        chapters=[chapter(400, "story")],
        arcs=[OutlineArc(first_unit=100, last_unit=300, title="The loan", strength=5, shape="s")],
    )
    scope = SeriesScope(analysis="", story_found=True, first_unit=101, last_unit=300, premise="The loan")
    answers = {
        "SeriesScope": [scope],
        "SeriesDraft": lambda prompt: _draft(start=101, ends=[121, 141, 160]),
        "SeriesCritique": [SHIP],
    }
    model = ScriptedModel(answers).install(monkeypatch)
    pi = plan_input(plan_context("series", data, part_count=3), outline)

    segments, plan = await plan_series(pi)

    assert model.schemas()[0] == "SeriesScope"
    assert "at most 7 minutes" in model.calls[0].system
    assert "#101–#" in model.calls[1].prompt
    span_end = int(model.calls[1].prompt.split("The material is units #101–#")[1].split(" ")[0])
    assert pi.index.speech_seconds(101, span_end) <= 3 * 100 / 0.7
    assert len(segments) == 3
    # The note names the stretch the parts actually cover.
    first, last = plan["parts"][0]["first_unit"], plan["parts"][-1]["last_unit"]
    expected = {"start": format_clock(pi.index.unit(first).start), "end": format_clock(pi.index.unit(last).end)}
    assert {"code": "STORY_ARC_SELECTED", "params": expected} in plan["notes"]


@pytest.mark.asyncio
async def test_no_storyline_long_enough_is_no_story(monkeypatch):
    data = story_data(400)
    outline = make_outline(chapters=[chapter(400)])
    scope = SeriesScope(analysis="", story_found=False, first_unit=200, last_unit=205, premise="")
    ScriptedModel({"SeriesScope": [scope]}).install(monkeypatch)
    pi = plan_input(plan_context("series", data), outline)

    with pytest.raises(WorkflowError) as error:
        await plan_series(pi)
    assert error.value.code == "no_story"


@pytest.mark.asyncio
async def test_without_a_draft_the_series_is_partitioned_and_retitled(monkeypatch):
    copy = SeriesCopy(
        series_title="Fallback",
        parts=[PartCopy(part=n, title=f"Auto {n}", delivers="", ends_on="", post_caption="") for n in range(1, 11)],
    )
    pi, model = _whole_story(
        monkeypatch,
        {"SeriesDraft": [RuntimeError("bad"), RuntimeError("bad again")], "SeriesCopy": [copy]},
    )

    segments, plan = await plan_series(pi)

    assert model.schemas() == ["SeriesDraft", "SeriesDraft", "SeriesCopy"]
    assert len(segments) >= 2 and segments[0]["hook_title"] == "Part 1: Auto 1"
    assert plan["title"] == "Fallback"
    parts = plan["parts"]
    assert parts[0]["first_unit"] == 5 and parts[-1]["last_unit"] == 116
    assert all(45 <= part["seconds"] <= 150 for part in parts)


@pytest.mark.asyncio
async def test_part_labels_follow_the_plan_language(monkeypatch):
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [SHIP]}, language="de")

    segments, _ = await plan_series(pi)

    assert segments[0]["hook_title"] == "Teil 1: Title 1"


@pytest.mark.asyncio
async def test_too_little_story_fails_before_any_model_call(monkeypatch):
    data = story_data(12)
    model = ScriptedModel({}).install(monkeypatch)
    pi = plan_input(plan_context("series", data), make_outline(chapters=[chapter(12)]))

    with pytest.raises(WorkflowError) as error:
        await plan_series(pi)
    assert error.value.code == "source_too_short" and model.calls == []
