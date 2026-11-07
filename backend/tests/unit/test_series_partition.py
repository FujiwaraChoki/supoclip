import pytest

from src.workflows.durations import Budget, part_budget
from src.workflows.errors import WorkflowError
from src.workflows.partition import (
    SKIP_MAX_SHARE,
    PartitionConstraints,
    partition_series,
    validate_skips,
)
from src.workflows.units import build_units

WORD = 0.3
WORD_GAP = 0.05


def _sentence(words, cursor, n_words, *, end=".", comma_at=None, prefix=None, speaker=None):
    for position in range(n_words):
        text = f"w{len(words)}"
        if prefix and position < len(prefix):
            text = prefix[position]
        if comma_at is not None and position == comma_at:
            text += ","
        if position == n_words - 1:
            text += end
        words.append({"text": text, "start": round(cursor * 1000), "end": round((cursor + WORD) * 1000), "speaker": speaker})
        cursor += WORD + WORD_GAP
    return cursor - WORD_GAP


def _story(sentences, *, gap=0.4):
    """Sentence specs -> sidecar. A spec is a word count or a dict of _sentence options."""
    words, cursor = [], 1.0
    for spec in sentences:
        options = {"n_words": spec} if isinstance(spec, int) else dict(spec)
        after = options.pop("gap", gap)
        cursor = _sentence(words, cursor, **options) + after
    return {"version": 2, "words": words, "utterances": [], "language_code": "en"}


def _index(sentences, **kwargs):
    return build_units(_story(sentences, **kwargs), "")


def _check_parts(index, result, span, budget, cleanup=None):
    parts = result.parts
    assert parts[0][0] == span[0]
    skipped = {uid for low, high in result.skips for uid in range(low, high + 1)}
    spine = [uid for uid in range(span[0], span[1] + 1) if uid not in skipped]
    for (_, left_last), (right_first, _) in zip(parts, parts[1:]):
        # Consecutive: the next part starts at the spine unit right after the previous end.
        assert spine[spine.index(left_last) + 1] == right_first
    for (first, last), part_skips, seconds in zip(parts, result.skips_by_part, result.seconds):
        assert budget.min <= index.speech_seconds(first, last, part_skips, cleanup) <= budget.max
        assert seconds == pytest.approx(index.speech_seconds(first, last, part_skips, cleanup), abs=0.01)
        assert all(first < low and high < last for low, high in part_skips)


# 14 words: 4.85 s of speech, ~5.25 s per sentence with the gap. Short parts
# (30-60 s, ideally 35-50) hold 6-11 sentences, ideally 7-9.
EVEN = [14] * 48


def test_parts_respect_the_budget_and_are_consecutive():
    index = _index(EVEN)
    budget = part_budget("short")
    span = (1, len(index))
    result = partition_series(index, span, [], budget, None, [], PartitionConstraints(), None)
    _check_parts(index, result, span, budget)
    assert result.parts[-1][1] == span[1]
    assert not result.relaxed and result.notes == []
    # Auto without model ends leans toward total / ideal length.
    total = index.speech_seconds(*span)
    assert len(result.parts) == round(total / budget.ideal)


def test_exact_part_count():
    index = _index(EVEN)
    budget = part_budget("medium")
    result = partition_series(index, (1, len(index)), [], budget, 3, [], PartitionConstraints(), None)
    assert len(result.parts) == 3
    _check_parts(index, result, (1, len(index)), budget)


def test_auto_count_follows_the_model():
    index = _index(EVEN)
    budget = part_budget("auto")  # 45-150 s, ideally 60-100
    three = partition_series(index, (1, 48), [], budget, None, [16, 32, 48], PartitionConstraints(), None)
    assert [last for _, last in three.parts] == [16, 32, 48]
    four = partition_series(index, (1, 48), [], budget, None, [12, 24, 36, 48], PartitionConstraints(), None)
    assert [last for _, last in four.parts] == [12, 24, 36, 48]
    # Without model ends: total / ideal (80 s) -> 3 parts.
    assert len(partition_series(index, (1, 48), [], budget, None, [], PartitionConstraints(), None).parts) == 3


def test_keeps_feasible_model_cuts_exactly():
    index = _index(EVEN)
    budget = part_budget("short")
    ends = [8, 17, 25, 33, 41, 48]
    result = partition_series(index, (1, 48), [], budget, 6, ends, PartitionConstraints(), None)
    assert [last for _, last in result.parts] == ends


def test_moves_a_mid_sentence_cut_to_the_nearest_strong_boundary():
    # Sentence 12 runs 45 words with a comma after word 20: it splits into a
    # clause unit (#12, weak) and its rest (#13).
    sentences = [14] * 11 + [{"n_words": 45, "comma_at": 19}] + [14] * 30
    index = _index(sentences)
    assert index.unit(12).end_kind == "clause" and index.boundary_strength(12) < 0.45
    budget = Budget(min=30, ideal_min=35, ideal_max=60, max=80, render_cap=85)
    result = partition_series(index, (1, len(index)), [], budget, None, [12, 24, len(index)], PartitionConstraints(), None)
    first_end = result.parts[0][1]
    assert first_end in (11, 13)
    assert index.boundary_strength(first_end) >= 0.75


def test_prefers_a_question_and_avoids_a_back_reference_opening():
    sentences = [14] * 10 + [{"n_words": 14, "end": "?"}] + [14] + [{"n_words": 14, "prefix": ["That's", "why"]}] + [14] * 30
    index = _index(sentences)
    assert index.unit(11).ends_question
    budget = Budget(min=30, ideal_min=35, ideal_max=70, max=80, render_cap=85)
    result = partition_series(index, (1, len(index)), [], budget, 4, [], PartitionConstraints(), None)
    ends = [last for _, last in result.parts]
    assert 11 in ends
    assert 12 not in ends  # the next part would open on "That's why"


def test_parts_end_on_chapter_ends_and_hooks_never_open_on_an_acknowledgement():
    # Chapters end at 15 and 30; sentence 22 points ahead; 9 is a bare "Thank you."
    sentences = [14] * 8 + [{"n_words": 14}] + [{"n_words": 2, "prefix": ["Thank", "you"]}] + [14] * 11 + [
        {"n_words": 14, "prefix": ["Number", "six", "is", "feedback"]}
    ] + [14] * 20
    index = _index(sentences)
    budget = Budget(min=20, ideal_min=30, ideal_max=60, max=90, render_cap=95)
    plain = partition_series(index, (1, len(index)), [], budget, 3, [], PartitionConstraints(), None)
    chaptered = partition_series(
        index, (1, len(index)), [], budget, 3, [], PartitionConstraints(), None, chapter_ends=[15, 30]
    )
    ends = [last for _, last in chaptered.parts]
    assert 15 in ends or 30 in ends
    assert 9 not in [last for _, last in plain.parts] + ends  # "Thank you." never opens a part


def test_over_long_parts_cost_more_than_short_ones():
    from src.workflows.partition import LONG_PART_COST, SHORT_PART_COST

    assert LONG_PART_COST > SHORT_PART_COST


def test_pinned_and_forbidden_ends():
    index = _index(EVEN)
    budget = part_budget("short")
    ends = [8, 17, 25, 33, 41, 48]
    base = partition_series(index, (1, 48), [], budget, 6, ends, PartitionConstraints(), None)
    assert base.parts[1][1] == 17
    moved = partition_series(
        index, (1, 48), [], budget, 6, ends,
        PartitionConstraints(pinned_ends={15}, forbidden_ends={17}), None,
    )
    moved_ends = [last for _, last in moved.parts]
    assert 15 in moved_ends and 17 not in moved_ends
    assert not moved.relaxed
    _check_parts(index, moved, (1, 48), budget)


def test_impossible_constraints_are_dropped_before_anything_else():
    index = _index(EVEN)
    budget = part_budget("short")
    # Pinning #2 would make part 1 far too short.
    result = partition_series(index, (1, 48), [], budget, 4, [], PartitionConstraints(pinned_ends={2}), None)
    assert result.relaxed
    assert 2 not in [last for _, last in result.parts]
    _check_parts(index, result, (1, 48), budget)


def test_skip_validation():
    index = _index(EVEN)
    span = (5, 40)
    skips = [
        (5, 7),  # contains the span's first unit
        (39, 44),  # contains its last
        (10, 10), (11, 12),  # touching: merged into 10..12
        (20, 20),  # ~5 s: kept
        (30, 29),  # reversed: swapped
    ]
    assert validate_skips(index, span, skips) == [(10, 12), (20, 20), (29, 30)]
    short = build_units(_story([14, 2, 14, 14]), "")
    assert short.speech_seconds(2, 2) < 3.0
    assert validate_skips(short, (1, 4), [(2, 2)]) == []


def test_skips_are_removed_from_parts_and_never_take_over_40_percent():
    index = _index(EVEN)
    budget = part_budget("short")
    result = partition_series(index, (1, 48), [(14, 16), (30, 30)], budget, None, [], PartitionConstraints(), None)
    _check_parts(index, result, (1, 48), budget)
    assert result.skips == [(14, 16), (30, 30)]
    heard = [uid for (first, last), skips in zip(result.parts, result.skips_by_part) for uid in range(first, last + 1)
             if not any(low <= uid <= high for low, high in skips)]
    assert not {14, 15, 16, 30} & set(heard)
    for (first, last), skips in zip(result.parts, result.skips_by_part):
        skipped = sum(index.cut_out[high] - index.cut_in[low] for low, high in skips)
        assert skipped / (index.cut_out[last] - index.cut_in[first]) <= SKIP_MAX_SHARE


def test_a_skip_no_part_can_hold_is_dropped():
    index = _index([14] * 12)  # ~63 s
    budget = Budget(min=18, ideal_min=25, ideal_max=35, max=45, render_cap=50)
    # Units 4-7 (~21 s) skipped: a part ending before them is too short, and
    # a part holding them is either over 40 % skipped or leaves too little
    # for the other part.
    result = partition_series(index, (1, 12), [(4, 7)], budget, 2, [], PartitionConstraints(), None)
    assert result.skips == [] and result.relaxed
    assert len(result.parts) == 2
    _check_parts(index, result, (1, 12), budget)


def test_a_skip_that_makes_the_story_too_short_is_dropped_before_giving_up():
    index = _index([14] * 18)  # ~94 s: two auto parts (45 s minimum) only without the skip
    budget = part_budget("auto")
    skip = (8, 9)  # ~10 s, a clean skip
    assert validate_skips(index, (1, 18), [skip]) == [skip]

    for ends in ([], [5, 18], [9, 18]):
        result = partition_series(index, (1, 18), [skip], budget, None, ends, PartitionConstraints(), None)
        assert len(result.parts) == 2 and result.skips == [] and result.relaxed
        _check_parts(index, result, (1, 18), budget)


def test_a_sponsor_read_is_never_dropped_even_when_it_dominates_a_part():
    # A 21 s cold open, a 63 s sponsor read, then the story. No part can end
    # before the read, and a part holding it is far over 40 % skipped; a
    # model skip would be dropped here, the sponsor read never is.
    index = _index([14] * 45)
    budget = part_budget("short")
    sponsor = (5, 16)

    result = partition_series(index, (1, 45), [], budget, None, [], PartitionConstraints(), None, forced_skips=[sponsor])

    assert result.skips == [sponsor]
    assert sponsor in result.skips_by_part[0]
    heard = {uid for (first, last), skips in zip(result.parts, result.skips_by_part) for uid in range(first, last + 1)
             if not any(low <= uid <= high for low, high in skips)}
    assert not heard & set(range(5, 17))
    _check_parts(index, result, (1, 45), budget)
    # The same stretch as a model skip is still dropped when nothing can hold it.
    assert partition_series(index, (1, 45), [sponsor], budget, None, [], PartitionConstraints(), None).skips == []


def test_a_model_skip_still_counts_toward_the_share_next_to_a_sponsor_read():
    index = _index([14] * 45)
    budget = part_budget("short")
    result = partition_series(
        index, (1, 45), [(30, 33)], budget, None, [], PartitionConstraints(), None, forced_skips=[(5, 16)]
    )
    assert (5, 16) in result.skips
    for (first, last), skips in zip(result.parts, result.skips_by_part):
        optional = [skip for skip in skips if skip != (5, 16)]
        skipped = sum(index.cut_out[high] - index.cut_in[low] for low, high in optional)
        assert skipped / (index.cut_out[last] - index.cut_in[first]) <= SKIP_MAX_SHARE


def test_too_little_story_after_the_sponsor_read_raises():
    index = _index([14] * 20)  # ~105 s, ~42 s of it a sponsor read
    with pytest.raises(WorkflowError) as raised:
        partition_series(
            index, (1, 20), [], part_budget("auto"), None, [], PartitionConstraints(), None, forced_skips=[(6, 13)]
        )
    assert raised.value.code == "source_too_short"


def test_weak_boundaries_only_relax_to_any_cut():
    # One 160-word monologue with no punctuation and no pauses: only forced splits.
    index = _index([{"n_words": 160, "end": ""}])
    assert all(index.boundary_strength(unit.id) < 0.45 for unit in index.units[:-1])
    budget = Budget(min=12, ideal_min=15, ideal_max=20, max=25, render_cap=30)
    result = partition_series(index, (1, len(index)), [], budget, None, [], PartitionConstraints(), None)
    assert result.relaxed
    _check_parts(index, result, (1, len(index)), budget)


def test_explicit_count_that_does_not_fit_is_reduced():
    index = _index([14] * 27)  # ~140 s
    budget = part_budget("short")  # 30-60 s
    result = partition_series(index, (1, 27), [], budget, 6, [], PartitionConstraints(), None)
    assert len(result.parts) == 4
    assert result.notes == [{"code": "PART_COUNT_REDUCED", "params": {"requested": 6, "actual": 4}}]
    assert result.relaxed
    _check_parts(index, result, (1, 27), budget)


def test_too_little_story_raises():
    index = _index([14] * 9)  # ~47 s
    with pytest.raises(WorkflowError) as raised:
        partition_series(index, (1, 9), [], part_budget("short"), None, [], PartitionConstraints(), None)
    assert raised.value.code == "source_too_short"
    seconds = raised.value.details["story_seconds"]
    assert f"about {seconds} seconds of story" in raised.value.message
    assert 40 <= seconds <= 55


def test_material_too_long_for_the_parts_ends_early_on_a_strong_cut():
    index = _index(EVEN)  # ~250 s
    budget = part_budget("short")
    result = partition_series(index, (1, 48), [], budget, 2, [], PartitionConstraints(), None)
    assert len(result.parts) == 2
    assert result.parts[-1][1] < 48
    assert index.boundary_strength(result.parts[-1][1]) >= 0.75
    assert result.notes[0]["code"] == "STORY_ARC_SELECTED"
    assert set(result.notes[0]["params"]) == {"start", "end"}
    _check_parts(index, result, (1, 48), budget)


def test_cleanup_shortens_parts_and_changes_the_solution():
    sentences = [{"n_words": 14, "gap": 2.5}] * 30
    index = _index(sentences)
    cleanup = {"cut_long_pauses": True, "pause_threshold_ms": 1200, "remove_filler_words": False, "filtered_words": []}
    budget = part_budget("short")
    plain = partition_series(index, (1, 30), [], budget, None, [], PartitionConstraints(), None)
    cleaned = partition_series(index, (1, 30), [], budget, None, [], PartitionConstraints(), cleanup)
    _check_parts(index, cleaned, (1, 30), budget, cleanup)
    assert len(cleaned.parts) < len(plain.parts)


def test_coarse_transcripts_partition_on_lines():
    lines = "\n".join(
        f"[{n * 6 // 60:02d}:{n * 6 % 60:02d} - {(n * 6 + 5) // 60:02d}:{(n * 6 + 5) % 60:02d}] Line number {n} ends here."
        for n in range(40)
    )
    index = build_units(None, lines)
    assert index.coarse
    budget = part_budget("short")
    result = partition_series(index, (1, 40), [], budget, 4, [], PartitionConstraints(), None)
    assert len(result.parts) == 4
    _check_parts(index, result, (1, 40), budget)


def test_span_is_clamped_and_ordered():
    index = _index(EVEN)
    result = partition_series(index, (60, 3), [], part_budget("short"), 2, [], PartitionConstraints(), None)
    assert result.parts[0][0] == 3


# -- round 2: real loops, clean openers, strong cuts ---------------------------------------------------


def _spoken(lines, *, gap=400, speakers=None):
    words, cursor = [], 1000
    for number, line in enumerate(lines):
        speaker = speakers[number] if speakers else "A"
        for token in line.split():
            words.append({"text": token, "start": cursor, "end": cursor + 300, "speaker": speaker})
            cursor += 350
        cursor += gap
    return build_units({"words": words, "language_code": "en"}, "")


FILLER = "We kept the shop open through the whole of that long winter season."


def test_a_real_question_beats_a_flat_model_end_nearby():
    from src.workflows.partition import partition_series

    lines = [FILLER] * 20 + ["And who pays for all of this in the end?"] + [FILLER] * 20
    lines[18] = "We kept the shop open for sure through that winter season."
    index = _spoken(lines)
    assert index.unit(21).ends_question
    budget = part_budget("auto")
    # The model ended on the flat line two sentences before the question.
    result = partition_series(index, (1, len(index)), [], budget, 2, [19, len(index)], None, None)
    assert result.parts[0][1] == 21


def test_the_hook_reads_past_a_listeners_reply():
    from src.workflows.partition import _Solver, PartitionConstraints, _Spine

    index = _spoken([FILLER, "But here's the thing about the rent.", "Right.", FILLER, FILLER], speakers="AABAA")
    solver = _Solver(index, (1, 5), part_budget("auto"), [], PartitionConstraints(), None)
    spine = _Spine(index, (1, 5), [], None)
    hooked = solver._cut_cost(spine, 2, False)
    flat = solver._cut_cost(spine, 3, False)
    assert hooked < flat


def test_a_back_reference_opener_costs_more_unless_it_answers_a_question():
    from src.workflows.partition import _Solver, PartitionConstraints, _Spine

    index = _spoken(
        [FILLER, "And here the vectors do not talk to each other at all.", FILLER, "Would you ever take that kind of offer?", "It's the only way I would."],
        speakers="AAABA",
    )
    solver = _Solver(index, (1, 5), part_budget("auto"), [], PartitionConstraints(), None)
    spine = _Spine(index, (1, 5), [], None)
    assert solver._cut_cost(spine, 0, False) > solver._cut_cost(spine, 2, False)
    # "It's..." right after someone else asked: a real answer, no penalty on that cut.
    assert solver._cut_cost(spine, 3, False) < solver._cut_cost(spine, 0, False)


def test_parts_break_at_a_jump_rather_than_play_across_it():
    from src.workflows.partition import partition_series

    index = _spoken([FILLER] * 60)
    budget = part_budget("auto")
    jump = (21, 30)
    result = partition_series(index, (1, 60), [], budget, None, [], None, None, forced_skips=[jump], jumps=[jump])
    assert 20 in [last for _, last in result.parts]


def test_a_part_never_ends_on_a_comma_while_anything_else_fits():
    from src.workflows.partition import partition_series, soft_end

    lines = [FILLER] * 12 + ["and that is why we kept on going,"] + [FILLER] * 12
    index = _spoken(lines, gap=900)
    comma = next(unit.id for unit in index.units if unit.text.endswith(","))
    assert soft_end(index, comma)
    assert not soft_end(index, 1)
    assert soft_end(_spoken(["We kept going and"]), 1)
    result = partition_series(index, (1, len(index)), [], part_budget("auto"), 2, [comma, len(index)], None, None)
    assert result.parts[0][1] != comma


def test_a_strong_cut_wins_over_a_pause_and_a_pause_is_the_next_rung():
    from src.workflows.partition import partition_series

    # Pauses but no punctuation at all: the usable rung, not "any word".
    words, cursor = [], 0
    for sentence in range(30):
        for position in range(40):
            words.append({"text": f"W{position}", "start": cursor, "end": cursor + 300, "speaker": "A"})
            cursor += 350
        cursor += 900
    index = build_units({"words": words, "language_code": "hi"}, "")
    result = partition_series(index, (1, len(index)), [], part_budget("auto"), 3, [], None, None)
    assert result.relaxed
    assert all(index.boundary_strength(last) >= 0.45 for _, last in result.parts[:-1])


def test_the_last_part_never_plays_as_a_montage():
    from src.workflows.partition import partition_series

    index = _spoken([FILLER] * 40)
    budget = part_budget("auto")
    # 30 % of what would be the last part is skipped: allowed in another part, not this one.
    tail_skip = (31, 34)
    result = partition_series(index, (1, 40), [tail_skip], budget, 2, [20, 40], None, None)
    if tail_skip in result.skips:
        assert result.parts[-1][1] - result.parts[-1][0] + 1 >= 16
    else:
        assert result.relaxed
