"""Deterministic adversarial plans: no model calls or external footage requests."""

import json
import random

import pytest
from pydantic import ValidationError

from src.editor_document import EditDocument
from src.services.ai_edit_service import EditPlan, apply_plan


def document(**changes):
    return EditDocument.model_validate({
        "segments": [{"id": "s", "start": 0, "end": 10}],
        "words": [{"id": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.3, "text": f"word{i}"}
                  for i in range(16)],
        **changes,
    })


def plan(*operations):
    return EditPlan.model_validate({"summary": "Stress case", "operations": operations})


async def test_cut_limit_never_silently_discards_the_tail():
    draft = document(segments=[{"id": f"s{i}", "start": i, "end": i + 0.8} for i in range(100)], words=[])
    result, notes = await apply_plan(draft, plan(
        {"type": "cut_ranges", "ranges": [{"start": 0.2, "end": 0.4}]},
        {"type": "set", "setting": "audio.muted", "value": "true"},
    ), 100)
    assert result.segments == draft.segments
    assert result.muted is True
    assert any("100" in note for note in notes)


async def test_oversized_keep_ranges_is_skipped_instead_of_truncated():
    draft = document(words=[])
    result, notes = await apply_plan(draft, plan({
        "type": "keep_ranges", "ranges": [{"start": 0, "end": 1}] * 101,
    }), 10)
    assert result == draft
    assert any("100" in note for note in notes)


async def test_overlapping_captions_are_not_mistaken_for_silence():
    draft = document(words=[
        {"id": "long", "start": 0, "end": 5, "text": "long"},
        {"id": "overlap", "start": 0.5, "end": 0.8, "text": "overlap"},
        {"id": "later", "start": 3, "end": 3.2, "text": "later"},
    ])
    result, _ = await apply_plan(draft, plan({"type": "remove_pauses", "min_gap": 0.3, "keep": 0.1}), 10)
    assert result.segments == draft.segments


@pytest.mark.parametrize("start,end", [(1, 1.04), (0.26, 0.3)])
async def test_removing_fillers_does_not_cut_a_short_retained_word_between_them(start, end):
    draft = document(words=[
        {"id": "um1", "start": 0.1, "end": start, "text": "um"},
        {"id": "keep", "start": start, "end": end, "text": "I"},
        {"id": "um2", "start": end, "end": 1.5, "text": "um"},
        {"id": "later", "start": 2, "end": 3, "text": "continue"},
    ])
    result, _ = await apply_plan(draft, plan({"type": "remove_words", "word_ids": ["um1", "um2"]}), 10)
    assert any(s.start <= start and s.end >= end for s in result.segments)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_plan_rejects_nonfinite_timing(value):
    with pytest.raises(ValidationError):
        plan({"type": "keep_ranges", "ranges": [{"start": value, "end": 2}]})


@pytest.mark.parametrize("seed", range(12))
async def test_seeded_plans_preserve_valid_documents_and_do_not_mutate_inputs(seed):
    rng = random.Random(seed)
    draft = document()
    cases = []
    for _ in range(100):
        a, b = rng.choice([-1000, 0, 0.96, 1, 1.04, 9.96, 10, 1000]), rng.uniform(-10, 20)
        cases.extend([
            {"type": "keep_ranges", "ranges": [{"start": a, "end": b}]},
            {"type": "cut_ranges", "ranges": [{"start": b, "end": a}]},
            {"type": "remove_words", "word_ids": rng.choices(["w0", "w2", "w15", "missing"], k=3)},
            {"type": "remove_pauses", "min_gap": rng.uniform(-1, 5), "keep": rng.uniform(-1, 2)},
            {"type": "set", "setting": rng.choice(["captions.size", "audio.volume", "framing.zoom", "effects.hue"]),
             "value": rng.choice(["NaN", "Infinity", "not a number", "-9999", "9999", "0", "100%"])}
        ])
    for operation in cases:
        before = draft.model_dump_json()
        result, _ = await apply_plan(draft, plan(operation), 10)
        result.validate_duration(10)
        assert len(result.segments) <= 100
        assert sum(s.end - s.start for s in result.segments) >= 0.04 - 1e-9
        assert draft.model_dump_json() == before
        draft = result


async def test_unicode_caption_text_stays_bounded_and_unknown_ids_are_ignored():
    draft = document()
    result, _ = await apply_plan(draft, plan(
        {"type": "edit_words", "edits": [
            {"word_id": "w0", "text": "  café\n東京🙂 " * 100},
            {"word_id": "missing", "text": "injected"},
        ]},
        {"type": "highlight_words", "word_ids": ["w0", "missing", "w0"], "highlight": True},
    ), 10)
    assert len(result.words[0].text) == 120
    assert result.words[0].highlight is True
    assert result.words[1:] == draft.words[1:]
    json.dumps(result.model_dump(), allow_nan=False)


async def test_broll_limit_skips_search_and_keeps_other_operations(monkeypatch):
    from unittest.mock import AsyncMock

    search = AsyncMock(side_effect=AssertionError("No footage search at the limit"))
    monkeypatch.setattr("src.broll.search_pexels_videos", search)
    footage = {"id": 1, "width": 1080, "height": 1920, "duration": 20,
               "fileUrl": "https://videos.pexels.com/1.mp4"}
    draft = document(segments=[{"id": "s", "start": 0, "end": 100}], words=[], broll=[
        {"id": str(i), "start": i, "end": i + 0.5, "footage": footage} for i in range(60)
    ])
    result, notes = await apply_plan(draft, plan(
        {"type": "add_broll", "start": 80, "end": 83, "query": "city"},
        {"type": "set", "setting": "audio.muted", "value": "true"},
    ), 100)
    assert result.broll == draft.broll
    assert result.muted
    assert any("60" in note for note in notes)
    search.assert_not_awaited()


async def test_minimum_length_broll_move_is_not_rejected_by_binary_rounding():
    draft = document(broll=[{"id": "b", "start": 0, "end": 1, "footage": {
        "id": 1, "width": 1080, "height": 1920, "duration": 10,
        "fileUrl": "https://videos.pexels.com/1.mp4",
    }}])
    result, notes = await apply_plan(draft, plan({"type": "move_broll", "id": "b", "start": 1, "end": 1.2}), 10)
    assert (result.broll[0].start, result.broll[0].end) == (1, 1.2)
    assert notes == []


async def test_one_thousand_broll_moves_at_source_and_footage_boundaries():
    rng = random.Random(273)
    draft = document(broll=[{"id": "b", "start": 0, "end": 0.3, "offset": 0.7, "footage": {
        "id": 1, "width": 1080, "height": 1920, "duration": 1,
        "fileUrl": "https://videos.pexels.com/1.mp4",
    }}])
    for _ in range(1000):
        start = rng.choice([0, 1, 9.7, 9.8, 9.9, 10, -1, 100, rng.uniform(0, 10)])
        end = rng.choice([start + 0.2, start + 0.3, 10, -1, 100])
        result, _ = await apply_plan(draft, plan({"type": "move_broll", "id": "b", "start": start, "end": end}), 10)
        result.validate_duration(10)
        b = result.broll[0]
        assert b.end <= 10 + 1e-9
        # A newly moved span may round by at most a millisecond.
        if result != draft:
            assert b.end - b.start + b.offset <= b.footage.duration + 0.001
        draft = result
