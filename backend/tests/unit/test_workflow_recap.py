import pytest

from src.workflows.assembly import RECAP_JUNCTION_FADE
from src.workflows.durations import recap_budget
from src.workflows.errors import WorkflowError
from src.workflows.recap import _DraftRules, plan_recap
from src.workflows.schemas import JunctionFix, RecapCritique, RecapDraft, RecapPoint, UnitSpan

from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, plan_input, point, story_data

NONE = UnitSpan(first_unit=-1, last_unit=-1)
SHIP = RecapCritique(viewer_notes="flows", verdict="ship", fixes=[])


def _point(first, last, core, importance=1, label="A point"):
    return RecapPoint(label=label, first_unit=first, last_unit=last, core_first=core[0], core_last=core[1], importance=importance)


def _draft(points=None, opener=(2, 2), closer=(110, 111)):
    return RecapDraft(
        editorial_notes="", title="How the company was saved",
        opener=UnitSpan(first_unit=opener[0], last_unit=opener[1]) if opener else NONE,
        points=points if points is not None else [
            _point(10, 12, (11, 11), 1, "Pricing changed"), _point(30, 32, (31, 31), 1, "The bank called"),
            _point(50, 52, (51, 51), 2, "Hiring froze"), _point(70, 72, (71, 71), 3, "A side story"),
            _point(90, 92, (91, 91), 2, "The rescue"),
        ],
        closer=UnitSpan(first_unit=closer[0], last_unit=closer[1]) if closer else NONE,
    )


def _setup(monkeypatch, answers, *, mode="full", outline_points=None, sentences=120, **ctx):
    data = story_data(sentences)
    outline = make_outline(
        chapters=[chapter(40, importance=5), chapter(80, importance=2), chapter(sentences, importance=4)],
        points=outline_points or [point(10, 12, statement="Pricing changed everything"), point(100, 102, statement="Missing point")],
    )
    model = ScriptedModel(answers).install(monkeypatch)
    return plan_input(plan_context("recap", data, **ctx), outline, mode=mode), model


@pytest.mark.asyncio
async def test_recap_is_one_locked_segment_inside_its_window(monkeypatch):
    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapCritique": [SHIP]})

    segments, plan = await plan_recap(pi)

    assert model.schemas() == ["RecapDraft", "RecapCritique"]
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
    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapCritique": [SHIP]}, instructions="Keep it upbeat")
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
    pi, model = _setup(monkeypatch, {"RecapDraft": [thin, _draft()], "RecapCritique": [SHIP]})

    await plan_recap(pi)

    assert model.schemas() == ["RecapDraft", "RecapDraft", "RecapCritique"]
    assert "You gave 1 points; give at least 3." in model.calls[1].prompt


@pytest.mark.asyncio
async def test_critique_reads_the_cuts_and_its_fixes_apply(monkeypatch):
    def critique(prompt):
        assert "‖ [" in prompt and "Main points of the video not included:\n- ★4 Missing point" in prompt
        return RecapCritique(
            viewer_notes="the side story breaks the flow", verdict="fix",
            fixes=[JunctionFix(beat=5, action="drop_beat", reason="tangent"),
                   JunctionFix(beat=3, action="drop_beat", reason="essential points never go")],
        )

    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapCritique": critique})
    segments, plan = await plan_recap(pi)

    base_pi, _ = _setup(monkeypatch, {"RecapDraft": [_draft()], "RecapCritique": [SHIP]})
    base_segments, base_plan = await plan_recap(base_pi)
    assert base_plan["points"][4]["label"] == "A side story"
    assert [p["label"] for p in plan["points"]] == [p["label"] for i, p in enumerate(base_plan["points"]) if i != 4]
    assert len(segments[0]["source_ranges"]) == len(base_segments[0]["source_ranges"]) - 1
    assert pi.stats.critique_fixes == 2


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
    pi, model = _setup(monkeypatch, {"RecapDraft": [_draft(), _draft()], "RecapCritique": [SHIP]}, mode="long")
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
        monkeypatch, {"RecapDraft": [_draft()], "RecapCritique": [SHIP]}, source_seconds=200.0, clip_length="long"
    )
    segments, plan = await plan_recap(pi)
    assert {"code": "TARGET_SHORTENED", "params": {}} in plan["notes"]
    assert len(segments) == 1


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
    pi, _ = _setup(monkeypatch, {"RecapDraft": [draft], "RecapCritique": [SHIP]})

    segments, plan = await plan_recap(pi)

    assert segments[0]["hook_title"] == plan["title"] == "A video about building things"
