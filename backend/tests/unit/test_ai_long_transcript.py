from src.ai import (
    _parse_transcript_lines,
    _parse_transcript_spans,
    _extract_transcript_text_for_segment,
    _parse_transcript_timestamp_seconds,
    parse_timestamp_to_seconds,
    build_transcript_analysis_prompt,
)


def test_long_video_transcript_parsing():
    sample_long_transcript = """[00:00 - 00:30] Intro content
[88:27 - 89:17] Spoken moment around 1.5 hours
[127:49 - 128:39] Deep insight at 2 hours 7 minutes into the podcast
[02:15:00 - 02:15:45] Formatted with HH:MM:SS format
"""
    lines = _parse_transcript_lines(sample_long_transcript)
    assert len(lines) == 4
    assert lines[2]["start_time"] == "127:49"
    assert lines[2]["end_time"] == "128:39"
    assert "Deep insight at 2 hours" in lines[2]["text"]
    assert lines[3]["start_time"] == "02:15:00"

    spans = _parse_transcript_spans(sample_long_transcript)
    assert len(spans) == 4
    assert spans[2]["start"] == 127 * 60 + 49
    assert spans[2]["end"] == 128 * 60 + 39

    # Test text grounding
    grounded = _extract_transcript_text_for_segment(lines, "127:49", "128:39")
    assert grounded is not None
    assert "Deep insight at 2 hours 7 minutes into the podcast" in grounded


def test_timestamp_parsing_accuracy():
    assert _parse_transcript_timestamp_seconds("127:49") == 7669
    assert _parse_transcript_timestamp_seconds("02:07:49") == 7669
    assert parse_timestamp_to_seconds("127:49") == 7669.0
    assert parse_timestamp_to_seconds("02:07:49") == 7669.0


def test_dynamic_duration_prompt_generation():
    prompt = build_transcript_analysis_prompt(
        transcript="[00:00 - 01:00] Sample text",
        min_clip_duration=60,
    )
    assert "MUST be between 60 and 95 seconds" in prompt
    assert "Do not return segments shorter than 60 seconds or longer than 95 seconds" in prompt
