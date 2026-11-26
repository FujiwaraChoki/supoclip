from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import ai
from src.ai import _extract_transcript_text_for_segment, _parse_transcript_lines


def test_extract_transcript_text_for_segment_strips_speaker_labels_and_joins_lines():
    transcript = "\n".join(
        [
            "[00:00 - 00:10] Speaker A: First sentence.",
            "[00:10 - 00:20] Speaker A: Second sentence.",
        ]
    )

    transcript_lines = _parse_transcript_lines(transcript)
    grounded_text = _extract_transcript_text_for_segment(
        transcript_lines,
        "00:00",
        "00:20",
    )

    assert grounded_text == "First sentence. Second sentence."


def test_extract_transcript_text_for_segment_rejects_non_boundary_timestamp():
    transcript = "[00:00 - 00:10] Speaker A: First sentence."

    transcript_lines = _parse_transcript_lines(transcript)

    assert (
        _extract_transcript_text_for_segment(transcript_lines, "00:05", "00:10")
        is None
    )


@pytest.mark.parametrize("preset,start,end", [("auto", 6, 20), ("medium", 2, 20)])
async def test_short_spoken_source_keeps_full_range_despite_silence_and_rounded_labels(
    monkeypatch, preset, start, end,
):
    transcript = (
        f"[00:{start:02d} - 00:13] The first cake collapsed when I opened the oven door.\n"
        f"[00:13 - 00:{end:02d}] On the second try I waited for the timer and it turned out fine."
    )
    segment = ai.TranscriptSegment(
        start_time="00:00", end_time="00:21",
        text="The first cake collapsed when I opened the oven door.",
        hook_title="Why the cake collapsed",
        virality=ai.ViralityAnalysis(hook_score=13, engagement_score=14, value_score=15, shareability_score=10),
    )
    analyzed = SimpleNamespace(output=ai.TranscriptAnalysis(
        most_relevant_segments=[segment], summary="A baking lesson", key_topics=["baking"],
    ))
    run = AsyncMock(return_value=analyzed)
    monkeypatch.setattr(ai, "get_transcript_agent", lambda _preset: object())
    monkeypatch.setattr(ai, "_run_transcript_analysis", run)

    result = await ai.get_most_relevant_parts_by_transcript(
        transcript, clip_length=preset, source_duration=21.2,
    )

    [selected] = result.most_relevant_segments
    assert (selected.start_time, selected.end_time) == (f"00:{start:02d}", f"00:{end:02d}")
    assert "second try" in selected.text
    assert selected.hook_title == "Why the cake collapsed"
    assert selected.virality.total_score == 52
    run.assert_awaited_once()


async def test_empty_short_source_analysis_retries_with_the_full_spoken_range(monkeypatch):
    transcript = "[00:01 - 00:21] I opened the oven too early and the cake sank in the middle."
    empty = ai.TranscriptAnalysis(most_relevant_segments=[], summary="", key_topics=[])
    selected = ai.TranscriptSegment(
        start_time="00:01", end_time="00:21", text="I opened the oven too early and the cake sank in the middle.",
        hook_title="Why the cake sank",
        virality=ai.ViralityAnalysis(hook_score=12, engagement_score=13, value_score=16, shareability_score=10),
    )
    retry = ai.TranscriptAnalysis(most_relevant_segments=[selected], summary="A baking mistake", key_topics=[])
    run = AsyncMock(side_effect=[SimpleNamespace(output=empty), SimpleNamespace(output=retry)])
    monkeypatch.setattr(ai, "get_transcript_agent", lambda _preset: object())
    monkeypatch.setattr(ai, "_run_transcript_analysis", run)

    result = await ai.get_most_relevant_parts_by_transcript(transcript, source_duration=22)

    assert run.await_count == 2
    assert "full spoken range (00:01 to 00:21)" in run.await_args_list[0].args[1]
    assert "Evaluate the full spoken range" in run.await_args_list[1].args[1]
    assert result.most_relevant_segments == [selected]
    assert selected.virality.total_score == 51


@pytest.mark.parametrize("transcript,duration", [("", 22), ("[00:00 - 00:18] Clear usable speech.", 90)])
async def test_silence_and_long_sources_do_not_trigger_the_short_source_retry(monkeypatch, transcript, duration):
    empty = ai.TranscriptAnalysis(most_relevant_segments=[], summary="", key_topics=[])
    run = AsyncMock(return_value=SimpleNamespace(output=empty))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda _preset: object())
    monkeypatch.setattr(ai, "_run_transcript_analysis", run)

    result = await ai.get_most_relevant_parts_by_transcript(transcript, source_duration=duration)

    assert result.most_relevant_segments == []
    run.assert_awaited_once()
    assert "source is already a short clip" not in run.await_args.args[1]
