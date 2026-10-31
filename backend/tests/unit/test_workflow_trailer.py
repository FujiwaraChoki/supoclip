import pytest

from src.workflows.assembly import TRAILER_JUNCTION_FADE
from src.workflows.durations import trailer_budget
from src.workflows.errors import WorkflowError
from src.workflows.schemas import OutlineArc, OutlinePayoff, SpoilerVerdict, TrailerBeat, TrailerDraft, TrailerPayoff
from src.workflows.trailer import clause_units, mark_off_limits, plan_trailer, spoiler_sentences, to_clauses
from src.workflows.units import build_units

from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, plan_input, point, story_data

QUESTIONS = (40, 75, 88)
SAFE = SpoilerVerdict(reasoning="hangs", reveals_payoff=False, offending_beat=-1, button_unresolved=True, curiosity=80)


def _beat(role, first, last=None, why="x"):
    return TrailerBeat(role=role, first_unit=first, last_unit=last or first, why=why)


def _draft(beats=None, payoffs=()):
    return TrailerDraft(
        premise="A founder bets the company on one deal.",
        payoffs=[TrailerPayoff(first_unit=a, last_unit=b, what="the deal closes") for a, b in payoffs],
        title="The deal that could end everything",
        beats=beats or [
            _beat("cold_open", 40), _beat("context", 10), _beat("escalation", 60), _beat("escalation", 20),
            _beat("escalation", 50), _beat("button", 75),
        ],
    )


def _setup(monkeypatch, answers, *, mode="full", outline=None, sentences=120, **ctx):
    data = story_data(sentences, questions=QUESTIONS)
    outline = outline or make_outline(
        chapters=[chapter(sentences, "story")],
        format="interview",
        points=[point(30, 32, core=(31, 31), importance=5, statement="The bet"),
                point(55, 57, core=(56, 56), importance=4), point(65, 66, core=(65, 65), importance=3),
                point(80, 82, core=(81, 81), importance=3)],
        payoffs=[OutlinePayoff(first_unit=100, last_unit=101, what="They sold for one dollar")],
        arcs=[OutlineArc(first_unit=1, last_unit=120, title="The bet", strength=5, shape="s")],
    )
    model = ScriptedModel(answers).install(monkeypatch)
    return plan_input(plan_context("trailer", data, **ctx), outline, mode=mode), model


def test_spoilers_cover_payoffs_arc_endings_and_story_tails(monkeypatch):
    pi, _ = _setup(monkeypatch, {})
    spoilers = spoiler_sentences(pi)
    assert {99, 100, 101, 102} <= spoilers  # payoff ±1 sentence
    assert 102 in spoilers and 120 in spoilers and 90 not in spoilers  # the arc's last 15 %
    story, _ = _setup(monkeypatch, {}, outline=make_outline(chapters=[chapter(120)], format="vlog"))
    tail = spoiler_sentences(story)
    assert 120 in tail and 112 in tail and 100 not in tail


def test_clause_ids_map_from_sentences_by_time():
    words, cursor = [], 0
    for text in "We had one plan, and it failed. Then the bank called.".split():
        words.append({"text": text, "start": cursor, "end": cursor + 300, "speaker": "A"})
        cursor += 300 + (600 if text.endswith(",") else 50)
    data = {"words": words, "language_code": "en"}
    sentences = build_units(data, "")
    clauses = build_units(data, "", granularity="clause")
    assert len(sentences) == 2 and len(clauses) == 3
    assert to_clauses(sentences, clauses, 1, 1) == (1, 2)
    assert to_clauses(sentences, clauses, 2, 2) == (3, 3)


def test_off_limits_lines_are_marked():
    listing = "#1 0:01 +4.2s A: First line.\n··· 0:10 not shown ···\n#9 0:30 +4.0s Spoiler line."
    marked = mark_off_limits(listing, {9})
    assert marked.splitlines()[2] == "#9 0:30 +4.0s [OFF LIMITS] Spoiler line."
    assert marked.splitlines()[0] == "#1 0:01 +4.2s A: First line."


@pytest.mark.asyncio
async def test_trailer_is_one_jumping_segment_inside_its_window(monkeypatch):
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [SAFE]})

    segments, plan = await plan_trailer(pi)

    assert model.schemas() == ["TrailerDraft", "SpoilerVerdict"]
    budget = trailer_budget("auto")
    assert budget.min <= plan["seconds"] <= budget.max
    segment = segments[0]
    ranges = segment["source_ranges"]
    assert segment["junction_fades"] == [TRAILER_JUNCTION_FADE] * (len(ranges) - 1)
    assert segment["max_seconds"] == budget.render_cap and segment["required"]
    assert any(b[0] < a[0] for a, b in zip(ranges, ranges[1:]))  # plays out of source order
    beats = segment["workflow_meta"]["beats"]
    assert beats[0]["role"] == "cold_open" and beats[-1]["role"] == "button"
    assert [b["range_index"] for b in beats] == list(range(len(ranges)))
    assert [b["ranges"][0] for b in plan["beats"]] == ranges
    assert segment["hook_title"] == "The deal that could end everything"
    draft_call = model.calls[0]
    assert "[OFF LIMITS]" in draft_call.prompt and "#100 " in draft_call.prompt
    assert "Will the company survive?" in draft_call.system
    judge = model.calls[1].prompt
    assert "They sold for one dollar" in judge and "Beat 1 (cold open):" in judge
    assert [message for _, message in pi.ctx.messages] == [
        "Mapping the story...", "Writing the trailer...", "Checking for spoilers...",
    ]


@pytest.mark.asyncio
async def test_a_beat_that_touches_a_payoff_never_plays(monkeypatch):
    beats = [_beat("cold_open", 40), _beat("context", 10), _beat("escalation", 101), _beat("escalation", 20),
             _beat("escalation", 50), _beat("escalation", 60), _beat("button", 75)]
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(beats), _draft(beats)], "SpoilerVerdict": [SAFE]})

    segments, _ = await plan_trailer(pi)

    assert "Beat 3 (#101–#101) uses a payoff or an [OFF LIMITS] unit." in model.calls[1].prompt
    index = pi.index
    starts = [start for start, _ in segments[0]["source_ranges"]]
    assert round(index.cut_in[101], 3) not in starts


@pytest.mark.asyncio
async def test_model_payoffs_are_off_limits_too(monkeypatch):
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [_draft(payoffs=[(60, 60)])] * 2, "SpoilerVerdict": [SAFE]})

    segments, _ = await plan_trailer(pi)

    assert round(pi.index.cut_in[60], 3) not in [start for start, _ in segments[0]["source_ranges"]]


@pytest.mark.asyncio
async def test_a_leak_drops_the_beat_and_is_judged_again(monkeypatch):
    leak = SpoilerVerdict(reasoning="beat 3 tells", reveals_payoff=True, offending_beat=3, button_unresolved=True, curiosity=40)
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [SAFE, SAFE]})
    first_segments, _ = await plan_trailer(pi)
    leaked_start = first_segments[0]["source_ranges"][2][0]

    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [leak, SAFE]})
    segments, _ = await plan_trailer(pi)

    assert model.schemas() == ["TrailerDraft", "SpoilerVerdict", "SpoilerVerdict"]
    assert leaked_start not in [start for start, _ in segments[0]["source_ranges"]]


@pytest.mark.asyncio
async def test_a_trailer_that_keeps_leaking_falls_back_to_the_outline(monkeypatch):
    leak = SpoilerVerdict(reasoning="", reveals_payoff=True, offending_beat=1, button_unresolved=True, curiosity=10)
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [leak, leak]})

    segments, plan = await plan_trailer(pi)

    index = pi.index
    beats = plan["beats"]
    assert beats[0]["role"] == "cold_open" and beats[-1]["role"] == "button"
    assert beats[0]["ranges"][0][0] == round(index.cut_in[31], 3)  # the most important point's core
    button_end = beats[-1]["ranges"][0][1]
    assert index.unit(index.unit_at(button_end - 0.4)).ends_question
    budget = trailer_budget("auto")
    assert budget.min <= plan["seconds"] <= budget.max


@pytest.mark.asyncio
async def test_the_outline_fallback_never_reuses_a_line_the_judge_caught(monkeypatch):
    # The draft opens on the outline's best point (#31), and the judge says it gives the ending away.
    leak = SpoilerVerdict(reasoning="", reveals_payoff=True, offending_beat=1, button_unresolved=True, curiosity=10)
    beats = [_beat("cold_open", 31), _beat("context", 10), _beat("escalation", 60), _beat("escalation", 20),
             _beat("escalation", 50), _beat("button", 75)]
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(beats)], "SpoilerVerdict": [leak, leak]})

    _, plan = await plan_trailer(pi)

    index = pi.index
    starts = [beat["ranges"][0][0] for beat in plan["beats"]]
    assert round(index.cut_in[31], 3) not in starts
    assert plan["beats"][0]["ranges"][0][0] == round(index.cut_in[56], 3)  # the next best point's core


@pytest.mark.asyncio
async def test_a_refit_whose_button_resolves_without_a_question_falls_back(monkeypatch):
    leak = SpoilerVerdict(reasoning="", reveals_payoff=True, offending_beat=3, button_unresolved=True, curiosity=40)
    resolved = SpoilerVerdict(reasoning="", reveals_payoff=False, offending_beat=-1, button_unresolved=False, curiosity=40)
    beats = [_beat("cold_open", 41), _beat("context", 10), _beat("escalation", 60), _beat("escalation", 20),
             _beat("escalation", 50), _beat("button", 70)]
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(beats)], "SpoilerVerdict": [leak, resolved]})

    _, plan = await plan_trailer(pi)

    assert model.schemas() == ["TrailerDraft", "SpoilerVerdict", "SpoilerVerdict"]
    index = pi.index
    assert plan["beats"][0]["ranges"][0][0] == round(index.cut_in[31], 3)  # built from the outline
    assert index.unit(index.unit_at(plan["beats"][-1]["ranges"][0][1] - 0.4)).ends_question


@pytest.mark.asyncio
async def test_a_trailer_without_a_drafted_title_sells_the_central_question(monkeypatch):
    draft = _draft()
    draft.title = ""
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [draft], "SpoilerVerdict": [SAFE]})

    segments, plan = await plan_trailer(pi)

    assert segments[0]["hook_title"] == plan["title"] == "Will the company survive?"


@pytest.mark.asyncio
async def test_a_resolved_button_hands_over_to_a_question(monkeypatch):
    resolved = SpoilerVerdict(reasoning="", reveals_payoff=False, offending_beat=-1, button_unresolved=False, curiosity=50)
    beats = [_beat("cold_open", 41), _beat("context", 10), _beat("escalation", 88), _beat("escalation", 20),
             _beat("escalation", 50), _beat("button", 70)]
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(beats)], "SpoilerVerdict": [resolved, SAFE]})

    _, plan = await plan_trailer(pi)

    index = pi.index
    assert plan["beats"][-1]["ranges"][0][0] == round(index.cut_in[88], 3)
    assert model.schemas() == ["TrailerDraft", "SpoilerVerdict", "SpoilerVerdict"]


@pytest.mark.asyncio
async def test_without_any_safe_material_the_trailer_is_unbuildable(monkeypatch):
    bare = make_outline(chapters=[chapter(30)], payoffs=[OutlinePayoff(first_unit=1, last_unit=30, what="everything")])
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [RuntimeError("down")]}, outline=bare, sentences=30)

    with pytest.raises(WorkflowError) as error:
        await plan_trailer(pi)
    assert error.value.code == "trailer_unbuildable"


@pytest.mark.asyncio
async def test_long_mode_hides_spoilers_instead_of_marking_them(monkeypatch):
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(), _draft()], "SpoilerVerdict": [SAFE]}, mode="long")

    await plan_trailer(pi)

    listing = model.calls[0].prompt
    assert "[OFF LIMITS]" not in listing and "#100 " not in listing
    assert "#31 " in listing and "#99 " not in listing  # the arc's ending is cut, not marked


async def test_coarse_transcripts_keep_their_lines(monkeypatch):
    pi, _ = _setup(monkeypatch, {})
    clauses = await clause_units(pi)
    assert clauses is not pi.index and clauses.granularity == "clause"
    pi.index.coarse = True
    assert await clause_units(pi) is pi.index
