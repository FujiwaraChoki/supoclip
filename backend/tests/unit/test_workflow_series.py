import pytest

from src.workflows.errors import WorkflowError
from src.workflows.schemas import (
    JoinCheck,
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


def _copy(count=6, ends_on=None):
    return SeriesCopy(
        series_title="The bank calls back",
        parts=[
            PartCopy(part=number, title=f"Title {number}", delivers=f"delivers {number}",
                     ends_on=(ends_on or {}).get(number, "" if number == count else f"q{number}"), post_caption=f"caption {number}")
            for number in range(1, count + 1)
        ],
    )


def _draft(start=5, ends=ENDS, skips=()):
    parts = [
        SeriesPartDraft(end_unit=end, title=f"Title {number}", best_line=-1)
        for number, end in enumerate(ends, start=1)
    ]
    return SeriesDraft(
        story_analysis="setup, turn, payoff", premise="A bank calls back", series_title="The bank calls back", start_unit=start,
        skips=[SeriesSkip(first_unit=a, last_unit=b, reason="tangent") for a, b in skips], parts=parts,
    )


def _critique(*, viewer_notes="", verdict="fix", joins=(), fixes=(), finale_lands=True, unkept_promises=()):
    return SeriesCritique(
        viewer_notes=viewer_notes, verdict=verdict, joins=list(joins), finale_lands=finale_lands,
        unkept_promises=list(unkept_promises), fixes=list(fixes),
    )


def _join(part, leaves_open="question", next_part_answers=True, next_opens_cold=True, better_end=-1):
    return JoinCheck(
        part=part, leaves_open=leaves_open, next_part_answers=next_part_answers, next_opens_cold=next_opens_cold,
        better_end=better_end,
    )


SHIP = _critique(viewer_notes="works", verdict="ship")


def _whole_story(monkeypatch, answers, *, sentences=120, chapters=None, language="en", **ctx_kwargs):
    data = story_data(sentences, questions=(24, 44))
    outline = make_outline(chapters=chapters or [chapter(4, "intro", 1), chapter(116, "story"), chapter(sentences, "outro", 1)])
    model = ScriptedModel(answers).install(monkeypatch)
    pi = plan_input(plan_context("series", data, **ctx_kwargs), outline, language=language)
    return pi, model


@pytest.mark.asyncio
async def test_whole_story_series_maps_parts_to_locked_segments(monkeypatch):
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [SHIP], "SeriesCopy": [_copy()]})

    segments, plan = await plan_series(pi)

    assert model.schemas() == ["SeriesDraft", "SeriesOpen", "SeriesCritique", "SeriesCopy"]
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
    assert segments[0]["workflow_meta"]["ends_on"] == "q1"
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
    broken = _draft(ends=[24, 20, 116])
    pi, model = _whole_story(
        monkeypatch, {"SeriesDraft": [broken, _draft()], "SeriesCritique": [SHIP]}
    )

    segments, _ = await plan_series(pi)

    assert model.schemas()[:4] == ["SeriesDraft", "SeriesDraft", "SeriesOpen", "SeriesCritique"]
    repair = model.calls[1].prompt
    assert "Part 2 ends at #20, which is not after part 1's end #24." in repair
    assert "Part 3 is " in repair and "Move its end between #" in repair
    assert len(segments) == 6


@pytest.mark.asyncio
async def test_critique_moves_an_end_and_moved_parts_get_new_copy(monkeypatch):
    critique = _critique(
        viewer_notes="part 2 runs past its cliffhanger", verdict="fix",
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

    assert model.schemas() == ["SeriesDraft", "SeriesOpen", "SeriesCritique", "SeriesCopy"]
    assert plan["parts"][1]["last_unit"] == 42
    assert plan["parts"][2]["first_unit"] == 43
    assert segments[1]["hook_title"] == "Part 2: Fresh 2"
    assert segments[-1]["workflow_meta"]["ends_on"] == ""  # the last part never ends on a question
    assert plan["title"] == "New title"
    assert pi.stats.critique_fixes == 1
    critique_prompt = model.calls[2].prompt
    assert '=== PART 1 (' in critique_prompt and '"Title 1" ===' in critique_prompt
    # Copy written before the cuts were final never reaches the critique; the joins do.
    assert "ends on:" not in critique_prompt and "JOIN 1→2: #" in critique_prompt and "SERIES END" in critique_prompt


@pytest.mark.asyncio
async def test_invalid_critique_fixes_are_ignored(monkeypatch):
    critique = _critique(
        viewer_notes="", verdict="fix",
        fixes=[
            SeriesFix(part=2, action="end_later", unit=60, last_unit=-1, new_title="", reason="too far"),
            SeriesFix(part=6, action="end_later", unit=118, last_unit=-1, new_title="", reason="the last part never ends later"),
            SeriesFix(part=9, action="retitle", unit=-1, last_unit=-1, new_title="x", reason="no such part"),
            SeriesFix(part=3, action="retitle", unit=-1, last_unit=-1, new_title="Part 3: The loan", reason="spoils"),
        ],
    )
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique]})

    segments, plan = await plan_series(pi)

    assert [part["last_unit"] for part in plan["parts"]] == ENDS
    assert segments[2]["hook_title"] == "Part 3: The loan"  # the final copy failed here: the critique's title stands


@pytest.mark.asyncio
async def test_critique_titles_survive_a_failed_retitle(monkeypatch):
    critique = _critique(
        viewer_notes="", verdict="fix",
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

    assert model.schemas()[:3] == ["SeriesDraft", "SeriesOpen", "SeriesCritique"] and "SeriesCopy" in model.schemas()
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
async def test_a_long_sponsor_read_stays_out_even_when_it_dominates_a_part(monkeypatch):
    # The read is over half of part 2's source time: a model skip that size
    # would break the 40% rule, a sponsor read is removed anyway.
    chapters = [chapter(4, "intro", 1), chapter(29, "story"), chapter(50, "sponsor"), chapter(116, "story"), chapter(120, "outro", 1)]
    pi, model = _whole_story(
        monkeypatch,
        {"SeriesDraft": [_draft(ends=[24, 64, 84, 104, 116])], "SeriesCritique": [SHIP]},
        chapters=chapters,
    )
    assert pi.index.speech_seconds(30, 50) <= 120

    segments, plan = await plan_series(pi)

    assert model.schemas().count("SeriesDraft") == 1  # no repair over the sponsor's share
    assert [30, 50] in plan["skips"]
    assert not any(f"S{number} " in segment["text"] for segment in segments for number in range(30, 51))
    assert {"code": "SPONSOR_SKIPPED", "params": {}} in plan["notes"]


@pytest.mark.asyncio
async def test_critique_fixes_that_leave_no_valid_parts_keep_the_solved_parts(monkeypatch):
    from src.workflows import series as series_module

    solve = series_module.partition_series

    def partition(*args, **kwargs):
        if args[6] is not None:  # the critique's re-partition carries its constraints
            raise WorkflowError("source_too_short", "too short")
        return solve(*args, **kwargs)

    monkeypatch.setattr(series_module, "partition_series", partition)
    critique = _critique(
        viewer_notes="", verdict="fix",
        fixes=[
            SeriesFix(part=2, action="end_earlier", unit=42, last_unit=-1, new_title="", reason="past the cliffhanger"),
            SeriesFix(part=3, action="retitle", unit=-1, last_unit=-1, new_title="The loan", reason="vague"),
        ],
    )
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique]})

    segments, plan = await plan_series(pi)

    assert [part["last_unit"] for part in plan["parts"]] == ENDS
    assert segments[2]["hook_title"] == "Part 3: The loan"  # the final copy failed here: the critique's title stands


@pytest.mark.asyncio
async def test_long_videos_pick_one_arc_sized_for_the_parts(monkeypatch):
    data = story_data(400)  # ~30 min of speech
    outline = make_outline(
        chapters=[chapter(400, "story")],
        arcs=[OutlineArc(first_unit=101, last_unit=300, title="The loan", strength=5, shape="s")],
    )
    scope = SeriesScope(
        analysis="", story_found=True, keep_chapters=[1], spare_chapters=[], list_items=0, premise="The loan"
    )
    answers = {
        "SeriesScope": [scope],
        "SeriesDraft": lambda prompt: _draft(start=101, ends=[121, 141, 160]),
        "SeriesCritique": [SHIP],
    }
    model = ScriptedModel(answers).install(monkeypatch)
    pi = plan_input(plan_context("series", data, part_count=3), outline)

    segments, plan = await plan_series(pi)

    assert model.schemas()[0] == "SeriesScope"
    assert "at most 5 minutes" in model.calls[0].system
    assert "#101–#" in model.calls[1].prompt
    span_end = int(model.calls[1].prompt.split("The material is units #101–#")[1].split(" ")[0])
    assert pi.index.speech_seconds(101, span_end) <= 3 * 100 + 30
    assert len(segments) == 3
    # The note names the stretch the parts actually cover.
    first, last = plan["parts"][0]["first_unit"], plan["parts"][-1]["last_unit"]
    expected = {"start": format_clock(pi.index.unit(first).start), "end": format_clock(pi.index.unit(last).end)}
    assert {"code": "STORY_ARC_SELECTED", "params": expected} in plan["notes"]


@pytest.mark.asyncio
async def test_no_storyline_long_enough_is_no_story(monkeypatch):
    data = story_data(400)
    outline = make_outline(chapters=[chapter(400)])
    scope = SeriesScope(analysis="", story_found=False, keep_chapters=[], spare_chapters=[], list_items=0, premise="")
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
        {"SeriesDraft": [RuntimeError("bad"), RuntimeError("bad again")], "SeriesCopy": [copy], "SeriesCritique": [SHIP]},
    )

    segments, plan = await plan_series(pi)

    # The fallback still gets copy and a critique, and says it fell back.
    assert model.schemas() == ["SeriesDraft", "SeriesDraft", "SeriesCopy", "SeriesCritique"]
    assert pi.stats.fallbacks == ["SERIES_DRAFT_FALLBACK"]
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


@pytest.mark.asyncio
async def test_a_late_start_never_drops_the_story_the_parts_depend_on(monkeypatch):
    late = _draft(start=40, ends=[64, 84, 104, 116])
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [late, late], "SeriesCritique": [SHIP]})

    _, plan = await plan_series(pi)

    repair = model.calls[1]
    assert "start_unit #40 leaves out" in repair.prompt and "The series tells the whole story" in repair.prompt
    index = pi.index
    first = plan["parts"][0]["first_unit"]
    lead = index.speech_seconds(5, first - 1) if first > 5 else 0.0
    # Part 1 may skip a warm-up of at most a minute here, and still opens on a sentence.
    assert 5 < first < 40 and lead <= 60.0 and index.unit(first).starts_sentence


@pytest.mark.asyncio
async def test_a_short_warm_up_trim_is_kept(monkeypatch):
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [_draft(start=9)], "SeriesCritique": [SHIP]})

    _, plan = await plan_series(pi)

    assert model.schemas().count("SeriesDraft") == 1  # no repair
    assert plan["parts"][0]["first_unit"] == 9


@pytest.mark.asyncio
async def test_the_series_never_ends_on_a_listeners_one_word_reply(monkeypatch):
    texts = {116: "Right."}
    data = story_data(116, questions=(24, 44), texts=texts, speakers=("A",))
    # The last sentence comes from the other speaker, as in a podcast.
    for word in data["words"][-1:]:
        word["speaker"] = "B"
    outline = make_outline(chapters=[chapter(4, "intro", 1), chapter(116, "story")])
    model = ScriptedModel({"SeriesDraft": [_draft()], "SeriesCritique": [SHIP]}).install(monkeypatch)
    pi = plan_input(plan_context("series", data), outline)

    segments, plan = await plan_series(pi)

    assert plan["parts"][-1]["last_unit"] == 115
    assert not segments[-1]["text"].endswith("Right.")


def test_a_skip_never_leaves_a_sentence_without_its_end():
    from src.workflows.series import clean_skip
    from src.workflows.units import build_units

    words, cursor = [], 0
    text = (
        "We had a plan. Now when it comes to sleep aids, I think I was too blunt. "
        "We know pills have a place. They are short term."
    )
    for token in text.split():
        words.append({"text": token, "start": cursor, "end": cursor + 300, "speaker": "A"})
        cursor += 300 + (900 if token.endswith(",") else 400 if token.endswith(".") else 50)
    index = build_units({"words": words, "language_code": "en"}, "")
    texts = [unit.text for unit in index.units]
    aside = texts.index("I think I was too blunt.") + 1
    assert texts[aside - 2].endswith("aids,")  # the line before the aside is cut mid-sentence
    assert not clean_skip(index, aside, aside)
    assert clean_skip(index, aside - 1, aside)  # the whole sentence can go


def _pi(monkeypatch, sentences, outline, answers=None, **ctx):
    ScriptedModel(answers or {}).install(monkeypatch)
    return plan_input(plan_context("series", story_data(sentences, **ctx.pop("data", {})), **ctx), outline)


def test_a_news_bulletin_has_no_single_story_and_trailing_tangents_are_framing(monkeypatch):
    from src.workflows.series import content_span, has_throughline

    arcs = [OutlineArc(first_unit=1, last_unit=40, title="A", strength=4, shape="s"),
            OutlineArc(first_unit=41, last_unit=80, title="B", strength=4, shape="s")]
    outline = make_outline(chapters=[chapter(40), chapter(80), chapter(90, "tangent", 1)], arcs=arcs, format="news")
    pi = _pi(monkeypatch, 90, outline)
    assert not has_throughline(pi, (1, 90))
    assert content_span(pi) == (1, 80)  # the weather at the end is not the finale
    outline.format = "podcast"
    assert not has_throughline(pi, (1, 80))  # two stories, neither covers half
    outline.story_arcs = arcs[:1] + [OutlineArc(first_unit=1, last_unit=70, title="C", strength=4, shape="s")]
    assert has_throughline(pi, (1, 80))


def test_an_over_long_span_leaves_out_whole_weak_chapters_never_the_hook_or_the_finale(monkeypatch):
    from src.workflows.schemas import OutlinePayoff
    from src.workflows.series import _material, _note_left_out

    outline = make_outline(
        chapters=[chapter(30, importance=5), chapter(60, importance=1, title="Housekeeping"),
                  chapter(90, importance=2, title="Side story"), chapter(120, importance=3)],
        payoffs=[OutlinePayoff(first_unit=115, last_unit=116, what="saved")],
    )
    pi = _pi(monkeypatch, 120, outline)
    total = pi.index.speech_seconds(1, 120)
    material = _material(pi, (1, 120), total - 100)
    assert (31, 60) in material.forced_skips and material.skip_reasons[(31, 60)] == "chapter left out"
    assert material.span == (1, 120)
    assert not any(low <= 10 <= high or low <= 116 <= high for low, high in material.forced_skips)
    _note_left_out(pi, material)
    assert {"code": "CHAPTERS_LEFT_OUT", "params": {"chapters": "Housekeeping"}} in pi.notes


def test_the_draft_reads_a_running_speech_clock(monkeypatch):
    from src.workflows.series import clock_listing

    outline = make_outline(chapters=[chapter(30)])
    pi = _pi(monkeypatch, 30, outline, data={"questions": (10,)})
    listing = clock_listing(pi.index, (1, 30), [(5, 6)])
    lines = {line.split()[0]: line for line in listing.splitlines() if line.startswith("#")}
    index = pi.index
    expected = index.speech_seconds(1, 4) + index.speech_seconds(7, 10)
    assert f"Σ{format_clock(expected)}" in lines["#10"] and lines["#10"].endswith("◆")
    assert "#5" not in lines


@pytest.mark.asyncio
async def test_flat_joins_move_to_a_better_end_or_lose_their_invented_cliffhanger(monkeypatch):
    critique = _critique(
        verdict="ship",
        joins=[_join(1, "none", False), _join(2, "none", False, better_end=42), _join(3)],
    )
    copy = SeriesCopy(
        series_title="New", parts=[PartCopy(part=n, title=f"T{n}", delivers="d", ends_on=f"q{n}", post_caption="c") for n in range(1, 7)],
    )
    pi, model = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique], "SeriesCopy": [copy]})

    segments, plan = await plan_series(pi)

    assert plan["parts"][1]["last_unit"] == 42  # part 2 now ends on a loop
    assert pi.stats.critique_fixes == 1


@pytest.mark.asyncio
async def test_a_flat_join_with_no_better_end_ships_without_an_ends_on(monkeypatch):
    critique = _critique(verdict="ship", joins=[_join(1, "none", False)])
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique], "SeriesCopy": [_copy()]})
    segments, plan = await plan_series(pi)
    assert segments[0]["workflow_meta"]["ends_on"] == ""
    assert segments[1]["workflow_meta"]["ends_on"] == "q2"


@pytest.mark.asyncio
async def test_the_critique_may_end_the_last_part_earlier_on_its_payoff(monkeypatch):
    critique = _critique(
        viewer_notes="ends on an aside", verdict="fix",
        fixes=[SeriesFix(part=6, action="end_earlier", unit=114, last_unit=-1, new_title="", reason="payoff at 114")],
    )
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique], "SeriesCopy": [RuntimeError("x")]})
    _, plan = await plan_series(pi)
    assert plan["parts"][-1]["last_unit"] == 114


def test_draft_rules_keep_the_finale_payoff_in_the_last_part(monkeypatch):
    from src.workflows.schemas import OutlinePayoff
    from src.workflows.series import _DraftRules, _Material

    outline = make_outline(
        chapters=[chapter(120, "story")], payoffs=[OutlinePayoff(first_unit=100, last_unit=101, what="the bank says yes")]
    )
    pi = _pi(monkeypatch, 120, outline)
    from src.workflows.durations import part_budget

    rules = _DraftRules(pi, _Material(span=(1, 120)), part_budget("auto"), None, 10)
    issues = rules(_draft(start=1, ends=[24, 44, 64, 84, 102, 120]))
    assert any(issue.startswith("The last part must contain the series payoff (#100–#101") for issue in issues)
    assert not any("series payoff" in issue for issue in rules(_draft(start=1, ends=[24, 44, 64, 84, 98, 120])))


def test_trailing_replies_and_hanging_clauses_never_end_the_series():
    from src.workflows.series import trim_backchannel_tail
    from src.workflows.units import build_units

    words, cursor = [], 0
    for text in ("Melatonin is a timing signal.", "It does not knock you out.", "And for which the margins are quite wide.", "Right."):
        for token in text.split():
            words.append({"text": token, "start": cursor, "end": cursor + 300, "speaker": "A"})
            cursor += 350
        cursor += 500
    index = build_units({"words": words, "language_code": "en"}, "")
    assert trim_backchannel_tail(index, 1, 4) == 2


@pytest.mark.asyncio
async def test_a_news_bulletin_short_enough_to_tell_whole_still_becomes_one_story(monkeypatch):
    arcs = [OutlineArc(first_unit=5, last_unit=60, title="Israel", strength=4, shape="s"),
            OutlineArc(first_unit=61, last_unit=116, title="Frontex", strength=3, shape="s")]
    outline = make_outline(chapters=[chapter(4, "intro", 1), chapter(60), chapter(116), chapter(120, "outro", 1)],
                           arcs=arcs, format="news")
    scope = SeriesScope(
        analysis="", story_found=True, keep_chapters=[2], spare_chapters=[], list_items=0, premise="The vote"
    )
    model = ScriptedModel({"SeriesScope": [scope], "SeriesDraft": lambda prompt: _draft(start=5, ends=[24, 44, 60]),
                           "SeriesCritique": [SHIP]}).install(monkeypatch)
    pi = plan_input(plan_context("series", story_data(120, questions=(24, 44))), outline)
    _, plan = await plan_series(pi)
    assert model.schemas()[0] == "SeriesScope"
    assert "NO_SINGLE_STORY" in [note["code"] for note in plan["notes"]]
    assert plan["parts"][-1]["last_unit"] <= 60


# -- round 2: the season, not the first N minutes ---------------------------------------------------


def _scope(keep, spare=(), list_items=0, story_found=True, premise="A season"):
    return SeriesScope(
        analysis="", story_found=story_found, keep_chapters=list(keep), spare_chapters=list(spare),
        list_items=list_items, premise=premise,
    )


def _season(monkeypatch, *, sentences=600, stars=(5, 2, 5, 3, 5, 2, 4, 2, 5, 2), answers=None, payoffs=(), format="interview", **ctx):
    """A long interview of ten equal chapters, too long to serialize whole."""
    from src.workflows.schemas import OutlinePayoff

    step = sentences // len(stars)
    chapters = [chapter((number + 1) * step, importance=stars[number], title=f"Chapter {number + 1}") for number in range(len(stars))]
    outline = make_outline(
        chapters=chapters, format=format,
        payoffs=[OutlinePayoff(first_unit=uid - 1, last_unit=uid, what="the answer") for uid in payoffs],
    )
    model = ScriptedModel(answers or {}).install(monkeypatch)
    return plan_input(plan_context("series", story_data(sentences), **ctx), outline), model


@pytest.mark.asyncio
async def test_the_scope_jumps_over_chapters_and_parts_break_at_the_jumps(monkeypatch):
    def draft_for(prompt):
        return _draft(start=1, ends=[20, 60, 80, 120], skips=())

    pi, model = _season(
        monkeypatch, answers={"SeriesScope": [_scope([1, 3, 5, 9])], "SeriesDraft": draft_for, "SeriesCritique": [SHIP]}
    )

    _, plan = await plan_series(pi)

    draft_call = next(call for call in model.calls if call.schema == "SeriesDraft")
    assert "the best of the video" in draft_call.system and "the whole story" not in draft_call.system
    assert "Forced skips (already removed, do not use): #61–#120, #181–#240" in draft_call.prompt
    played = {uid for part in plan["parts"] for uid in range(part["first_unit"], part["last_unit"] + 1)} - {
        uid for low, high in plan["skips"] for uid in range(low, high + 1)
    }
    assert 30 in played and 150 in played and 90 not in played and 400 not in played
    assert plan["summary"] == "A bank calls back"  # the series' own premise, not the video's one-liner
    assert any(note["code"] == "CHAPTERS_LEFT_OUT" for note in plan["notes"])


def test_chapters_are_chosen_by_value_and_filled_from_the_spare_list(monkeypatch):
    from src.workflows.series import _choose_by_value, _chapters, _fit_chapters

    pi, _ = _season(monkeypatch)
    seconds = pi.index.speech_seconds(1, 60)
    # Room for five: the five most valuable, never first come first served.
    assert _fit_chapters(pi, list(range(1, 11)), 5.2 * seconds) == sorted({1, 3, 5, 7, 9})
    # The showrunner picked too little: spare chapters come in, best first, until 85 % of the room.
    filled = _fit_chapters(pi, [1, 3], 5 * seconds, spare=[9, 5, 7])
    assert filled == [1, 3, 5, 7, 9]
    # A spare chapter that doesn't fit stays out.
    assert _fit_chapters(pi, [1, 3], 2.5 * seconds, spare=[9]) == [1, 3]
    rest = _chapters(pi)
    # Greedy longest-first would take the long ★3; a knapsack takes the two ★5.
    assert _choose_by_value(pi, [rest[0], rest[2], rest[3]], 2.1 * seconds) == {1, 3}


def test_a_story_keeps_the_arc_it_started_when_that_fits(monkeypatch):
    from src.workflows.series import _fit_chapters

    pi, _ = _season(monkeypatch, format="story")
    pi.outline.outline.story_arcs = [OutlineArc(first_unit=1, last_unit=180, title="The heist", strength=5, shape="s")]
    seconds = pi.index.speech_seconds(1, 60)
    assert _fit_chapters(pi, [1, 3], 4 * seconds) == [1, 2, 3]
    # Interviews jump freely: an arc doesn't bind their chapters.
    pi.outline.outline.format = "interview"
    assert _fit_chapters(pi, [1, 3], 4 * seconds) == [1, 3]


@pytest.mark.asyncio
async def test_a_scope_that_fails_still_chooses_the_most_valuable_chapters(monkeypatch):
    pi, _ = _season(
        monkeypatch, answers={"SeriesScope": [RuntimeError("down")], "SeriesDraft": [RuntimeError("x"), RuntimeError("x")],
                              "SeriesCopy": [RuntimeError("x")], "SeriesCritique": [SHIP]},
    )
    _, plan = await plan_series(pi)
    skipped = {uid for low, high in plan["skips"] for uid in range(low, high + 1)}
    assert 90 in skipped  # a ★2 chapter
    assert 30 not in skipped and 150 not in skipped  # ★5 chapters


@pytest.mark.asyncio
async def test_a_video_that_walks_through_a_list_keeps_every_item_and_loses_its_dead_weight_first(monkeypatch):
    from src.workflows.schemas import OutlinePayoff, SeriesListSkips

    # About 2755 s of list against 1350 s of room: the skips below cut 368 units.
    marked = SeriesListSkips(analysis="", skips=[
        SeriesSkip(first_unit=20, last_unit=200, reason="second examples"),
        SeriesSkip(first_unit=201, last_unit=250, reason="touches the one before"),
        SeriesSkip(first_unit=260, last_unit=270, reason="the payoff"),
        SeriesSkip(first_unit=300, last_unit=480, reason="asides"),
    ])

    def draft_for(prompt):
        assert "Skip about" not in prompt  # no skip arithmetic left for the draft
        return _draft(start=1, ends=[20, 40, 60])

    pi, model = _season(
        monkeypatch, sentences=600,
        answers={"SeriesScope": [_scope([1, 10], list_items=7)], "SeriesListSkips": [marked],
                 "SeriesDraft": draft_for, "SeriesCritique": [SHIP]},
    )
    pi.outline.outline.payoffs = [OutlinePayoff(first_unit=265, last_unit=266, what="the answer")]

    _, plan = await plan_series(pi)

    assert model.schemas().index("SeriesListSkips") < model.schemas().index("SeriesDraft")
    draft_call = next(call for call in model.calls if call.schema == "SeriesDraft")
    assert "The material is units #1–#600" in draft_call.prompt
    assert "#20–#200" in draft_call.prompt and "#201–#250" not in draft_call.prompt and "#260–#270" not in draft_call.prompt
    assert plan["parts"][-1]["last_unit"] == 600  # every item plays
    assert "CHAPTERS_LEFT_OUT" not in [note["code"] for note in plan["notes"]]


def test_a_series_ends_on_a_payoff_not_where_the_budget_ran_out(monkeypatch):
    from src.workflows.durations import part_budget
    from src.workflows.schemas import OutlinePayoff
    from src.workflows.series import _Material, validate_finale

    budget = part_budget("auto")
    pi, _ = _season(monkeypatch, stars=(5, 3, 3, 3, 3), sentences=100, payoffs=(55, 97))
    index = pi.index
    # Ends right after a payoff: nothing to do.
    assert validate_finale(pi, _Material(span=(1, 100)), budget, 2000).span == (1, 100)
    # Nothing near: the next chapter holding a payoff comes in while it fits, across a jump.
    extended = validate_finale(pi, _Material(span=(1, 40)), budget, 2000)
    assert extended.span == (1, 60)
    # The allowed list keeps it on one subject: not allowed, so it pulls back to the payoff instead.
    held = validate_finale(pi, _Material(span=(1, 58)), budget, 2000, allowed=[1, 2, 3])
    assert held.span == (1, 58) or held.span[1] >= 55
    # No room to extend: back to the latest payoff.
    limit = index.speech_seconds(1, 75) + 1
    pi.outline.outline.payoffs = [OutlinePayoff(first_unit=54, last_unit=55, what="the answer")]
    pulled = validate_finale(pi, _Material(span=(1, 75)), budget, limit)
    assert pulled.span == (1, 55)
    # A strong chapter past the payoff is never given up for it.
    pi.outline.outline.chapters[3].importance = 5
    assert validate_finale(pi, _Material(span=(1, 75)), budget, limit).span == (1, 75)


@pytest.mark.asyncio
async def test_a_critique_that_says_the_finale_does_not_land_looks_again_at_the_ending(monkeypatch):
    from src.workflows.schemas import OutlinePayoff

    payoff = OutlinePayoff(first_unit=103, last_unit=104, what="the bank says yes")
    outline = make_outline(chapters=[chapter(4, "intro", 1), chapter(116, "story"), chapter(120, "outro", 1)], payoffs=[payoff])
    stops = _critique(verdict="ship", finale_lands=False)
    model = ScriptedModel({"SeriesDraft": [_draft()], "SeriesCritique": [stops]}).install(monkeypatch)
    pi = plan_input(plan_context("series", story_data(120, questions=(24, 44))), outline)

    _, plan = await plan_series(pi)

    assert plan["parts"][-1]["last_unit"] == 104


@pytest.mark.asyncio
async def test_a_promise_the_series_never_keeps_is_cut(monkeypatch):
    critique = _critique(verdict="ship", unkept_promises=[50, 2, 24])
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [critique]})

    _, plan = await plan_series(pi)

    assert [50, 50] in plan["skips"]
    assert [2, 2] not in plan["skips"]  # not inside a part
    assert [24, 24] not in plan["skips"]  # a part's last unit stays


def test_skips_never_take_a_payoff_or_the_core_of_a_strong_point(monkeypatch):
    from src.workflows.durations import part_budget
    from src.workflows.schemas import OutlinePayoff, OutlinePoint
    from src.workflows.series import _DraftRules, _Material, protected_skip

    outline = make_outline(
        chapters=[chapter(120, "story")],
        payoffs=[OutlinePayoff(first_unit=50, last_unit=51, what="the bank says yes")],
        points=[OutlinePoint(statement="The rate doubled", first_unit=70, last_unit=73, core_first=71, core_last=72, importance=5),
                OutlinePoint(statement="A side note", first_unit=80, last_unit=83, core_first=81, core_last=82, importance=3)],
    )
    pi = _pi(monkeypatch, 120, outline)
    assert protected_skip(pi, 51, 53) == "the bank says yes"
    assert protected_skip(pi, 72, 72) == "The rate doubled"
    assert protected_skip(pi, 81, 82) is None and protected_skip(pi, 60, 62) is None
    rules = _DraftRules(pi, _Material(span=(1, 120)), part_budget("auto"), None, 10)
    draft = _draft(start=1, ends=[24, 44, 64, 84, 104, 120], skips=[(50, 53), (60, 62)])
    assert (50, 53) not in rules.skips(draft) and (60, 62) in rules.skips(draft)
    assert any(issue.startswith("Skip #50–#53 removes a payoff the series depends on: the bank says yes.") for issue in rules(draft))


def test_part_titles_stop_a_stranger(monkeypatch):
    from src.workflows.series import title_issues

    questions = ["Why not rest?", "Why not work?", "Why steal?", "Rest beats volume", "Sixty minutes a day", "Steal from the best"]
    assert any("3 of 6 part titles are questions; at most 2" in issue for issue in title_issues(questions, [""] * 6, "en"))
    assert title_issues(questions[1:], [""] * 5, "en") == []
    assert any("definition question" in issue for issue in title_issues(["What is a vector?", "Forty million weights"], ["", ""], "en"))
    assert any("definition question" in issue for issue in title_issues(["Was ist ein Vektor?", "Vierzig Millionen"], ["", ""], "de"))
    echo = title_issues(["Rest beats volume", "Which one would you prefer?"], ["", "Which one would you prefer?"], "en")
    assert any("repeats the previous part's last line" in issue for issue in echo)
    assert title_issues(["Rest beats volume", "Sixty minutes a day"], ["", "Which one would you prefer?"], "en") == []


@pytest.mark.asyncio
async def test_final_copy_always_runs_is_repaired_and_never_invents_a_cliffhanger(monkeypatch):
    def copy(ends):
        return SeriesCopy(
            series_title="New",
            parts=[PartCopy(part=n, title=title, delivers="d", ends_on=end, post_caption="c")
                   for n, (title, end) in enumerate(ends, start=1)],
        )

    bad = copy([("Why one?", "invented riddle everyone skipped"), ("Why two?", ""), ("Why three?", ""), ("Why four?", ""), ("T5", ""), ("T6", "")])
    good = copy([("Forty weights", "invented riddle everyone skipped"), ("Why two?", ""), ("Why three?", ""), ("T4", ""), ("T5", ""), ("T6", "")])
    texts = {23: "We talked about money all night long here", 24: "Nobody wanted to say the number out loud"}
    data = story_data(120, questions=(24, 44), texts=texts)
    outline = make_outline(chapters=[chapter(4, "intro", 1), chapter(116, "story"), chapter(120, "outro", 1)])
    model = ScriptedModel({"SeriesDraft": [_draft()], "SeriesCritique": [SHIP], "SeriesCopy": [bad, good]}).install(monkeypatch)
    pi = plan_input(plan_context("series", data), outline)

    segments, _ = await plan_series(pi)

    assert model.schemas().count("SeriesCopy") == 2  # the repair
    assert segments[0]["hook_title"] == "Part 1: Forty weights"
    # The part's last two lines never say it: ends_on goes rather than ship a label.
    assert segments[0]["workflow_meta"]["ends_on"] == ""


@pytest.mark.asyncio
async def test_the_first_sentence_is_chosen_on_its_own_within_the_lead_limit(monkeypatch):
    from src.workflows.schemas import SeriesOpen

    def pick(prompt):
        assert "#5:" in prompt and "#9:" in prompt
        return SeriesOpen(start_unit=9, stop_scroll_score=4)

    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesOpen": pick, "SeriesCritique": [SHIP]})
    _, plan = await plan_series(pi)
    assert plan["parts"][0]["first_unit"] == 9

    pi, _ = _whole_story(
        monkeypatch, {"SeriesDraft": [_draft()], "SeriesOpen": lambda prompt: SeriesOpen(start_unit=110, stop_scroll_score=5), "SeriesCritique": [SHIP]}
    )
    _, plan = await plan_series(pi)
    assert plan["parts"][0]["first_unit"] == 5  # outside the candidates: ignored


@pytest.mark.asyncio
async def test_the_critique_sees_the_central_question_candidates_and_what_is_left_out(monkeypatch):
    outline = make_outline(chapters=[chapter(4, "intro", 1), chapter(116, "story"), chapter(120, "outro", 3, title="Later plans")])
    model = ScriptedModel({"SeriesDraft": [_draft()], "SeriesCritique": [SHIP]}).install(monkeypatch)
    pi = plan_input(plan_context("series", story_data(120, questions=(22, 24))), outline)
    await plan_series(pi)
    prompt = next(call for call in model.calls if call.schema == "SeriesCritique").prompt
    assert "CENTRAL QUESTION: Will the company survive?" in prompt
    assert "CANDIDATES: #22 " in prompt  # a real question two lines before part 1's end
    assert "NEXT LINES AFTER THE SERIES (not played): #121" not in prompt  # no such line in this video
    assert "NOT IN THIS SERIES" not in prompt  # the outro plays as the end of the material here


@pytest.mark.asyncio
async def test_a_join_may_reach_further_for_a_real_loop_but_never_onto_a_weak_cut(monkeypatch):
    far = _critique(verdict="ship", joins=[_join(1, "none", False, better_end=30)])  # 6 units away
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [far]})
    _, plan = await plan_series(pi)
    assert plan["parts"][0]["last_unit"] == 30

    cold = _critique(verdict="ship", joins=[_join(1, next_opens_cold=False, better_end=28)])
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [cold]})
    _, plan = await plan_series(pi)
    assert plan["parts"][0]["last_unit"] == 28

    too_far = _critique(verdict="ship", joins=[_join(1, "none", False, better_end=40)])
    pi, _ = _whole_story(monkeypatch, {"SeriesDraft": [_draft()], "SeriesCritique": [too_far]})
    _, plan = await plan_series(pi)
    assert plan["parts"][0]["last_unit"] == 24


def test_a_whole_story_still_drops_filler_chapters_but_never_a_plot_or_the_last_of_it(monkeypatch):
    from src.workflows.durations import part_budget
    from src.workflows.series import _Material, _leave_out_padding

    budget = part_budget("auto")
    outline = make_outline(chapters=[chapter(40), chapter(60, importance=2), chapter(120), chapter(140, importance=1)])
    pi = _pi(monkeypatch, 140, outline)
    material = _leave_out_padding(pi, _Material(span=(1, 140)), budget)
    assert (41, 60) in material.forced_skips and material.skip_reasons[(41, 60)] == "chapter left out"
    assert (121, 140) not in material.forced_skips  # the edge chapter is not "inside"
    outline.story_arcs = [OutlineArc(first_unit=30, last_unit=70, title="x", strength=5, shape="s")]
    assert _leave_out_padding(pi, _Material(span=(1, 140)), budget).forced_skips == []  # an arc runs through it
    outline.story_arcs = []
    outline.format = "story"
    assert _leave_out_padding(pi, _Material(span=(1, 140)), budget).forced_skips == []  # a plot may need it


@pytest.mark.asyncio
async def test_a_weak_cut_is_recorded_for_the_eval(monkeypatch):
    # Nothing but pauses to cut on: the series still ships, and says so.
    words, cursor = [], 0
    for sentence in range(60):
        for position in range(40):
            words.append({"text": f"w{position}", "start": cursor, "end": cursor + 300, "speaker": "A"})
            cursor += 350
        cursor += 900
    data = {"version": 2, "words": words, "utterances": [], "text": "", "language_code": "hi"}
    outline = make_outline(chapters=[chapter(60)])
    ScriptedModel({"SeriesDraft": [RuntimeError("x"), RuntimeError("x")], "SeriesCopy": [RuntimeError("x")], "SeriesCritique": [SHIP]}).install(monkeypatch)
    pi = plan_input(plan_context("series", data), outline, language="hi")
    assert max(pi.index.boundary_strength(uid) for uid in range(1, len(pi.index))) < 0.75
    await plan_series(pi)
    assert "SERIES_WEAK_CUT" in pi.stats.fallbacks


def test_a_draft_that_starts_inside_a_left_out_chapter_never_brings_it_back(monkeypatch):
    from src.workflows.series import _Material, _partition_span

    pi = _pi(monkeypatch, 120, make_outline(chapters=[chapter(120)]))
    material = _Material(span=(1, 120), forced_skips=[(3, 8)], skip_reasons={(3, 8): "chapter left out"})
    assert _partition_span(pi.index, material, _draft(start=5, ends=[40, 80, 120]), {})[0] == 9
