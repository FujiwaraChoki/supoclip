from types import SimpleNamespace

import pytest

from src.workflows.assembly import (
    RECAP_JUNCTION_FADE,
    TRAILER_JUNCTION_FADE,
    RecapDraftIn,
    RecapFixIn,
    RecapPointIn,
    TrailerBeatIn,
    apply_recap_fixes,
    fit_recap,
    fit_span,
    fit_trailer,
    recap_seconds,
)
from src.workflows.durations import Budget, clip_budget
from src.workflows.errors import WorkflowError
from src.workflows.units import build_units

WORD = 0.3
WORD_GAP = 0.05


def _story(sentences, *, gap=0.4, granularity="sentence"):
    """Sentence specs -> unit index. A spec is a word count or (word count, first words, end mark)."""
    words, cursor = [], 1.0
    for spec in sentences:
        count, opening, end = (spec, (), ".") if isinstance(spec, int) else (spec + ((), ".")[len(spec) - 1:])[:3]
        for position in range(count):
            text = opening[position] if position < len(opening) else f"w{len(words)}"
            if position == count - 1:
                text += end
            words.append({"text": text, "start": round(cursor * 1000), "end": round((cursor + WORD) * 1000)})
            cursor += WORD + WORD_GAP
        cursor += gap - WORD_GAP
    return build_units({"words": words, "language_code": "en"}, "", granularity=granularity)


# One 14-word sentence plays ~5.3 s with its padding.
STORY = _story([14] * 60)
SENTENCE = STORY.speech_seconds(1, 1)


def _budget(target, *, low=0.85, high=1.15):
    return Budget(
        min=round(low * target, 1), ideal_min=round(0.95 * target, 1), ideal_max=round(1.05 * target, 1),
        max=round(high * target, 1), render_cap=round(high * target + 5, 1), target=float(target),
    )


# -- fit_span ------------------------------------------------------------------


def test_fit_span_keeps_a_span_that_already_fits():
    budget = clip_budget("auto")  # 15-60 s, ideally 25-50
    assert fit_span(STORY, 10, 15, budget, anchor=12) == (10, 15)


def test_fit_span_trims_the_end_first_and_keeps_the_anchor():
    budget = Budget(min=15, ideal_min=20, ideal_max=30, max=35, render_cap=40)
    first, last = fit_span(STORY, 10, 25, budget, anchor=12)
    assert first == 10 and last >= 12
    assert budget.min <= STORY.speech_seconds(first, last) <= budget.max
    # The longest end that still fits.
    assert STORY.speech_seconds(first, last + 1) > budget.max


def test_fit_span_trims_the_start_when_the_anchor_is_late():
    budget = Budget(min=15, ideal_min=20, ideal_max=30, max=35, render_cap=40)
    first, last = fit_span(STORY, 10, 25, budget, anchor=25)
    assert last == 25 and first > 10
    assert STORY.unit(first).starts_sentence
    assert budget.min <= STORY.speech_seconds(first, last) <= budget.max


def test_fit_span_extends_short_spans_forward_then_back():
    budget = Budget(min=15, ideal_min=20, ideal_max=30, max=35, render_cap=40)
    first, last = fit_span(STORY, 20, 20, budget, anchor=20)
    assert first == 20 and last > 20
    assert STORY.speech_seconds(first, last) >= budget.ideal_min
    # At the end of the video it can only grow backward.
    first, last = fit_span(STORY, 60, 60, budget, anchor=60)
    assert last == 60 and first < 60
    assert STORY.speech_seconds(first, last) >= budget.ideal_min


def test_fit_span_gives_up_when_the_anchor_cannot_fit():
    long_sentence = _story([14, 120, 14])  # the middle sentence splits into ~40-word units of ~14 s
    budget = Budget(min=2, ideal_min=3, ideal_max=4, max=5, render_cap=10)
    assert fit_span(long_sentence, 2, 3, budget, anchor=2) is None


def test_fit_span_never_ends_inside_a_sentence_when_trimming():
    sentences = [14] * 5 + [(45, (), ".")] + [14] * 5
    index = _story(sentences)
    budget = Budget(min=10, ideal_min=15, ideal_max=25, max=30, render_cap=35)
    first, last = fit_span(index, 1, len(index), budget, anchor=2)
    assert index.boundary_strength(last) >= 0.75


def test_fit_span_moves_mid_sentence_edges_to_the_sentence():
    # Sentence 3 is 45 words: it splits into a 40-word unit (#3, forced end)
    # and a 5-word tail (#4) that does not open a sentence.
    index = _story([14, 14, (45, (), "."), 14, 14, 14, 14])
    assert index.boundary_strength(3) < 0.75 and not index.unit(4).starts_sentence
    budget = Budget(min=10, ideal_min=15, ideal_max=50, max=60, render_cap=65)
    # Ends inside the sentence: extended to its close.
    assert fit_span(index, 2, 3, budget, anchor=2) == (2, 4)
    # Starts inside it: moved back to its opening.
    assert fit_span(index, 4, 6, budget, anchor=5) == (3, 6)
    # Moving back would pass the maximum, so the start moves forward instead.
    tight = Budget(min=5, ideal_min=8, ideal_max=15, max=18, render_cap=23)
    assert fit_span(index, 4, 6, tight, anchor=5) == (5, 6)


# -- recap ---------------------------------------------------------------------


def _point(label, first, last, core_first, core_last, importance):
    return RecapPointIn(label, first, last, core_first, core_last, importance)


def _kinds(cut):
    return [(beat.kind, beat.first, beat.last, beat.span_mode) for beat in cut.beats]


def test_recap_starts_on_cores_and_grows_the_most_important_points_first():
    draft = RecapDraftIn(
        title="t",
        opener=(50, 50),
        points=[_point("one", 5, 7, 6, 6, 2), _point("two", 15, 17, 16, 16, 1), _point("three", 30, 32, 31, 31, 3)],
        closer=(40, 40),
    )
    budget = _budget(40)
    cut = fit_recap(STORY, draft, budget, None, None)
    assert _kinds(cut) == [
        ("opener", 50, 50, "full"),
        ("point", 6, 6, "core"),
        ("point", 15, 17, "full"),
        ("point", 31, 31, "core"),
        ("closer", 40, 40, "full"),
    ]
    assert cut.seconds == pytest.approx(recap_seconds(STORY, cut.beats))
    assert budget.min <= cut.seconds <= budget.max
    assert cut.seconds == pytest.approx(
        sum(STORY.speech_seconds(b.first, b.last) for b in cut.beats) - RECAP_JUNCTION_FADE * 4
    )
    assert cut.notes == []
    assert [beat.droppable for beat in cut.beats] == [False, True, False, True, False]


def test_recap_shrinks_nice_to_have_points_first_and_keeps_opener_closer_and_an_essential():
    # Two-sentence cores four units apart (points one unit apart would merge).
    points = [_point(f"p{n}", 4 * n + 1, 4 * n + 2, 4 * n + 1, 4 * n + 2, importance)
              for n, importance in enumerate([1, 3, 2, 3, 2, 1, 3])]
    draft = RecapDraftIn("t", (59, 59), points, (40, 40))
    budget = _budget(40)
    cut = fit_recap(STORY, draft, budget, None, None)
    labels = [beat.label for beat in cut.beats if beat.kind == "point"]
    # All three nice-to-haves go first (latest first), then important ones, latest first.
    assert "p1" not in labels and "p3" not in labels and "p6" not in labels
    assert labels[0] == "p0" and "p5" in labels
    assert cut.beats[0].kind == "opener" and cut.beats[-1].kind == "closer"
    assert cut.seconds <= budget.max


def test_recap_merges_touching_points():
    draft = RecapDraftIn(
        "t", None,
        [_point("Pricing", 10, 11, 10, 10, 2), _point("Discounts", 13, 14, 14, 14, 1), _point("Pricing", 12, 12, 12, 12, 3),
         _point("Hiring", 30, 31, 30, 30, 2)],
        None,
    )
    cut = fit_recap(STORY, draft, _budget(30), None, None)
    points = [beat for beat in cut.beats if beat.kind == "point"]
    assert len(points) == 2
    merged = points[0]
    assert merged.full == (10, 14) and merged.importance == 1
    assert merged.label == "Pricing · Discounts"


def test_recap_opener_that_contains_a_point_replaces_it():
    draft = RecapDraftIn("t", (20, 21), [_point("a", 5, 6, 5, 5, 1), _point("b", 19, 22, 20, 21, 2)], None)
    cut = fit_recap(STORY, draft, _budget(20), None, None)
    assert [beat.kind for beat in cut.beats] == ["opener", "point"]
    assert cut.beats[0].first == 20


def test_recap_drops_an_opener_that_only_grazes_a_point_and_a_closer_before_the_last_point():
    draft = RecapDraftIn("t", (21, 23), [_point("a", 5, 6, 5, 5, 1), _point("b", 19, 22, 20, 20, 2)], (2, 2))
    cut = fit_recap(STORY, draft, _budget(20), None, None)
    assert [beat.kind for beat in cut.beats] == ["point", "point"]


def test_recap_adds_uncovered_outline_points_when_short():
    outline = SimpleNamespace(main_points=[
        SimpleNamespace(statement="Covered", first_unit=6, last_unit=7, core_first=6, core_last=6, importance=5),
        SimpleNamespace(statement="Extra", first_unit=30, last_unit=31, core_first=30, core_last=30, importance=4),
        SimpleNamespace(statement="After the closer", first_unit=55, last_unit=56, core_first=55, core_last=55, importance=5),
    ])
    draft = RecapDraftIn("t", None, [_point("a", 5, 7, 6, 6, 1), _point("b", 15, 17, 16, 16, 1)], (45, 45))
    cut = fit_recap(STORY, draft, _budget(40), outline, None)
    extra = [beat for beat in cut.beats if beat.from_outline]
    assert [beat.label for beat in extra] == ["Extra"]
    assert extra[0].importance == 2
    assert cut.beats[-1].kind == "closer"


def test_recap_bridges_a_beat_that_opens_on_a_back_reference():
    index = _story([14] * 20 + [(14, ("That's", "why"))] + [14] * 20)
    draft = RecapDraftIn("t", None, [_point("a", 3, 4, 3, 3, 1), _point("b", 21, 23, 21, 21, 2)], None)
    cut = fit_recap(index, draft, _budget(30), None, None)
    second = cut.beats[1]
    assert second.first == 20 and second.lead == 1
    # No room: the beat is flagged for the critique instead.
    tight = fit_recap(index, draft, _budget(10, high=1.15), None, None)
    assert tight.attention == [2] and tight.beats[1].first == 21


def test_recap_on_a_short_source_notes_the_shortened_target():
    draft = RecapDraftIn("t", None, [_point("a", 3, 3, 3, 3, 1), _point("b", 9, 9, 9, 9, 1)], None)
    cut = fit_recap(STORY, draft, _budget(120), None, None)
    assert cut.notes == [{"code": "TARGET_SHORTENED", "params": {}}]
    shortened = Budget(min=25, ideal_min=28, ideal_max=32, max=35, render_cap=40, target=30, shortened=True)
    assert fit_recap(STORY, RecapDraftIn("t", None, [_point("a", 3, 5, 3, 3, 1), _point("b", 9, 11, 9, 9, 1)], None),
                     shortened, None, None).notes[0]["code"] == "TARGET_SHORTENED"


def test_recap_unsupported():
    one_point = RecapDraftIn("t", None, [_point("a", 3, 4, 3, 3, 1)], None)
    with pytest.raises(WorkflowError) as raised:
        fit_recap(STORY, one_point, _budget(40), None, None)
    assert raised.value.code == "recap_unsupported"
    tiny = _story([14] * 8)  # ~42 s of speech in the whole source
    draft = RecapDraftIn("t", None, [_point("a", 1, 2, 1, 1, 1), _point("b", 5, 6, 5, 5, 1)], None)
    with pytest.raises(WorkflowError):
        fit_recap(tiny, draft, _budget(20), None, None)


def test_recap_fixes_are_applied_then_refit():
    draft = RecapDraftIn(
        "t", (50, 50),
        [_point("a", 5, 7, 6, 6, 1), _point("b", 15, 17, 16, 16, 2), _point("c", 30, 32, 31, 31, 3)],
        None,
    )
    budget = _budget(40)
    cut = fit_recap(STORY, draft, budget, None, None)
    fixed = apply_recap_fixes(
        STORY, cut,
        [RecapFixIn(1, "drop_beat"), RecapFixIn(4, "drop_beat"), RecapFixIn(3, "use_core"), RecapFixIn(2, "extend_back"), RecapFixIn(9, "use_full")],
        budget,
    )
    kinds = [(beat.kind, beat.label) for beat in fixed.beats]
    assert ("opener", "Intro") in kinds  # the opener is not droppable
    assert ("point", "c") not in kinds
    b = next(beat for beat in fixed.beats if beat.label == "b")
    assert b.span_mode == "core"
    a = next(beat for beat in fixed.beats if beat.label == "a")
    assert a.first == cut.beats[1].first - 1
    assert fixed.seconds <= budget.max


# -- trailer -------------------------------------------------------------------

CLAUSES = _story([8] * 10 + [(8, (), "?")] + [8] * 30, granularity="clause")


def _roles(cut):
    return [(beat.role, beat.first, beat.last) for beat in cut.beats]


def test_trailer_drops_spoiler_beats_and_orders_cold_open_and_button():
    beats = [
        TrailerBeatIn("context", 3, 3),
        TrailerBeatIn("cold_open", 20, 20),
        TrailerBeatIn("escalation", 30, 30),
        TrailerBeatIn("escalation", 11, 11),  # ends on a question: becomes the button
        TrailerBeatIn("escalation", 38, 38),  # a spoiler
    ]
    budget = _budget(12, low=0.8, high=1.1)
    cut = fit_trailer(CLAUSES, beats, {37, 38, 39}, budget)
    assert [beat.first for beat in cut.dropped] == [38]
    assert cut.beats[0].role == "cold_open" and cut.beats[0].first == 20
    assert cut.beats[-1] .role == "button" and cut.beats[-1].first == 11
    assert all(not {37, 38, 39} & set(range(beat.first, beat.last + 1)) for beat in cut.beats)
    # Play order may jump back in the source.
    assert [beat.first for beat in cut.beats] == [20, 3, 30, 11]
    assert cut.usable


def test_trailer_over_budget_drops_escalations_then_extra_context_never_the_ends():
    beats = [TrailerBeatIn("cold_open", 1, 1), TrailerBeatIn("context", 5, 5), TrailerBeatIn("context", 7, 7)]
    beats += [TrailerBeatIn("escalation", n, n) for n in (14, 17, 20, 23)]
    beats += [TrailerBeatIn("button", 11, 11)]
    budget = _budget(14, low=0.8, high=1.1)
    cut = fit_trailer(CLAUSES, beats, set(), budget)
    roles = _roles(cut)
    assert roles[0] == ("cold_open", 1, 1) and roles[-1] == ("button", 11, 11)
    assert cut.seconds <= budget.max
    assert ("context", 5, 5) in roles
    assert cut.seconds == pytest.approx(
        sum(CLAUSES.speech_seconds(b.first, b.last) for b in cut.beats) - TRAILER_JUNCTION_FADE * (len(cut.beats) - 1)
    )


def test_trailer_under_budget_grows_escalations_clear_of_spoilers():
    beats = [TrailerBeatIn("cold_open", 1, 1), TrailerBeatIn("escalation", 20, 20), TrailerBeatIn("button", 11, 11)]
    budget = _budget(20, low=0.8, high=1.1)
    cut = fit_trailer(CLAUSES, beats, {22}, budget)
    escalation = cut.beats[1]
    assert escalation.first == 20 and escalation.last == 21  # stops before the spoiler
    blocked = fit_trailer(CLAUSES, beats, {21}, budget)
    assert blocked.beats[1].last == 20
    assert not blocked.fits and not blocked.usable


def test_trailer_merges_beats_that_continue_each_other():
    beats = [TrailerBeatIn("cold_open", 25, 25), TrailerBeatIn("context", 3, 3), TrailerBeatIn("escalation", 4, 4), TrailerBeatIn("button", 11, 11)]
    cut = fit_trailer(CLAUSES, beats, set(), _budget(12, low=0.8, high=1.1))
    assert _roles(cut)[1] == ("context", 3, 4)
    assert len(cut.beats) == 3


def test_trailer_with_no_button_uses_the_latest_escalation():
    beats = [TrailerBeatIn("cold_open", 25, 25), TrailerBeatIn("escalation", 30, 30), TrailerBeatIn("escalation", 5, 5), TrailerBeatIn("context", 2, 2)]
    cut = fit_trailer(CLAUSES, beats, set(), _budget(12, low=0.8, high=1.1))
    assert cut.beats[-1].role == "button" and cut.beats[-1].first == 30


def test_trailer_trims_overlong_beats_and_keeps_the_button_line():
    clauses = _story([30] * 10, granularity="clause")  # 30-word sentences become ~3 clause units each
    assert len(clauses) > 10
    last = len(clauses)
    beats = [TrailerBeatIn("cold_open", 1, 6), TrailerBeatIn("button", last - 5, last)]
    cut = fit_trailer(clauses, beats, set(), _budget(14, low=0.8, high=1.1))
    cold, button = cut.beats[0], cut.beats[-1]
    assert clauses.speech_seconds(cold.first, cold.last) <= 8.0 and cold.first == 1
    assert button.last == last and clauses.speech_seconds(button.first, button.last) <= 10.0


def test_trailer_with_too_few_beats_is_not_usable():
    cut = fit_trailer(CLAUSES, [TrailerBeatIn("cold_open", 1, 1), TrailerBeatIn("button", 11, 11)], set(), _budget(10))
    assert len(cut.beats) == 2 and not cut.usable


def test_recap_never_ships_as_opener_and_closer_alone():
    # No usable point (the -1 sentinel is "none", not unit 1): refused.
    with pytest.raises(WorkflowError) as raised:
        fit_recap(STORY, RecapDraftIn("t", (1, 1), [_point("x", -1, -1, -1, -1, 1)], (40, 40)), _budget(12), None, None)
    assert raised.value.code == "recap_unsupported"
    # An opener that contains a point's core plays that point: a real recap.
    cut = fit_recap(STORY, RecapDraftIn("t", (10, 10), [_point("a", 10, 10, 10, 10, 1)], (40, 40)), _budget(12), None, None)
    assert [beat.kind for beat in cut.beats] == ["opener", "closer"]
    assert cut.beats[0].covers_point and not cut.beats[0].droppable
    # The critique can't drop the last point either.
    draft = RecapDraftIn("t", (1, 1), [_point("a", 10, 11, 10, 10, 2), _point("b", 20, 21, 20, 20, 3)], (40, 40))
    cut = fit_recap(STORY, draft, _budget(25), None, None)
    assert [beat.kind for beat in cut.beats] == ["opener", "point", "point", "closer"]
    fixed = apply_recap_fixes(STORY, cut, [RecapFixIn(2, "drop_beat"), RecapFixIn(3, "drop_beat")], _budget(25))
    assert [beat.kind for beat in fixed.beats] == ["opener", "point", "closer"]


def test_trailer_ignores_beats_with_no_units():
    beats = [TrailerBeatIn("cold_open", -1, -1), TrailerBeatIn("context", 3, 3), TrailerBeatIn("button", 11, 11)]
    cut = fit_trailer(CLAUSES, beats, set(), _budget(10))
    assert [(beat.first, beat.last) for beat in cut.beats] == [(3, 3), (11, 11)]


def test_recap_shrink_always_keeps_one_point():
    # Only important (2) and nice-to-have (3) points, long cores, a tight window.
    points = [_point("a", 5, 9, 5, 9, 2), _point("b", 20, 24, 20, 24, 3)]
    cut = fit_recap(STORY, RecapDraftIn("t", (50, 50), points, None), _budget(28), None, None)
    kinds = [beat.kind for beat in cut.beats]
    assert kinds == ["opener", "point"]
    assert cut.beats[1].label == "a"
    # Too tight for the last point: refused, never shipped as opener + closer alone.
    with pytest.raises(WorkflowError):
        fit_recap(STORY, RecapDraftIn("t", (50, 50), [_point("a", 5, 9, 5, 9, 2)], (55, 55)), _budget(20), None, None)
