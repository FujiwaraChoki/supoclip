from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import ai
from src.clip_duration import (
    normalize_clip_count,
    normalize_clip_instructions,
    validate_clip_count,
    validate_clip_instructions,
)
from tests.unit.test_ai_windowed_selection import _label, _segment, _transcript
from tests.unit.test_task_service import build_task_service


def test_clip_count_validation():
    assert validate_clip_count("auto") == "auto"
    assert [validate_clip_count(v) for v in (1, 7, 20)] == [1, 7, 20]
    for bad in (0, 21, -1, "7", "junk", None, True, 2.5):
        with pytest.raises(ValueError):
            validate_clip_count(bad)
        assert normalize_clip_count(bad) == "auto"


def test_clip_instructions_normalization_and_validation():
    assert normalize_clip_instructions("  focus   on\n the   funny  parts ") == "focus on the funny parts"
    assert normalize_clip_instructions("   ") is None
    assert normalize_clip_instructions(None) is None
    assert normalize_clip_instructions(5) is None
    assert validate_clip_instructions(None) is None
    assert validate_clip_instructions(" hook   first ") == "hook first"
    assert validate_clip_instructions("x" * 500) == "x" * 500
    for bad in ("x" * 501, 5, ["a"]):
        with pytest.raises(ValueError):
            validate_clip_instructions(bad)


def test_default_prompt_is_unchanged_and_custom_prompt_has_count_and_instructions():
    base = ai.build_transcript_analysis_prompt("[00:00 - 00:10] hi there friend")
    explicit = ai.build_transcript_analysis_prompt(
        "[00:00 - 00:10] hi there friend", clip_instructions=None, clip_count="auto"
    )
    assert base == explicit
    assert "Creator instructions" not in base
    assert "Choose 2-5 segments total." in base

    custom = ai.build_transcript_analysis_prompt(
        "[00:00 - 00:10] hi there friend", clip_instructions="Only funny moments", clip_count=6
    )
    assert "Creator instructions (follow when choosing and ranking segments" in custom
    assert '"Only funny moments"' in custom
    assert "Choose 6 segments total." in custom
    assert "6 final clips" in custom

    windowed = ai.build_transcript_analysis_prompt(
        "[00:00 - 00:10] hi", window=(0, 720), clip_count=10, window_count=3
    )
    assert "Choose 6 segments total." in windowed  # ceil(10 / 3) + 2


def test_rerank_prompt_carries_instructions():
    candidates = [_segment("00:00", "00:30"), _segment("00:40", "01:10")]
    plain = ai.build_candidate_rerank_prompt(candidates)
    assert plain == ai.build_candidate_rerank_prompt(candidates, None, None)
    assert '"Prefer advice"' in ai.build_candidate_rerank_prompt(candidates, None, "Prefer advice")


def _agent_with(segments):
    return SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                output=ai.TranscriptAnalysis(most_relevant_segments=segments, summary="s", key_topics=[])
            )
        )
    )


def _many_segments(n):
    return [_segment(_label(i * 40 + 10), _label(i * 40 + 40), total=90 - i) for i in range(n)]


async def test_clip_count_selects_exactly_n_and_auto_cap_is_unchanged(monkeypatch):
    transcript = _transcript(600)
    monkeypatch.setattr(ai, "get_transcript_agent", lambda clip_length="auto": _agent_with(_many_segments(12)))

    auto = await ai.get_most_relevant_parts_by_transcript(transcript)
    assert len(auto.most_relevant_segments) == ai.max_final_clips_for_duration(600, ai.get_config().max_clips)

    for wanted in (2, 9):
        result = await ai.get_most_relevant_parts_by_transcript(transcript, clip_count=wanted)
        assert len(result.most_relevant_segments) == wanted

    reserve = await ai.get_most_relevant_parts_by_transcript(transcript, clip_count=3, include_reserve=True)
    assert len(reserve.most_relevant_segments) == 6

    agent = _agent_with(_many_segments(12))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda clip_length="auto": agent)
    await ai.get_most_relevant_parts_by_transcript(transcript, clip_count=9, clip_instructions="x y")
    assert "Choose 9 segments total." in agent.run.await_args.args[0]
    assert '"x y"' in agent.run.await_args.args[0]


async def test_clip_count_fills_from_overlapping_candidates(monkeypatch):
    overlapping = [
        _segment("00:10", "00:40", total=90),
        _segment("00:20", "00:50", total=80),
        _segment("00:30", "01:00", total=70),
    ]
    monkeypatch.setattr(ai, "get_transcript_agent", lambda clip_length="auto": _agent_with(overlapping))

    result = await ai.get_most_relevant_parts_by_transcript(_transcript(600), clip_count=3)

    assert len(result.most_relevant_segments) == 3


@pytest.mark.parametrize("metadata", [{"clip_instructions": "funny"}, {"clip_count": 3}])
async def test_custom_selection_bypasses_shared_analysis_cache(metadata):
    service = build_task_service()
    service.cache_repo.get_cache = AsyncMock(
        return_value={"transcript_text": "Transcript", "analysis_json": "{}"}
    )
    service.task_repo.load_source_metadata = AsyncMock(return_value=metadata)

    await service.process_task(task_id="task-1", url="upload://test.mp4", source_type="upload", user_id="user-1")

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["cached_analysis_json"] is None
    assert kwargs["cached_transcript"] == "Transcript"
    for key, value in metadata.items():
        assert kwargs[key] == value
    assert service.cache_repo.upsert_cache.await_args.kwargs["analysis_json"] is None


async def test_default_tasks_keep_the_shared_cache_and_pass_no_extra_options():
    service = build_task_service()
    service.cache_repo.get_cache = AsyncMock(
        return_value={"transcript_text": "Transcript", "analysis_json": "{}"}
    )
    service.task_repo.load_source_metadata = AsyncMock(return_value={})

    await service.process_task(task_id="task-1", url="upload://test.mp4", source_type="upload", user_id="user-1")

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["cached_analysis_json"] == "{}"
    assert "clip_count" not in kwargs and "clip_instructions" not in kwargs
