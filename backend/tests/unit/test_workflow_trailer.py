import pytest

from src.workflows.assembly import TRAILER_JUNCTION_FADE
from src.workflows.durations import trailer_budget
from src.workflows.errors import WorkflowError
from src.workflows.assembly import TrailerBeatIn
from src.workflows.schemas import (
    ButtonPick,
    OutlineArc,
    OutlinePayoff,
    SpoilerVerdict,
    TrailerBeat,
    TrailerDraft,
    TrailerPayoff,
    TrailerReclaimed,
    TrailerTitles,
)
from src.workflows.trailer import clause_units, mark_off_limits, plan_trailer, spoiler_sentences, to_clauses
from src.workflows.units import build_units

from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, plan_input, point, story_data

QUESTIONS = (40, 75, 88)
# What the beats say, so the drafted titles are heard in them.
TEXTS = {
    40: "One deal could end everything we built here today",
    10: "One signature from the whole company losing it all",
    60: "Why would anyone sell a company for one dollar",
}
SAFE = SpoilerVerdict(reasoning="hangs", reveals_payoff=False, offending_beat=-1, button_unresolved=True, chopped_beat=-1, best_title=1, curiosity=80, unclear_beat=-1, weakest_beat=-1, thread_holds=True)


def _beat(role, first, last=None, why="x"):
    return TrailerBeat(role=role, first_unit=first, last_unit=last or first, why=why)


def _draft(beats=None, payoffs=()):
    return TrailerDraft(
        premise="A founder bets the company on one deal.",
        thread="Will this deal end the company?",
        payoffs=[TrailerPayoff(first_unit=a, last_unit=b, what="the deal closes") for a, b in payoffs],
        reclaimed=[],
        beats=beats or [
            _beat("cold_open", 40), _beat("context", 10, 11), _beat("escalation", 20), _beat("escalation", 50),
            _beat("escalation", 60), _beat("button", 75),
        ],
        stranger_read="clear",
        title_candidates=["The deal that could end everything", "One signature from losing it all", "Why sell a company for one dollar"],
    )


def _setup(monkeypatch, answers, *, mode="full", outline=None, sentences=120, **ctx):
    data = story_data(sentences, questions=QUESTIONS, texts=TEXTS)
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
    assert {100, 101, 102} <= spoilers and 99 not in spoilers  # payoff +1 sentence; the setup before it stays
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
    leak = SpoilerVerdict(reasoning="beat 3 tells", reveals_payoff=True, offending_beat=3, button_unresolved=True, chopped_beat=-1, best_title=1, curiosity=40, unclear_beat=-1, weakest_beat=-1, thread_holds=True)
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [SAFE, SAFE]})
    first_segments, _ = await plan_trailer(pi)
    leaked_start = first_segments[0]["source_ranges"][2][0]

    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [leak, SAFE]})
    segments, _ = await plan_trailer(pi)

    assert model.schemas() == ["TrailerDraft", "SpoilerVerdict", "SpoilerVerdict"]
    assert leaked_start not in [start for start, _ in segments[0]["source_ranges"]]


@pytest.mark.asyncio
async def test_a_trailer_that_keeps_leaking_falls_back_to_the_outline(monkeypatch):
    leak = SpoilerVerdict(reasoning="", reveals_payoff=True, offending_beat=1, button_unresolved=True, chopped_beat=-1, best_title=1, curiosity=10, unclear_beat=-1, weakest_beat=-1, thread_holds=True)
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [leak, leak, SAFE]})

    segments, plan = await plan_trailer(pi)

    assert model.schemas()[:4] == ["TrailerDraft", "SpoilerVerdict", "SpoilerVerdict", "SpoilerVerdict"]
    assert pi.stats.fallbacks == ["TRAILER_OUTLINE_FALLBACK"]  # the eval can see it
    index = pi.index
    beats = plan["beats"]
    assert beats[0]["role"] == "cold_open" and beats[-1]["role"] == "button"
    # What survived of the draft plays, with the outline filling in around it.
    starts = [beat["ranges"][0][0] for beat in beats]
    assert round(index.cut_in[75], 3) in starts or round(index.cut_in[60], 3) in starts
    assert round(index.cut_in[40], 3) not in starts  # the first leak stays out
    budget = trailer_budget("auto")
    assert budget.min <= plan["seconds"] <= budget.max


@pytest.mark.asyncio
async def test_the_outline_fallback_never_reuses_a_line_the_judge_caught(monkeypatch):
    # The draft opens on the outline's best point (#31), and the judge says it gives the ending away.
    leak = SpoilerVerdict(reasoning="", reveals_payoff=True, offending_beat=1, button_unresolved=True, chopped_beat=-1, best_title=1, curiosity=10, unclear_beat=-1, weakest_beat=-1, thread_holds=True)
    beats = [_beat("cold_open", 31), _beat("context", 10), _beat("escalation", 20), _beat("escalation", 50),
             _beat("escalation", 60), _beat("button", 75)]
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(beats)], "SpoilerVerdict": [leak, leak, SAFE]})

    _, plan = await plan_trailer(pi)

    index = pi.index
    starts = [beat["ranges"][0][0] for beat in plan["beats"]]
    assert round(index.cut_in[31], 3) not in starts


@pytest.mark.asyncio
async def test_a_weak_button_alone_never_sends_the_trailer_to_the_fallback(monkeypatch):
    leak = SpoilerVerdict(reasoning="", reveals_payoff=True, offending_beat=3, button_unresolved=True, chopped_beat=-1, best_title=1, curiosity=70, unclear_beat=-1, weakest_beat=-1, thread_holds=True)
    resolved = SpoilerVerdict(reasoning="", reveals_payoff=False, offending_beat=-1, button_unresolved=False, chopped_beat=-1, best_title=1, curiosity=70, unclear_beat=-1, weakest_beat=-1, thread_holds=True)
    beats = [_beat("cold_open", 41), _beat("context", 10, 11), _beat("escalation", 20), _beat("escalation", 50),
             _beat("escalation", 60), _beat("escalation", 65), _beat("button", 70)]
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(beats), _draft(beats)], "SpoilerVerdict": [leak, resolved]})

    _, plan = await plan_trailer(pi)

    assert "TRAILER_WEAK_BUTTON" in pi.stats.fallbacks and "TRAILER_OUTLINE_FALLBACK" not in pi.stats.fallbacks
    index = pi.index
    assert plan["beats"][0]["ranges"][0][0] == round(index.cut_in[41], 3)


@pytest.mark.asyncio
async def test_a_trailer_without_a_drafted_title_sells_the_central_question(monkeypatch):
    draft = _draft()
    draft.title_candidates = []
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [draft], "SpoilerVerdict": [SAFE]})

    segments, plan = await plan_trailer(pi)

    assert segments[0]["hook_title"] == plan["title"] == "Will the company survive?"


@pytest.mark.asyncio
async def test_a_resolved_button_hands_over_to_a_question(monkeypatch):
    resolved = SpoilerVerdict(reasoning="", reveals_payoff=False, offending_beat=-1, button_unresolved=False, chopped_beat=-1, best_title=1, curiosity=50, unclear_beat=-1, weakest_beat=-1, thread_holds=True)
    beats = [_beat("cold_open", 41), _beat("context", 10, 11), _beat("escalation", 20), _beat("escalation", 50),
             _beat("escalation", 60), _beat("button", 70)]

    def pick(prompt):
        number = next(line.split(".")[0] for line in prompt.splitlines() if line.split(". ")[-1].startswith("S88 "))
        return ButtonPick(reasoning="this one hangs", candidate=int(number))

    pi, model = _setup(
        monkeypatch, {"TrailerDraft": [_draft(beats), _draft(beats)], "SpoilerVerdict": [resolved, SAFE], "ButtonPick": pick}
    )

    _, plan = await plan_trailer(pi)

    index = pi.index
    assert plan["beats"][-1]["ranges"][0][0] == round(index.cut_in[88], 3)
    assert model.schemas().count("SpoilerVerdict") == 2 and "ButtonPick" in model.schemas()


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
    assert "#31 " in listing and "#110 " not in listing  # the arc's ending is cut, not marked
    assert "#99 " in listing  # the setup right before the payoff stays usable


async def test_coarse_transcripts_keep_their_lines(monkeypatch):
    pi, _ = _setup(monkeypatch, {})
    clauses = await clause_units(pi)
    assert clauses is not pi.index and clauses.granularity == "clause"
    pi.index.coarse = True
    assert await clause_units(pi) is pi.index


@pytest.mark.asyncio
async def test_the_draft_knows_what_to_hide_and_a_telling_title_is_replaced(monkeypatch):
    telling = SpoilerVerdict(
        reasoning="the title says they sold", reveals_payoff=False, offending_beat=-1, button_unresolved=True,
        chopped_beat=-1, best_title=-1, curiosity=70, unclear_beat=-1, weakest_beat=-1, thread_holds=True,
    )
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [telling]})

    segments, plan = await plan_trailer(pi)

    draft_call = model.calls[0]
    assert "<story>" in draft_call.prompt and "- They sold for one dollar" in draft_call.prompt
    assert "#100 " not in draft_call.prompt.split("<story>")[1].split("</story>")[0]
    judge_call = next(call for call in model.calls if call.schema == "SpoilerVerdict")
    assert "1. The deal that could end everything" in judge_call.prompt
    assert segments[0]["hook_title"] == "Will the company survive?" and plan["title"] == "Will the company survive?"


def _unfit(title):
    # Three beats too short for the window, so the outline fallback plays instead.
    draft = _draft([_beat("cold_open", 40), _beat("button", 75), _beat("button", 90)])
    draft.title_candidates = [title]
    return draft


@pytest.mark.asyncio
async def test_the_title_of_a_draft_that_does_not_fit_is_still_judged(monkeypatch):
    telling = SpoilerVerdict(
        reasoning="the title names the sale", reveals_payoff=False, offending_beat=-1, button_unresolved=True,
        chopped_beat=-1, best_title=-1, curiosity=50, unclear_beat=-1, weakest_beat=-1, thread_holds=True,
    )
    draft = _unfit("Why sell a company for one dollar")
    pi, model = _setup(monkeypatch, {"TrailerDraft": [draft, draft, draft], "SpoilerVerdict": [telling]})

    segments, plan = await plan_trailer(pi)

    assert model.schemas()[:3] == ["TrailerDraft", "TrailerDraft", "TrailerDraft"]
    assert segments[0]["hook_title"] == plan["title"] == "Will the company survive?"


@pytest.mark.asyncio
async def test_a_cleared_title_survives_the_fallback(monkeypatch):
    draft = _unfit("One deal could end everything")
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [draft, draft], "SpoilerVerdict": [SAFE]})

    segments, _ = await plan_trailer(pi)

    assert segments[0]["hook_title"] == "One deal could end everything"  # the fallback's beats still say it


@pytest.mark.asyncio
async def test_an_unjudged_title_is_never_burned_in(monkeypatch):
    # The judge is down: the fallback still ships, under the central question.
    draft = _unfit("How They Sold For One Dollar")
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [draft, draft], "SpoilerVerdict": [RuntimeError("down")]})

    segments, _ = await plan_trailer(pi)

    assert segments[0]["hook_title"] == "Will the company survive?"
    # Same for a drafted cut the judge could not check.
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [RuntimeError("down")]})
    segments, _ = await plan_trailer(pi)
    assert segments[0]["hook_title"] == "Will the company survive?"


@pytest.mark.asyncio
async def test_a_leaking_fallback_is_rebuilt_without_the_leak_once(monkeypatch):
    leak = SpoilerVerdict(reasoning="", reveals_payoff=True, offending_beat=1, button_unresolved=True, chopped_beat=-1, best_title=1, curiosity=10, unclear_beat=-1, weakest_beat=-1, thread_holds=True)
    draft = _unfit("The deal that could end everything")
    pi, model = _setup(monkeypatch, {"TrailerDraft": [draft, draft], "SpoilerVerdict": [leak, SAFE]})

    _, plan = await plan_trailer(pi)

    index = pi.index
    assert model.schemas().count("SpoilerVerdict") == 2
    starts = [beat["ranges"][0][0] for beat in plan["beats"]]
    assert round(index.cut_in[40], 3) not in starts  # the kept cold open leaked, another line opens

    pi, _ = _setup(monkeypatch, {"TrailerDraft": [draft, draft], "SpoilerVerdict": [leak, leak]})
    with pytest.raises(WorkflowError) as error:
        await plan_trailer(pi)
    assert error.value.code == "trailer_unbuildable"


def test_draft_rules_reject_chopped_beats_and_middles_out_of_order(monkeypatch):
    from src.workflows.trailer import _DraftRules

    words, cursor = [], 0
    for sentence in ["We had one plan for the launch, and it failed badly in the end."] * 40:
        for token in sentence.split():
            words.append({"text": token, "start": cursor, "end": cursor + 300, "speaker": "A"})
            cursor += 300 + (600 if token.endswith(",") else 50)
        cursor += 500
    clauses = build_units({"words": words, "language_code": "en"}, "", granularity="clause")
    # Clause 2 of each sentence ("and it failed...") starts after a comma pause; clause 1 ends on the comma.
    assert not clauses.unit(2).starts_sentence and clauses.unit(1).end_kind in ("clause", "pause")
    rules = _DraftRules(clauses, set(), None, trailer_budget("auto"), {}, story=True)
    draft = TrailerDraft(
        premise="p", thread="t", payoffs=[], reclaimed=[], stranger_read="", title_candidates=["a", "b", "c"],
        beats=[_beat("cold_open", 3, 4), _beat("context", 11), _beat("escalation", 21, 22), _beat("escalation", 13, 14),
               _beat("button", 31)],
    )
    issues = rules(draft)
    assert any(issue.startswith("Beat 2 stops mid-phrase") for issue in issues)
    assert any("play in source order" in issue for issue in issues)
    assert not any(issue.startswith("Beat 1 ") and "mid" in issue for issue in issues)
    # The button may stop on a complete hanging clause.
    assert not any("The button ends on" in issue for issue in issues)


def test_fit_keeps_middle_beats_in_source_order_and_merges_neighbours():
    from src.workflows.assembly import fit_trailer

    clauses = build_units(story_data(60, questions=(50,)), "", granularity="clause")
    beats = [TrailerBeatIn("cold_open", 40, 40), TrailerBeatIn("escalation", 22, 22), TrailerBeatIn("context", 21, 21),
             TrailerBeatIn("escalation", 30, 30), TrailerBeatIn("button", 50, 50)]
    cut = fit_trailer(clauses, beats, set(), trailer_budget("auto"))
    middle = [(beat.first, beat.last) for beat in cut.beats[1:-1]]
    assert middle == sorted(middle) and (21, 22) in middle


def test_button_candidates_are_safe_questions_and_setup_lines(monkeypatch):
    from src.workflows.trailer import button_candidates, setup_sentences

    pi, _ = _setup(monkeypatch, {})
    clauses = pi.index
    spoilers = spoiler_sentences(pi)
    setups = setup_sentences(pi, spoilers)
    assert setups == {99}
    found = button_candidates(pi, clauses, spoilers, setups, None)
    assert found and len(found) <= 12
    assert found[0][1] == 99  # the setup line ranks first
    assert all(not any(uid in spoilers for uid in range(a, b + 1)) for a, b in found)
    assert any(b == 99 for _, b in found) and any(b == 75 for _, b in found)


def test_trailer_junctions_are_plain_cuts_the_renderer_concatenates():
    # A sub-two-frame "declick" xfade lost frames to fps rounding and froze the
    # picture for the last beats (pr163); plain cuts take the concat path.
    from src.media.ffmpeg import junction_fades_for_offsets
    from src.media.render_ranges import output_offsets_for_junctions

    assert TRAILER_JUNCTION_FADE == 0.0
    keep = [(100.0, 103.27), (10.0, 14.9), (50.0, 52.03), (200.0, 207.5)]
    offsets = output_offsets_for_junctions(keep, [0, 1, 2, 3], [TRAILER_JUNCTION_FADE] * 3)
    assert not any(junction_fades_for_offsets(keep, offsets))


@pytest.mark.asyncio
async def test_the_outline_fallback_ends_on_a_strong_line_when_the_video_asks_no_question(monkeypatch):
    from src.workflows.trailer import fallback_trailer

    data = story_data(120)
    outline = make_outline(
        chapters=[chapter(120, "story")], format="interview",
        points=[point(30, 32, core=(31, 31), importance=5), point(55, 57, core=(56, 56), importance=4),
                point(65, 66, core=(65, 65), importance=3), point(80, 82, core=(81, 81), importance=3)],
        payoffs=[OutlinePayoff(first_unit=100, last_unit=101, what="They sold for one dollar")],
    )
    ScriptedModel({}).install(monkeypatch)
    pi = plan_input(plan_context("trailer", data), outline)
    clauses = await clause_units(pi)
    spoilers = spoiler_sentences(pi)
    cut = fallback_trailer(pi, clauses, spoilers, trailer_budget("auto"))
    assert cut.usable and cut.beats[-1].role == "button"
    assert not any(uid in spoilers for beat in cut.beats for uid in range(beat.first, beat.last + 1))


# -- round 2 --------------------------------------------------------------------------------------


def _verdict(**changes):
    return SAFE.model_copy(update=changes)


def test_reclaimed_setups_are_free_outside_stories_and_recorded(monkeypatch):
    from src.workflows.trailer import _DraftRules

    clauses = build_units(story_data(60), "", granularity="clause")
    rules = _DraftRules(clauses, {40, 41, 42}, None, trailer_budget("auto"), {})
    draft = _draft()
    draft.reclaimed = [TrailerReclaimed(first_unit=40, last_unit=40, why="only asks"), TrailerReclaimed(first_unit=50, last_unit=50, why="not off limits")]
    assert rules.reclaimed(draft) == {40}
    assert rules.blocked(draft) == {41, 42}
    # A story keeps its ending strictly hidden.
    story = _DraftRules(clauses, {40, 41, 42}, None, trailer_budget("auto"), {}, story=True)
    assert story.reclaimed(draft) == set() and 40 in story.blocked(draft)


@pytest.mark.asyncio
async def test_a_reclaimed_setup_plays_and_the_plan_says_so(monkeypatch):
    outline = make_outline(
        chapters=[chapter(120, "story")], format="interview",
        payoffs=[OutlinePayoff(first_unit=60, last_unit=60, what="It costs one dollar")],
    )
    draft = _draft([_beat("cold_open", 40), _beat("context", 10, 11), _beat("escalation", 20), _beat("escalation", 50),
                    _beat("escalation", 60), _beat("button", 75)])
    draft.reclaimed = [TrailerReclaimed(first_unit=60, last_unit=60, why="only raises the question")]
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [draft], "SpoilerVerdict": [SAFE]}, outline=outline)
    segments, plan = await plan_trailer(pi)
    assert plan["reclaimed"] == [60]
    assert round(pi.index.cut_in[60], 3) in [start for start, _ in segments[0]["source_ranges"]]


def test_draft_rules_ask_a_stranger_and_for_rhythm(monkeypatch):
    from src.workflows.trailer import _DraftRules

    texts = {10: "And then the bank called back the same day", 20: "That prediction takes the form of a number", 30: "But nobody ever asked us about the price"}
    clauses = build_units(story_data(60, texts=texts, questions=(50,)), "", granularity="clause")
    rules = _DraftRules(clauses, set(), None, trailer_budget("auto"), {})
    draft = _draft([_beat("cold_open", 30), _beat("escalation", 20), _beat("escalation", 40), _beat("escalation", 44), _beat("button", 50)])
    issues = rules(draft)
    assert any('The cold open opens on "but"' in issue for issue in issues)
    assert any(issue.startswith('Beat 2 opens on "That prediction') for issue in issues)
    assert any("Every beat is about the same length" in issue for issue in issues)
    # A beat that continues the one before it may lean on it.
    draft = _draft([_beat("cold_open", 25), _beat("escalation", 19), _beat("escalation", 20), _beat("escalation", 44, 45), _beat("button", 50)])
    assert not any(issue.startswith("Beat 3 opens on") for issue in rules(draft))
    assert not any("same length" in issue for issue in rules(draft))
    # A short hit is allowed from the second beat on.
    assert not any("must be" in issue and issue.startswith("Beat 2") for issue in rules(draft))


def test_draft_rules_see_what_the_fitter_will_do(monkeypatch):
    from src.workflows import trailer as trailer_module
    from src.workflows.assembly import TrailerCut
    from src.workflows.trailer import _DraftRules

    clauses = build_units(story_data(120, questions=QUESTIONS, texts=TEXTS), "", granularity="clause")
    rules = _DraftRules(clauses, set(), None, trailer_budget("auto"), {})
    assert rules(_draft()) == []
    monkeypatch.setattr(trailer_module, "fit_trailer", lambda *args, **kwargs: TrailerCut([], 14.0, trailer_budget("auto")))
    assert any(issue.startswith("After fitting, the trailer has 0 beats and 14s") for issue in rules(_draft()))


@pytest.mark.asyncio
async def test_a_draft_that_can_not_fit_gets_one_more_call_before_the_fallback(monkeypatch):
    good = _draft()
    draft = _unfit("One deal could end everything")
    pi, model = _setup(monkeypatch, {"TrailerDraft": [draft, draft, good], "SpoilerVerdict": [SAFE]})

    _, plan = await plan_trailer(pi)

    assert model.schemas() == ["TrailerDraft", "TrailerDraft", "TrailerDraft", "SpoilerVerdict"]
    rescue = model.calls[2].prompt
    assert "These beats survived:" in rescue and "After fitting, the trailer is" in rescue
    assert pi.stats.fallbacks == []
    assert len(plan["beats"]) >= 4


@pytest.mark.asyncio
async def test_a_viewer_who_is_not_hooked_earns_one_guided_redraft_and_never_a_worse_cut(monkeypatch):
    bored = _verdict(curiosity=40, weakest_beat=3, thread_holds=False, reasoning="the middle wanders")
    better = _draft([_beat("cold_open", 60), _beat("context", 10, 11), _beat("escalation", 20), _beat("escalation", 50),
                     _beat("escalation", 65), _beat("button", 75)])
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft(), better], "SpoilerVerdict": [bored, _verdict(curiosity=75)]})

    _, plan = await plan_trailer(pi)

    index = pi.index
    assert plan["beats"][0]["ranges"][0][0] == round(index.cut_in[60], 3)
    guided = [call for call in model.calls if call.schema == "TrailerDraft"][1].prompt
    assert "the middle wanders" in guided and "Replace beat 3." in guided

    worse = better.model_copy(deep=True)
    pi, _ = _setup(monkeypatch, {"TrailerDraft": [_draft(), worse], "SpoilerVerdict": [bored, _verdict(curiosity=30)]})
    _, plan = await plan_trailer(pi)
    assert plan["beats"][0]["ranges"][0][0] == round(pi.index.cut_in[40], 3)


@pytest.mark.asyncio
async def test_a_title_must_be_heard_in_the_final_beats(monkeypatch):
    draft = _draft()
    draft.title_candidates = ["Billions of Knobs Nobody Ever Touched", "One deal could end everything"]
    judge_prompts = []

    def judge(prompt):
        judge_prompts.append(prompt)
        return SAFE

    pi, model = _setup(monkeypatch, {"TrailerDraft": [draft], "SpoilerVerdict": judge})
    segments, _ = await plan_trailer(pi)
    # The unheard one never reaches the judge.
    assert "Billions" not in judge_prompts[0] and segments[0]["hook_title"] == "One deal could end everything"

    # Every candidate unheard: a small call writes one for the beats that play.
    draft = _draft()
    draft.title_candidates = ["Billions of Knobs Nobody Ever Touched"]
    pi, model = _setup(
        monkeypatch,
        {"TrailerDraft": [draft], "SpoilerVerdict": [_verdict(best_title=-1)],
         "TrailerTitles": [TrailerTitles(titles=["Nothing heard here today", "One deal could end everything", "x"])]},
    )
    segments, _ = await plan_trailer(pi)
    assert segments[0]["hook_title"] == "One deal could end everything"
    assert "TrailerTitles" in model.schemas()


@pytest.mark.asyncio
async def test_an_unclear_beat_goes_like_a_chopped_one(monkeypatch):
    unclear = _verdict(unclear_beat=3)
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [SAFE]})
    first, _ = await plan_trailer(pi)
    third = first[0]["source_ranges"][2][0]
    pi, model = _setup(monkeypatch, {"TrailerDraft": [_draft()], "SpoilerVerdict": [unclear, SAFE]})
    segments, _ = await plan_trailer(pi)
    assert third not in [start for start, _ in segments[0]["source_ranges"]]
    assert model.schemas().count("SpoilerVerdict") == 2
    judge = next(call for call in model.calls if call.schema == "SpoilerVerdict").prompt
    assert "The question this trailer sells (its thread): Will this deal end the company?" in judge


def test_button_candidates_come_from_across_the_whole_video(monkeypatch):
    from src.workflows.trailer import button_candidates

    questions = tuple(range(5, 115, 5))
    data = story_data(120, questions=questions, speakers=("A",))
    outline = make_outline(chapters=[chapter(120, "story")], format="interview")
    ScriptedModel({}).install(monkeypatch)
    pi = plan_input(plan_context("trailer", data), outline)
    found = button_candidates(pi, pi.index, set(), set(), None)
    assert len(found) == 12
    lasts = [last for _, last in found]
    assert min(lasts) <= 15 and max(lasts) >= 95  # not the first twelve
    assert len(button_candidates(pi, pi.index, set(), set(), None, limit=20)) == 20


def test_definition_shaped_points_go_last_in_the_fallback():
    from src.workflows.trailer import definition_shaped

    assert definition_shaped("A token is a piece of text the model reads.", "en")
    assert definition_shaped("Ein Token ist ein Stück Text.", "de")
    assert not definition_shaped("Nobody expected this to work at all.", "en")
    assert not definition_shaped("A token is a piece of text.", "hi")
