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
