"""Advanced settings: the registry, the request contract, and that each one really changes the output."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media.captions import build_point_label_ass
from src.media.render_ranges import burned_hook_title, point_labels_for
from src.services.video_service import VideoService
from src.workflows.assembly import RecapDraftIn, RecapPointIn, coverage_slices, fit_recap, recap_beat_cap
from src.workflows.contracts import WORKFLOW_PLAN_VERSION, WorkflowOptions, WorkflowSpec, workflow_spec_from_task
from src.workflows.durations import recap_budget, trailer_budget
from src.workflows.errors import WorkflowError, WorkflowRequestError
from src.workflows.contracts import WORKFLOWS
from src.workflows.options import parse_workflow_request, workflows_info
from src.workflows.partition import partition_series
from src.workflows.planning import apply_output_settings
from src.workflows.recap import plan_recap
from src.workflows.schemas import SeriesColdOpen
from src.workflows.series import cold_open_candidates, plan_series
from src.workflows.settings import SETTINGS, parse_settings, read_settings, settings_schema
from src.workflows.topic import plan_topic
from src.workflows.trailer import _DraftRules, _judge, button_candidates, clause_units, plan_trailer
from src.workflows.durations import part_budget
from src.workflows.units import build_units
from test_workflow_recap import _draft as recap_draft, _same_cut, _setup as recap_setup
from test_workflow_series import SHIP, _copy, _draft as series_draft, _whole_story
from test_workflow_topic import _answering, _locate, _moment
from test_workflow_trailer import _beat, _draft as trailer_draft, _setup as trailer_setup, _verdict
from workflow_planner_fakes import ScriptedModel, chapter, make_outline, plan_context, plan_input, point, story_data
from src.workflows.schemas import OutlinePayoff

ROOT = Path(__file__).resolve().parents[3]


# -- the contract -----------------------------------------------------------------------------


def _old_fingerprint(spec):
    payload = {
        "workflow": spec.workflow, "version": WORKFLOW_PLAN_VERSION, "clip_length": spec.clip_length,
        "topic": spec.topic_key, "part_count": spec.options.part_count, "instructions": "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def test_nothing_set_keeps_fingerprints_stored_options_and_the_legacy_path():
    spec = parse_workflow_request({}, "auto")
    assert spec.legacy is True
    assert spec.options.to_json() == {}
    series = parse_workflow_request({"workflow": "series"}, "auto")
    assert series.fingerprint() == _old_fingerprint(series)


def test_defaults_are_not_stored_and_change_nothing():
    spec = parse_workflow_request(
        {"workflow": "series", "workflow_options": {"advanced": {"cold_open": False, "endings": "cliffhanger", "part_labels": None}}},
        "auto",
    )
    assert spec.options.advanced == {}
    assert "advanced" not in spec.options.to_json()
    assert spec.fingerprint() == _old_fingerprint(spec)


def test_a_changed_setting_is_stored_and_changes_the_fingerprint():
    spec = parse_workflow_request({"workflow": "series", "workflow_options": {"advanced": {"cold_open": True}}}, "auto")
    assert spec.options.to_json() == {"advanced": {"cold_open": True}}
    assert spec.fingerprint() != _old_fingerprint(spec)
    assert spec.setting("cold_open") is True
    assert spec.setting("part_labels") is True


def test_highlights_with_a_setting_leave_the_legacy_path_so_the_gate_applies():
    assert parse_workflow_request({"workflow_options": {"advanced": {"max_clips": 3}}}, "auto").legacy is False


@pytest.mark.parametrize(
    "workflow, advanced",
    [
        ("series", {"nope": True}),
        ("series", {"cold_open": "yes"}),
        ("series", {"endings": "sometimes"}),
        ("recap", {"target_seconds": 5}),
        ("recap", {"target_seconds": 500}),
        ("recap", {"target_seconds": True}),
        ("topic", {"max_clips": 1.5}),
        (None, {"max_clips": 99}),
    ],
)
def test_bad_values_are_rejected_before_any_charge(workflow, advanced):
    body = {"workflow_options": {"advanced": advanced, "topic": "pricing"}}
    if workflow:
        body["workflow"] = workflow
    with pytest.raises(WorkflowRequestError) as error:
        parse_workflow_request(body, "auto")
    assert error.value.code == "INVALID_WORKFLOW_SETTING"
    assert error.value.status == 422


def test_advanced_must_be_an_object():
    with pytest.raises(WorkflowRequestError) as error:
        parse_settings("series", ["cold_open"])
    assert error.value.code == "INVALID_WORKFLOW_OPTIONS"


def test_a_setting_of_another_workflow_is_dropped_like_other_options():
    spec = parse_workflow_request({"workflow": "recap", "workflow_options": {"advanced": {"cold_open": True}}}, "auto")
    assert spec.options.advanced == {}


def test_the_worker_reads_leniently():
    assert read_settings("series", {"cold_open": True, "nope": 1, "endings": "sometimes", "part_labels": True}) == {"cold_open": True}
    assert read_settings("series", "garbage") == {}
    row = {"workflow": "recap", "workflow_options": {"advanced": {"target_seconds": 9999, "max_points": 4, "cold_open": True}}}
    assert workflow_spec_from_task(row, "auto").options.advanced == {"max_points": 4}


def test_the_schema_is_published_only_with_access():
    enabled = {entry["id"]: entry for entry in workflows_info(frozenset(WORKFLOWS))["workflows"]}
    assert {item["key"] for item in enabled["series"]["settings"]} >= {"cold_open", "part_labels", "endings", "coverage", "hook_title", "junction_style"}
    assert {"key": "max_clips", "type": "int", "default": None, "min": 1, "max": 10} in enabled["highlights"]["settings"]
    assert all(entry["settings"] == [] for entry in workflows_info(frozenset())["workflows"])
    json.dumps(settings_schema("recap"))


@pytest.mark.parametrize("locale", ["en", "de", "es", "id", "pt", "zh"])
def test_every_setting_has_text_in_every_language(locale):
    path = ROOT / "frontend" / "messages" / locale / "home.json"
    if not path.exists():
        pytest.skip("frontend sources are not part of this image")
    home = json.loads(path.read_text(encoding="utf-8"))
    advanced = home["advanced"]
    assert "INVALID_WORKFLOW_SETTING" in home["workflowErrors"]
    for settings in SETTINGS.values():
        for setting in settings:
            assert advanced["settings"][setting.key]["label"] and advanced["settings"][setting.key]["hint"]
            for choice in setting.choices:
                assert advanced["choices"][choice]


# -- shared -----------------------------------------------------------------------------------


def _segment(fades, **meta):
    return {"junction_fades": list(fades), "workflow_meta": {"junction_fades": list(fades), **meta}, "hook_title": "Title"}


def _spec(workflow, **advanced):
    return WorkflowSpec(workflow, WorkflowOptions(advanced=advanced), "auto")


def test_hook_title_off_keeps_the_name_and_stops_the_burn_in():
    segment = _segment([])
    assert burned_hook_title(segment) == "Title"
    apply_output_settings(_spec("recap", hook_title=False), [segment])
    assert segment["hook_title"] == "Title"
    assert burned_hook_title(segment) is None


@pytest.mark.asyncio
async def test_the_render_receives_no_title_when_it_is_off(monkeypatch, tmp_path):
    seen = {}

    def render(*args, **kwargs):
        seen["hook_title"] = args[11]
        seen["point_labels"] = kwargs.get("point_labels")
        return False

    monkeypatch.setattr("src.services.video_service.create_optimized_clip", render)
    segment = {
        "start_time": "00:01", "end_time": "00:05", "keep_ranges": [[1, 5]], "text": "x", "hook_title": "Title",
        "workflow_meta": {"burn_hook_title": False},
    }
    await VideoService.create_single_clip(tmp_path / "v.mp4", segment, 0, tmp_path)
    assert seen == {"hook_title": None, "point_labels": None}


@pytest.mark.parametrize("style, expected", [("hard", [0.0, 0.0]), ("soft", [0.12, 0.12])])
def test_junction_style_replaces_the_workflow_fades(style, expected):
    segment = _segment([0.1, 0.1])
    apply_output_settings(_spec("series", junction_style=style), [segment])
    assert segment["junction_fades"] == expected
    assert segment["workflow_meta"]["junction_fades"] == expected


def test_a_cold_opens_jump_stays_a_hard_cut_with_soft_junctions():
    segment = _segment([0.0, 0.1], cold_open={"range_count": 1})
    apply_output_settings(_spec("series", junction_style="soft"), [segment])
    assert segment["junction_fades"] == [0.0, 0.12]


def test_nothing_set_leaves_segments_alone():
    segment = _segment([0.1])
    apply_output_settings(_spec("series"), [segment])
    assert segment == _segment([0.1])


# -- series -----------------------------------------------------------------------------------


def _series_answers(**extra):
    return {"SeriesDraft": [series_draft()], "SeriesCritique": [SHIP], "SeriesCopy": [_copy()], **extra}


@pytest.mark.asyncio
async def test_part_labels(monkeypatch):
    pi, _ = _whole_story(monkeypatch, _series_answers())
    segments, _ = await plan_series(pi)
    assert segments[0]["hook_title"].startswith("Part 1")

    pi, _ = _whole_story(monkeypatch, _series_answers(), advanced={"part_labels": False})
    segments, plan = await plan_series(pi)
    assert not segments[0]["hook_title"].startswith("Part")
    assert segments[0]["part_number"] == 1


@pytest.mark.asyncio
async def test_cold_open_off_makes_no_extra_call_and_plays_in_order(monkeypatch):
    pi, model = _whole_story(monkeypatch, _series_answers())
    segments, plan = await plan_series(pi)
    assert "SeriesColdOpen" not in model.schemas()
    assert "cold_open" not in segments[0]["workflow_meta"]


@pytest.mark.asyncio
async def test_cold_open_plays_a_later_line_before_part_one_and_nothing_else_moves(monkeypatch):
    pi, plain_model = _whole_story(monkeypatch, _series_answers())
    plain, _ = await plan_series(pi)

    pick = SeriesColdOpen(reasoning="stakes", candidate=1)
    pi, model = _whole_story(monkeypatch, _series_answers(SeriesColdOpen=[pick]), advanced={"cold_open": True})
    segments, plan = await plan_series(pi)

    assert "SeriesColdOpen" in model.schemas()
    cold = segments[0]["workflow_meta"]["cold_open"]
    count = cold["range_count"]
    # The story plays exactly as before, after the flash-forward.
    assert segments[0]["source_ranges"][count:] == plain[0]["source_ranges"]
    assert segments[0]["source_ranges"][0][0] > plain[0]["source_ranges"][-1][0]
    assert segments[0]["junction_fades"][count - 1] == 0.0
    assert [s["source_ranges"] for s in segments[1:]] == [s["source_ranges"] for s in plain[1:]]
    # Labels and file names still say where the part's story starts.
    assert (segments[0]["start_time"], segments[0]["end_time"]) == (plain[0]["start_time"], plain[0]["end_time"])
    assert plan["parts"][0]["cold_open"]["from_part"] == cold["from_part"] >= 2
    assert plan["parts"][0]["ranges"] != plain[0]["source_ranges"]


@pytest.mark.asyncio
async def test_no_strong_line_means_no_cold_open_and_says_so(monkeypatch):
    pick = SeriesColdOpen(reasoning="all weak", candidate=-1)
    pi, _ = _whole_story(monkeypatch, _series_answers(SeriesColdOpen=[pick]), advanced={"cold_open": True})
    segments, plan = await plan_series(pi)
    assert "cold_open" not in segments[0]["workflow_meta"]
    assert any(note["code"] == "COLD_OPEN_SKIPPED" for note in plan["notes"])


def test_cold_open_candidates_avoid_the_finale_payoffs_and_part_one(monkeypatch):
    data = story_data(120)
    payoffs = [OutlinePayoff(first_unit=50, last_unit=51, what="reveal")]
    outline = make_outline(chapters=[chapter(120, "story")], payoffs=payoffs)
    pi = plan_input(plan_context("series", data), outline)
    budget = part_budget("auto")
    result = partition_series(pi.index, (1, 120), [], budget, 5, [], None, {})
    found = cold_open_candidates(pi, result, budget)
    assert found
    for item in found:
        assert result.parts[1][0] <= item.first <= item.last <= result.parts[-2][1]
        assert not ({item.first, item.last} & {50, 51})
        assert 3.0 <= item.seconds <= 9.0


def test_natural_endings_stop_chasing_questions():
    questions = {25, 50, 75, 100, 125}
    pi_index = build_units(story_data(150, questions=tuple(questions)), "")
    budget = part_budget("auto")
    hooked = partition_series(pi_index, (1, 150), [], budget, 6, [], None, {})
    natural = partition_series(pi_index, (1, 150), [], budget, 6, [], None, {}, natural_endings=True)
    assert len({end for _, end in hooked.parts} & questions) > len({end for _, end in natural.parts} & questions)


@pytest.mark.asyncio
async def test_natural_endings_reach_the_prompts(monkeypatch):
    pi, model = _whole_story(monkeypatch, _series_answers(), advanced={"endings": "natural"})
    await plan_series(pi)
    assert "Don't chase cliffhangers" in model.calls[0].prompt


@pytest.mark.asyncio
async def test_start_to_finish_chooses_no_chapters_and_runs_in_order(monkeypatch):
    chapters = [chapter(n * 40, "content", 5 if n == 2 else 2) for n in range(1, 5)]
    data = story_data(160)
    outline = make_outline(chapters=chapters)

    default_model = ScriptedModel(_series_answers(SeriesScope=[])).install(monkeypatch)
    pi = plan_input(plan_context("series", data, advanced={"coverage": "start_to_finish"}), outline)
    segments, plan = await plan_series(pi)

    assert "SeriesScope" not in default_model.schemas()
    assert plan["skips"] == []
    assert plan["parts"][0]["first_unit"] <= 5


# -- topic ------------------------------------------------------------------------------------


def _topic_pi(monkeypatch, *, locate=None, advanced=None):
    data = story_data(200, texts={uid: f"The pricing story number {uid} keeps going on and on here." for uid in range(10, 190, 30)})
    outline = make_outline(chapters=[chapter(200)])
    moments = [_moment(uid, uid + 3, title=f"Pricing story {uid}") for uid in range(10, 190, 30)]
    model = ScriptedModel({"TopicLocate": [locate or _locate()], "TopicExtraction": _answering(moments)}).install(monkeypatch)
    return plan_input(plan_context("topic", data, topic="pricing", advanced=advanced), outline), model


@pytest.mark.asyncio
async def test_topic_max_clips_limits_the_clips(monkeypatch):
    pi, _ = _topic_pi(monkeypatch)
    segments, _ = await plan_topic(pi)
    selected = [s for s in segments if not s["workflow_meta"].get("reserve")]
    assert len(selected) > 1

    pi, _ = _topic_pi(monkeypatch, advanced={"max_clips": 1})
    segments, plan = await plan_topic(pi)
    assert len([s for s in segments if not s["workflow_meta"].get("reserve")]) == 1
    # Asking for one clip isn't a thin topic.
    assert not any(note["code"] == "TOPIC_THIN" for note in plan["notes"])


@pytest.mark.asyncio
async def test_strict_match_refuses_a_broad_fit(monkeypatch):
    broad = _locate().model_copy(update={"fit": "broader"})
    pi, _ = _topic_pi(monkeypatch, locate=broad)
    _, plan = await plan_topic(pi)
    assert any(note["code"] == "TOPIC_BROADENED" for note in plan["notes"])

    pi, _ = _topic_pi(monkeypatch, locate=broad, advanced={"match": "strict"})
    with pytest.raises(WorkflowError) as error:
        await plan_topic(pi)
    assert error.value.code == "topic_not_found"


# -- recap ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_screen_points_default_on_and_can_be_turned_off(monkeypatch):
    pi, _ = recap_setup(monkeypatch, {"RecapDraft": [recap_draft()], "RecapFinalCut": _same_cut})
    segments, plan = await plan_recap(pi)
    meta = segments[0]["workflow_meta"]
    starts = [float(i) for i in range(len(segments[0]["source_ranges"]))]
    labels = point_labels_for(meta, starts)
    assert labels
    # Only points show: "Intro" and "Takeaway" name nothing.
    assert not {"Intro", "Takeaway"} & {label["text"] for label in labels}

    pi, _ = recap_setup(monkeypatch, {"RecapDraft": [recap_draft()], "RecapFinalCut": _same_cut}, advanced={"on_screen_points": False})
    segments, _ = await plan_recap(pi)
    assert point_labels_for(segments[0]["workflow_meta"], starts) == []


def test_a_point_with_several_ranges_shows_once_at_its_start_and_stored_starts_hold_after_a_caption_edit():
    meta = {
        "points": [
            {"label": "Intro", "kind": "opener", "range_index": 0, "output_start": 0.0},
            {"label": "Why it broke", "kind": "point", "range_index": 1, "output_start": 5.0},
            {"label": "Why it broke", "kind": "point", "range_index": 2, "output_start": 9.0},
            {"label": "The fix", "kind": "point", "range_index": 3, "output_start": 14.0},
        ]
    }
    assert point_labels_for(meta) == [{"text": "Why it broke", "start": 5.0}, {"text": "The fix", "start": 14.0}]
    assert point_labels_for(meta, [0.0, 6.0, 10.0, 15.0])[1]["start"] == 15.0


def _ass(labels, hook_end=0.0, duration=60.0, **kwargs):
    template = {"font_color": "#FFFFFF", **kwargs.pop("template", {})}
    return build_point_label_ass(labels, template, 1080, 1920, duration, "Arial", 60, hook_end=hook_end, **kwargs)


def test_labels_last_about_three_seconds_and_never_meet_the_next_one():
    _, events = _ass([{"text": "First", "start": 10.0}, {"text": "Second", "start": 11.5}, {"text": "Third", "start": 20.0}])
    assert "0:00:10.00,0:00:11.45" in events[0]
    assert "0:00:11.50,0:00:14.50" in events[1]
    assert "0:00:20.00,0:00:23.00" in events[2]


def test_a_label_waits_for_the_hook_title():
    _, events = _ass([{"text": "First", "start": 1.0}], hook_end=4.1)
    assert "0:00:04.10,0:00:07.10" in events[0]


def test_labels_move_with_the_title_when_captions_take_the_top():
    _, top = _ass([{"text": "First", "start": 10.0}])
    _, bottom = _ass([{"text": "First", "start": 10.0}], position_y=0.94)
    assert "\\pos" not in top[0]
    assert "\\an2\\pos(540,1805)" in bottom[0]


def test_a_label_too_short_to_read_is_left_out():
    _, events = _ass([{"text": "First", "start": 10.0}, {"text": "Second", "start": 10.5}])
    assert len(events) == 1 and "Second" in events[0]


def test_labels_reach_the_subtitle_file(tmp_path):
    from src.media.captions import build_assemblyai_ass_subtitles

    ass = tmp_path / "x.ass"
    assert build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0, 30, 1080, 1920, ass, keep_ranges=[(0.0, 30.0)], output_offsets=[0.0],
        include_captions=False, point_labels=[{"text": "Why it broke", "start": 5.0}],
    )
    text = ass.read_text(encoding="utf-8")
    assert "Style: Point," in text
    assert "Why it broke" in text


@pytest.mark.asyncio
async def test_target_seconds_replaces_the_length_chip(monkeypatch):
    assert recap_budget("auto", 3000, 60).target == 60
    assert recap_budget("long", 3000, 60).target == 60
    assert recap_budget("auto", 100, 90).shortened is True
    pi, _ = recap_setup(monkeypatch, {"RecapDraft": [recap_draft()], "RecapFinalCut": _same_cut}, advanced={"target_seconds": 45})
    segments, plan = await plan_recap(pi)
    assert plan["seconds"] <= recap_budget("auto", 3000, 45).max


def _recap_points(index, spans):
    return [
        RecapPointIn(label=f"P{first}", first=first, last=last, core_first=first, core_last=last, importance=importance)
        for first, last, importance in spans
    ]


def _spread_case():
    data = story_data(600)
    index = build_units(data, "")
    outline = make_outline(chapters=[chapter(200, importance=5), chapter(400, importance=3), chapter(600, importance=3)])
    # The early points are worth the most: a recap chooses them all unless told to spread.
    spans = [(10 + n * 8, 12 + n * 8, 1) for n in range(6)] + [(250, 252, 3), (350, 352, 3), (500, 502, 3)]
    return index, outline, RecapDraftIn(title="t", opener=None, points=_recap_points(index, spans), closer=None)


def test_whole_video_coverage_reaches_every_quarter():
    index, outline, draft = _spread_case()
    budget = recap_budget("medium", 3000)
    slices = coverage_slices(index, outline, budget=budget)
    assert len(slices) == 4
    assert len(coverage_slices(index, outline, budget=recap_budget("short", 3000))) == 3
    early = fit_recap(index, draft, budget, outline)
    spread = fit_recap(index, draft, budget, outline, whole_video=True)
    assert max(beat.first for beat in early.beats) < slices[1][0]
    for low, high in slices:
        assert any(low <= beat.first <= high for beat in spread.beats), (low, high)


def test_framing_and_minor_chapters_dont_count_as_video_to_cover():
    data = story_data(300)
    index = build_units(data, "")
    outline = make_outline(chapters=[chapter(100, importance=5), chapter(200, importance=5), chapter(300, importance=1)])
    # The last chapter is an outro in all but name: a stretch of nothing asks for nothing.
    slices = coverage_slices(index, outline)
    assert all(low <= 225 for low, _ in slices) and len(slices) == 3
    trimmed = coverage_slices(index, outline, framing=set(range(201, 301)))
    assert trimmed[-1][1] <= 200


def test_max_points_holds_even_under_the_window():
    index, outline, draft = _spread_case()
    budget = recap_budget("short", 3000)
    cut = fit_recap(index, draft, budget, outline, max_points=2)
    assert sum(1 for beat in cut.beats if beat.kind == "point") == 2
    assert recap_beat_cap(budget) > 2


# -- trailer ----------------------------------------------------------------------------------


def test_trailer_target_seconds_sets_the_window():
    assert trailer_budget("auto", 60).target == 60
    assert trailer_budget("short", 60).max == 66.0


@pytest.mark.asyncio
async def test_relaxed_spoilers_free_the_payoff_and_ignore_the_veto(monkeypatch):
    pi, _ = trailer_setup(monkeypatch, {})
    clauses = await clause_units(pi)
    from src.workflows.trailer import spoiler_sentences, _to_clause_ids

    assert _to_clause_ids(pi, clauses, spoiler_sentences(pi))
    strict = _DraftRules(clauses, {5, 6}, None, trailer_budget("auto"), {})
    relaxed = _DraftRules(clauses, set(), None, trailer_budget("auto"), {}, relaxed=True)
    draft = trailer_draft(payoffs=[(100, 101)])
    assert strict.blocked(draft) >= {5, 6}
    assert relaxed.blocked(draft) == set()

    verdict = _verdict(reveals_payoff=True, offending_beat=2)
    pi, _ = trailer_setup(monkeypatch, {"SpoilerVerdict": [verdict]}, advanced={"spoilers": "relaxed"})
    cut = SimpleNamespace(beats=[])
    monkeypatch.setattr("src.workflows.trailer.judge_prompt", lambda *a, **k: "p")
    judged = await _judge(pi, clauses, cut, [], [])
    assert judged.reveals_payoff is False and judged.offending_beat == -1


@pytest.mark.asyncio
async def test_relaxed_spoilers_mark_nothing_off_limits(monkeypatch):
    answers = {"TrailerDraft": [trailer_draft()], "SpoilerVerdict": [_verdict()]}
    pi, model = trailer_setup(monkeypatch, answers, advanced={"spoilers": "relaxed"})
    await plan_trailer(pi)
    assert "[OFF LIMITS]" not in model.calls[0].prompt
    assert "give the ending away" in model.calls[0].prompt

    pi, model = trailer_setup(monkeypatch, {"TrailerDraft": [trailer_draft()], "SpoilerVerdict": [_verdict()]})
    await plan_trailer(pi)
    assert "[OFF LIMITS]" in model.calls[0].prompt


@pytest.mark.asyncio
async def test_question_ending_keeps_only_question_buttons_and_asks_for_one(monkeypatch):
    pi, _ = trailer_setup(monkeypatch, {})
    clauses = await clause_units(pi)
    every = button_candidates(pi, clauses, set(), set(), None)
    questions = button_candidates(pi, clauses, set(), set(), None, questions_only=True)
    assert questions and len(questions) < len(every)
    assert all(clauses.unit(last).ends_question for _, last in questions)

    rules = _DraftRules(clauses, set(), None, trailer_budget("auto"), {}, candidates=questions, question_ending=True)
    assert any("end on a question" in issue for issue in rules(_flat_ending_draft()))
    assert not any("end on a question" in issue for issue in rules(trailer_draft()))


@pytest.mark.asyncio
async def test_question_ending_changes_the_finished_trailer(monkeypatch):
    def answers():
        return {
            "TrailerDraft": lambda prompt: _flat_ending_draft(),
            "SpoilerVerdict": lambda prompt: _verdict(),
            "ButtonPick": lambda prompt: _pick(1),
        }

    async def last_unit(**ctx):
        pi, _ = trailer_setup(monkeypatch, answers(), **ctx)
        clauses = await clause_units(pi)
        segments, _ = await plan_trailer(pi)
        end = segments[0]["source_ranges"][-1][1]
        return clauses.unit(next(u for u in range(1, len(clauses) + 1) if abs(clauses.cut_out[u] - end) < 0.01))

    assert not (await last_unit()).ends_question
    assert (await last_unit(advanced={"ending": "question"})).ends_question


def _flat_ending_draft():
    return trailer_draft(
        beats=[_beat("cold_open", 40), _beat("context", 10, 11), _beat("escalation", 20), _beat("escalation", 50),
               _beat("escalation", 60), _beat("button", 70)],
    )


def _pick(candidate):
    from src.workflows.schemas import ButtonPick

    return ButtonPick(reasoning="x", candidate=candidate)
