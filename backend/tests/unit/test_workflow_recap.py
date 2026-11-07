import pytest

from src.workflows.assembly import RECAP_JUNCTION_FADE
from src.workflows.durations import recap_budget
from src.workflows.errors import WorkflowError
from src.workflows.recap import _DraftRules, plan_recap
from src.workflows.schemas import RecapDraft, RecapFinalBeat, RecapFinalCut, RecapPoint, UnitSpan

from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, plan_input, point, story_data

NONE = UnitSpan(first_unit=-1, last_unit=-1)


def _final(beats=(), *, verdict="fix", title="", why=""):
    return RecapFinalCut(
        viewer_notes="flows", verdict=verdict,
        beats=[RecapFinalBeat(first_unit=a, last_unit=b, label=label) for a, b, label in beats],
        title=title, why=why,
    )


def _same_cut(prompt):
    """The cut it was shown, unchanged: every PLAYS block read back."""
    beats = []
    for block in prompt.split("  PLAYS:\n")[1:]:
        lines = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped.startswith("#"):
                break
            lines.append(int(stripped.split()[0][1:]))
        beats.append((lines[0], lines[-1], "beat"))
    return _final(beats, verdict="ship", title="", why="")


def _point(first, last, core, importance=1, label="A point"):
    return RecapPoint(
        label=label, subject="", first_unit=first, last_unit=last, core_first=core[0], core_last=core[1],
        setup_first=-1, setup_last=-1, importance=importance,
    )


def _draft(points=None, opener=(2, 2), closer=(110, 111)):
    return RecapDraft(
        editorial_notes="", spine="", title="How the company was saved",
        opener=UnitSpan(first_unit=opener[0], last_unit=opener[1]) if opener else NONE,
        opener_answered_by=0,
        points=points if points is not None else [
            _point(10, 12, (11, 11), 1, "Pricing changed"), _point(30, 32, (31, 31), 1, "The bank called"),
            _point(50, 52, (51, 51), 2, "Hiring froze"), _point(70, 72, (71, 71), 3, "A side story"),
            _point(90, 92, (91, 91), 2, "The rescue"),
        ],
        closer=UnitSpan(first_unit=closer[0], last_unit=closer[1]) if closer else NONE,
    )


# What the beats say, so the drafted titles are heard in them.
TEXTS = {10: "How the company was saved by the pricing change", 11: "One call saved the company from the pricing mess we made back then"}


def _setup(monkeypatch, answers, *, mode="full", outline_points=None, sentences=120, **ctx):
    data = story_data(sentences, texts=TEXTS)
    outline = make_outline(
        chapters=[chapter(40, importance=5), chapter(80, importance=2), chapter(sentences, importance=4)],
        points=outline_points or [point(10, 12, statement="Pricing changed everything"), point(100, 102, statement="Missing point")],
    )
    model = ScriptedModel(answers).install(monkeypatch)
    return plan_input(plan_context("recap", data, **ctx), outline, mode=mode), model


@pytest.mark.asyncio
async def test_recap_is_one_locked_segment_inside_its_window(monkeypatch):
    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": _same_cut})

    segments, plan = await plan_recap(pi)

    assert model.schemas() == ["RecapDraft", "RecapFinalCut"]
    assert len(segments) == 1
    segment = segments[0]
    budget = recap_budget("auto", pi.ctx.source_seconds)
    assert budget.min <= plan["seconds"] <= budget.max
    assert segment["planned"] and segment["required"] and segment["workflow_role"] == "recap"
    assert segment["max_seconds"] == budget.render_cap
    ranges = segment["source_ranges"]
    assert segment["junction_fades"] == [RECAP_JUNCTION_FADE] * (len(ranges) - 1)
    points = segment["workflow_meta"]["points"]
    assert points[0] == {"label": "Intro", "kind": "opener", "range_index": 0, "droppable": False, "output_start": None}
    assert points[-1]["kind"] == "closer" and points[-1]["label"] == "Takeaway"
    assert [p["range_index"] for p in points] == list(range(len(ranges)))
    assert any(p["label"] == "Pricing changed" and p["kind"] == "point" for p in points)
    assert [p["ranges"][0] for p in plan["points"]] == ranges
    assert segment["hook_title"] == "How the company was saved" and plan["title"] == "How the company was saved"
    assert " … " in segment["text"]
    assert [message for _, message in pi.ctx.messages] == [
        "Mapping the main points...", "Choosing what to keep...", "Checking the edit...",
    ]


@pytest.mark.asyncio
async def test_draft_prompt_frames_the_budget_and_the_central_question(monkeypatch):
    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": _same_cut}, instructions="Keep it upbeat")
    await plan_recap(pi)

    call = model.calls[0]
    budget = recap_budget("auto", pi.ctx.source_seconds)
    assert f"ONE short video of {budget.min:.0f}-{budget.max:.0f}s" in call.system
    assert '"Will the company survive?"' in call.system
    assert "<outline>" in call.prompt and "#120 " in call.prompt
    assert "Keep it upbeat" in call.prompt


def test_draft_rules_name_every_problem(monkeypatch):
    pi, _ = _setup(monkeypatch, {})
    rules = _DraftRules(pi, None)
    draft = _draft(
        points=[
            _point(20, 18, (18, 19)),
            _point(30, 40, (31, 31)),
            _point(31, 33, (36, 36)),
            _point(10, 12, (11, 11)),
            _point(50, 51, (51, 51), label="x"),
        ],
        opener=(1, 4),
        closer=(51, 51),
    )
    issues = rules(draft)

    assert "Point 1 has a reversed range; write first..last in video order." in issues
    assert any(issue.startswith("Point 2 is ") and "6-25s" in issue for issue in issues)
    assert "Point 3: the core #36–#36 must lie inside #31–#33." in issues
    assert "Points 2 and 3 overlap; each unit belongs to one point." in issues
    assert "Points must be in video order: point 4 (#10) comes before point 3 (#31)." in issues
    assert any(issue.startswith("The opener is ") for issue in issues)
    assert not any(issue.startswith("The closer overlaps") for issue in issues)  # it is exactly point 5's core
    assert "The opener overlaps" not in " ".join(issues)
    shifted = _draft(closer=(52, 53))
    shifted.points[2] = _point(50, 53, (51, 51), 2)
    assert "The closer overlaps point 3; choose a line outside the points, or exactly that point's core." in rules(shifted)
    assert rules(_draft()) == []


def test_draft_rules_only_accept_ids_that_were_shown(monkeypatch):
    pi, _ = _setup(monkeypatch, {})
    rules = _DraftRules(pi, list(range(1, 60)))
    issues = rules(_draft())
    assert "Point 4 uses #70, which is not in the transcript you were given." in issues
    assert any("closer #110–#111 is not a valid range" in issue for issue in issues)


@pytest.mark.asyncio
async def test_a_short_draft_is_repaired_once(monkeypatch):
    thin = _draft(points=[_point(10, 12, (11, 11))])
    pi, model = _setup(monkeypatch, {"RecapDraft": [thin, _draft()], "RecapFinalCut": _same_cut})

    await plan_recap(pi)

    assert model.schemas() == ["RecapDraft", "RecapDraft", "RecapFinalCut"]
    assert "You gave 1 points; give at least 3." in model.calls[1].prompt


@pytest.mark.asyncio
async def test_the_final_cut_plays_exactly_as_returned_and_titles_what_plays(monkeypatch):
    seen = {}

    def final(prompt):
        seen["prompt"] = prompt
        return _final(
            [(10, 12, "Pricing changed"), (30, 32, "The bank called"), (90, 92, "The rescue")],
            title="One call saved the company", why="Pricing nearly ends it, the bank calls, and the rescue lands.",
        )

    # The verify round reads the new cut with the same eyes and ships it.
    answers = [final, _same_cut]
    pi, model = _setup(
        monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": lambda prompt: answers.pop(0)(prompt)},
        advanced={"coverage": "best_moments"},
    )

    segments, plan = await plan_recap(pi)

    assert "  before (does not play):" in seen["prompt"] and "  PLAYS:" in seen["prompt"]
    assert "Total: " in seen["prompt"] and "Unused candidates:" in seen["prompt"]
    assert [p["label"] for p in plan["points"]] == ["Pricing changed", "The bank called", "The rescue"]
    index = pi.index
    assert segments[0]["source_ranges"][0] == [round(index.cut_in[10], 3), round(index.cut_out[12], 3)]
    assert plan["title"] == "One call saved the company"
    assert segments[0]["reasoning"] == "Pricing nearly ends it, the bank calls, and the rescue lands."
    assert pi.stats.critique_fixes == 1
    assert model.schemas().count("RecapFinalCut") == 2


@pytest.mark.asyncio
async def test_a_final_cut_outside_the_window_or_on_a_sponsor_read_is_asked_again_then_ignored(monkeypatch):
    too_long = _final([(10, 60, "Everything"), (70, 110, "The rest")], title="t")
    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": [too_long, too_long]})

    _, plan = await plan_recap(pi)

    repair = model.calls[-1].prompt
    assert "The cut totals" in repair
    assert model.schemas().count("RecapFinalCut") == 2
    budget = recap_budget("auto", pi.ctx.source_seconds)
    assert budget.min <= plan["seconds"] <= budget.max  # the assembled cut stays
    assert pi.stats.critique_fixes == 0


@pytest.mark.asyncio
async def test_a_mid_recap_beat_labelled_intro_is_asked_for_its_own_name(monkeypatch):
    def relabelled(prompt):
        cut = _same_cut(prompt)
        cut.beats[1].label = "Intro"
        return cut

    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": relabelled})

    _, plan = await plan_recap(pi)

    assert "needs a label of its own" in model.calls[-1].prompt
    # Never burned in as the name of a point.
    assert not [point for point in plan["points"] if point["kind"] == "point" and point["label"] == "Intro"]


@pytest.mark.asyncio
async def test_a_failed_final_cut_keeps_the_assembled_recap_and_a_title_that_plays(monkeypatch):
    pi, _ = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": [RuntimeError("down")]})
    segments, plan = await plan_recap(pi)
    assert plan["seconds"] > 0 and segments[0]["hook_title"] == "How the company was saved"


@pytest.mark.asyncio
async def test_the_final_cut_sees_a_missing_main_point_as_an_unused_candidate(monkeypatch):
    seen = {}

    def final(prompt):
        seen["prompt"] = prompt
        return _same_cut(prompt)

    outline_points = [
        point(10, 12, statement="Pricing changed everything"), point(100, 102, statement="Missing point"),
        point(60, 64, importance=2, statement="The quiet turn"),
    ]
    pi, _ = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": final}, outline_points=outline_points)
    await plan_recap(pi)
    assert "P3 ★2 The quiet turn" in seen["prompt"]


@pytest.mark.asyncio
async def test_no_usable_point_is_recap_unsupported(monkeypatch):
    empty = _draft(points=[_point(-1, -1, (-1, -1))], opener=None, closer=None)
    pi, _ = _setup(monkeypatch, {"RecapDraft": [empty, empty]}, outline_points=[point(200, 201)])
    pi.outline.outline.main_points.clear()

    with pytest.raises(WorkflowError) as error:
        await plan_recap(pi)
    assert error.value.code == "recap_unsupported"


@pytest.mark.asyncio
async def test_long_sources_show_the_draft_the_outlines_best_material(monkeypatch):
    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft(), _draft()], "RecapFinalCut": _same_cut}, mode="long")
    await plan_recap(pi)

    listing = model.calls[0].prompt.split("<transcript>")[1]
    assert "not shown" in listing
    assert "#11 " in listing and "#101 " in listing  # outline points ±2
    assert "#1 " in listing and "#3 " in listing and "#5 " not in listing  # first 3 units of the ★5 chapter


@pytest.mark.asyncio
async def test_a_failed_draft_is_plan_failed(monkeypatch):
    pi, _ = _setup(monkeypatch, {"RecapDraft": [RuntimeError("down")]})
    with pytest.raises(WorkflowError) as error:
        await plan_recap(pi)
    assert error.value.code == "plan_failed"


@pytest.mark.asyncio
async def test_a_short_source_target_is_noted(monkeypatch):
    pi, _ = _setup(
        monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": _same_cut}, source_seconds=190.0, clip_length="long"
    )
    segments, plan = await plan_recap(pi)
    assert {"code": "TARGET_SHORTENED", "params": {}} in plan["notes"]
    assert len(segments) == 1


@pytest.mark.asyncio
async def test_an_undershoot_on_a_long_source_never_claims_the_video_is_short(monkeypatch):
    # 200 s at "long" is not a short source (the 120 s target is not cut); the draft is too thin to fill it.
    pi, _ = _setup(
        monkeypatch, {"RecapDraft": [_draft(), _draft()], "RecapFinalCut": _same_cut}, source_seconds=200.0, clip_length="long"
    )
    segments, plan = await plan_recap(pi)
    codes = [note["code"] for note in plan["notes"]]
    assert "TARGET_SHORTENED" not in codes
    assert "RECAP_UNDER_WINDOW" in codes


def test_points_that_start_mid_sentence_snap_to_the_sentence():
    from src.workflows.recap import _draft_in
    from src.workflows.units import build_units

    words, cursor = [], 0
    for text in ("Alpha beta gamma delta.", "One of the classic examples", "is how vectors add up.", "Then we move on now."):
        for token in text.split():
            words.append({"text": token, "start": cursor, "end": cursor + 300, "speaker": "A"})
            cursor += 350
        cursor += 2600 if text == "One of the classic examples" else 400
    index = build_units({"words": words, "language_code": "en"}, "")
    assert not index.unit(3).starts_sentence

    draft = _draft(points=[_point(3, 3, (3, 3))], opener=None, closer=None)
    converted = _draft_in(index, draft)

    assert (converted.points[0].first, converted.points[0].last) == (2, 3)
    assert (converted.points[0].core_first, converted.points[0].core_last) == (2, 3)


@pytest.mark.asyncio
async def test_a_recap_without_a_drafted_title_still_has_one(monkeypatch):
    draft = _draft()
    draft.title = "  "
    pi, _ = _setup(monkeypatch, {"RecapDraft": [draft], "RecapFinalCut": _same_cut})

    segments, plan = await plan_recap(pi)

    # Nothing drafted to promise: the title is made from what plays.
    assert segments[0]["hook_title"] == plan["title"]
    assert plan["title"] == "Pricing changed"


@pytest.mark.asyncio
async def test_sponsor_reads_never_make_the_recap(monkeypatch):
    data = story_data(120)
    outline = make_outline(
        chapters=[chapter(40, importance=5), chapter(49, "sponsor", 1), chapter(80, importance=3), chapter(120, importance=4)],
        points=[point(10, 12, statement="Pricing changed everything")],
    )
    draft = _draft(points=[
        _point(10, 12, (11, 11), 1, "Pricing changed"), _point(44, 46, (45, 45), 1, "Our sponsor"),
        _point(70, 72, (71, 71), 2, "Hiring froze"), _point(90, 92, (91, 91), 2, "The rescue"),
    ])
    model = ScriptedModel({"RecapDraft": [draft, draft], "RecapFinalCut": _same_cut}).install(monkeypatch)
    pi = plan_input(plan_context("recap", data), outline)

    segments, _ = await plan_recap(pi)

    assert "Point 2 (#44–#46) is in a sponsor chapter" in model.calls[1].prompt
    index = pi.index
    sponsor = (index.unit(41).start, index.unit(49).end)
    assert all(end <= sponsor[0] or start >= sponsor[1] for start, end in segments[0]["source_ranges"])
    assert "Our sponsor" not in [p["label"] for p in segments[0]["workflow_meta"]["points"]]


def _rules_with_texts(monkeypatch, texts, *, chapters=None):
    data = story_data(120, texts=texts)
    outline = make_outline(
        chapters=chapters or [chapter(40, importance=4), chapter(80, importance=2), chapter(120, importance=4)],
        points=[point(10, 12)],
    )
    ScriptedModel({}).install(monkeypatch)
    pi = plan_input(plan_context("recap", data), outline)
    return _DraftRules(pi, None, recap_budget("auto", pi.ctx.source_seconds)), pi


def test_draft_rules_reject_signpost_openers_and_counts_the_points_dont_keep(monkeypatch):
    rules, _ = _rules_with_texts(monkeypatch, {
        2: "What I want to do with this video is walk you through the seven biggest habits of artists",
        3: "Here are the seven biggest habits that separate the best artists from everyone else",
    })
    issues = rules(_draft(opener=(2, 2)))
    assert any("talks about the video instead of its content" in issue for issue in issues)
    issues = rules(_draft(opener=(3, 3)))
    assert any("announces 7 items but you give 5 points" in issue for issue in issues)


def test_draft_rules_require_a_core_to_finish_the_list_it_announces(monkeypatch):
    rules, _ = _rules_with_texts(monkeypatch, {
        31: "And so there are 2 consequences of drinking coffee late at night",
        32: "The first is that you sleep less deeply than you think",
    })
    points = [_point(10, 12, (11, 11), 1), _point(30, 33, (31, 32), 1), _point(90, 92, (91, 91), 2)]
    assert any("announces a list at #31" in issue for issue in rules(_draft(points=points)))
    points[1] = _point(30, 33, (30, 30), 1)
    assert not any("announces a list" in issue for issue in rules(_draft(points=points)))


def test_draft_rules_ask_for_every_star5_chapter_and_enough_material(monkeypatch):
    rules, _ = _rules_with_texts(
        monkeypatch, {}, chapters=[chapter(40, importance=4), chapter(80, importance=5), chapter(120, importance=4)]
    )
    thin = _draft(points=[_point(10, 11, (10, 10), 1), _point(90, 91, (90, 90), 1), _point(100, 101, (100, 100), 2)],
                  opener=None, closer=None)
    issues = rules(thin)
    assert any(issue.startswith("Chapter C2 (★5,") and "has no point" in issue for issue in issues)
    assert any("the recap must be at least" in issue for issue in issues)


def test_titles_promise_only_what_plays(monkeypatch):
    from src.workflows.recap import title_problem

    _, pi = _rules_with_texts(monkeypatch, {5: "I met Netanyahu in Jerusalem yesterday", 11: "Frontex grows again this year"})
    from src.workflows.assembly import fit_recap
    from src.workflows.recap import _draft_in

    cut = fit_recap(pi.index, _draft_in(pi.index, _draft()), recap_budget("auto", pi.ctx.source_seconds), None, None)
    assert title_problem("Seven Habits of Great Artists", cut, pi.index, "en") is not None
    assert title_problem("Why Frontex grows", cut, pi.index, "en") is None
    assert title_problem("Why Netanyahu came back", cut, pi.index, "en") == "names Netanyahu, which doesn't play"


def test_points_stop_before_a_line_they_repeat():
    from src.workflows.recap import _without_repeats
    from src.workflows.units import build_units

    words, cursor = [], 0
    for text in ("That's why you rock pumps.", "Get out of my face.", "That's why you rock pumps.", "Again now."):
        for token in text.split():
            words.append({"text": token, "start": cursor, "end": cursor + 300, "speaker": "A"})
            cursor += 350
        cursor += 400
    index = build_units({"words": words, "language_code": "en"}, "")
    assert _without_repeats(index, 1, 4) == 2


def test_a_bulletin_recap_gives_every_story_a_point(monkeypatch):
    data = story_data(120)
    outline = make_outline(
        chapters=[chapter(40, importance=4), chapter(80, importance=3), chapter(120, importance=3)],
        points=[point(10, 12)], format="news",
    )
    ScriptedModel({}).install(monkeypatch)
    pi = plan_input(plan_context("recap", data), outline)
    rules = _DraftRules(pi, None, recap_budget("auto", pi.ctx.source_seconds))
    draft = _draft(points=[_point(10, 12, (11, 11), 1), _point(20, 22, (21, 21), 1), _point(30, 32, (31, 31), 2)])
    issues = rules(draft)
    assert any(issue.startswith("Chapter C2 is a story of this bulletin") for issue in issues)
    assert any(issue.startswith("Chapter C3 is a story of this bulletin") for issue in issues)


# -- round 2 --------------------------------------------------------------------------------------


def _with(point_, **changes):
    return point_.model_copy(update=changes)


def test_a_point_says_its_subject_out_loud_or_is_told_where_it_is_said(monkeypatch):
    rules, _ = _rules_with_texts(monkeypatch, {
        29: "Some people call it the night shift around here",
        31: "And one of those cuts your pay by a third every month",
    })
    base = _point(30, 33, (31, 32), 1)
    leaning = _with(base, subject="night shift")
    draft = _draft(points=[_point(10, 12, (11, 11), 1), leaning, _point(90, 92, (91, 91), 2)])
    assert any('is about "night shift" but those words never play. #29 says them' in issue for issue in rules(draft))
    fixed = _with(leaning, setup_first=29, setup_last=29)
    draft = _draft(points=[_point(10, 12, (11, 11), 1), fixed, _point(90, 92, (91, 91), 2)])
    assert not any("never play" in issue for issue in rules(draft))
    # A setup is short, before the core, and close by.
    late = _with(leaning, setup_first=31, setup_last=31)
    draft = _draft(points=[_point(10, 12, (11, 11), 1), late, _point(90, 92, (91, 91), 2)])
    assert any("setup must come before its core" in issue for issue in rules(draft))
    long = _with(leaning, setup_first=26, setup_last=29)
    draft = _draft(points=[_point(10, 12, (11, 11), 1), long, _point(90, 92, (91, 91), 2)])
    assert any("setup is" in issue and "at most 8s" in issue for issue in rules(draft))


@pytest.mark.asyncio
async def test_a_setup_is_its_own_range_right_before_the_core(monkeypatch):
    draft = _draft(points=[
        _point(10, 12, (11, 11), 1, "Pricing changed"),
        _with(_point(30, 32, (31, 31), 1, "The bank called"), setup_first=25, setup_last=25),
        _point(90, 92, (91, 91), 2, "The rescue"),
    ], opener=None, closer=None)
    pi, _ = _setup(monkeypatch, {"RecapDraft": [draft], "RecapFinalCut": [RuntimeError("down")]})

    segments, plan = await plan_recap(pi)

    index = pi.index
    ranges = segments[0]["source_ranges"]
    called = next(point for point in plan["points"] if point["label"] == "The bank called")
    assert called["ranges"][0] == [round(index.cut_in[25], 3), round(index.cut_out[25], 3)]
    assert len(called["ranges"]) == 2 and len(segments[0]["junction_fades"]) == len(ranges) - 1
    assert [item["range_index"] for item in segments[0]["workflow_meta"]["points"]] == list(range(len(ranges)))


def test_a_short_echo_never_truncates_a_point_and_a_reply_never_ends_one():
    from src.workflows.recap import _without_repeats, trim_tail
    from src.workflows.units import build_units

    words, cursor = [], 0
    lines = [("A", "It only adds four minutes?"), ("B", "Minutes?"), ("A", "Minutes."), ("A", "And it raises your efficiency by two percent."), ("B", "Amazing.")]
    for speaker, text in lines:
        for token in text.split():
            words.append({"text": token, "start": cursor, "end": cursor + 300, "speaker": speaker})
            cursor += 350
        cursor += 400
    index = build_units({"words": words, "language_code": "en"}, "")
    assert _without_repeats(index, 1, 5) == 5
    assert trim_tail(index, 1, 5) == 4  # the host's "Amazing." goes
    assert trim_tail(index, 1, 4) == 4


def test_a_closer_may_not_talk_about_the_video(monkeypatch):
    rules, _ = _rules_with_texts(monkeypatch, {110: "A lot of the goal with this chapter was to lay foundations"})
    assert any("The closer talks about the video" in issue for issue in rules(_draft()))


@pytest.mark.asyncio
async def test_a_speakers_own_summary_is_material_not_an_outro(monkeypatch):
    from src.workflows.recap import framing_units
    from src.workflows.schemas import UnitSpan

    data = story_data(120, texts=TEXTS)
    summary = UnitSpan(first_unit=112, last_unit=116)
    outline = make_outline(
        chapters=[chapter(40, importance=4), chapter(80, importance=3), chapter(120, "outro", 2)],
        points=[point(10, 12)], self_summaries=[summary],
    )
    model = ScriptedModel({"RecapDraft": [_draft(closer=(112, 116))], "RecapFinalCut": [RuntimeError("down")]}).install(monkeypatch)
    pi = plan_input(plan_context("recap", data), outline)
    framing = framing_units(pi)
    assert 113 not in framing and 118 in framing
    await plan_recap(pi)
    # The closer sits in an outro, and nobody objects.
    assert not any("closer" in call.prompt.split("IT BREAKS THESE RULES")[-1] for call in model.calls[1:] if "IT BREAKS" in call.prompt)
    assert "<self_summary>\n#112–#116:" in model.calls[0].prompt
    assert "The speaker sums the video up in: #112–#116" in model.calls[0].prompt


def test_a_title_must_be_said_in_german_and_hindi_too(monkeypatch):
    from src.workflows.assembly import fit_recap
    from src.workflows.recap import _draft_in, title_problem

    _, pi = _rules_with_texts(monkeypatch, {11: "Frontex wird wieder groesser und Israel waehlt im Fruehjahr"})
    cut = fit_recap(pi.index, _draft_in(pi.index, _draft()), recap_budget("auto", pi.ctx.source_seconds), None, None)
    assert title_problem("Israel wählt, Atommüll folgt", cut, pi.index, "de") is not None
    assert title_problem("Frontex wird wieder groesser", cut, pi.index, "de") is None


@pytest.mark.asyncio
async def test_the_recap_summary_is_its_spine(monkeypatch):
    draft = _draft()
    draft.spine = "Can one call save it? Yes."
    pi, _ = _setup(monkeypatch, {"RecapDraft": [draft], "RecapFinalCut": [RuntimeError("down")]})
    _, plan = await plan_recap(pi)
    assert plan["summary"] == "Can one call save it? Yes."


@pytest.mark.asyncio
async def test_a_final_cut_with_too_many_joins_is_asked_again_and_markup_never_reaches_the_reasoning(monkeypatch):
    many = _final([(uid, uid + 1, f"beat {uid}") for uid in range(3, 60, 5)], title="t")
    tidy = _final([(10, 12, "Pricing changed"), (30, 32, "The bank called"), (90, 92, "The rescue")],
                  title="One call saved the company", why="Pricing, the bank, the rescue.}</br>")
    answers = [many, tidy, _final(verdict="ship")]
    pi, model = _setup(
        monkeypatch, {"RecapDraft": [_draft()], "RecapFinalCut": lambda prompt: answers.pop(0)},
        advanced={"coverage": "best_moments"},
    )
    segments, plan = await plan_recap(pi)
    assert "beats; it can play at most" in [call for call in model.calls if call.schema == "RecapFinalCut"][1].prompt
    assert len(plan["points"]) == 3
    assert segments[0]["reasoning"] == "Pricing, the bank, the rescue."
