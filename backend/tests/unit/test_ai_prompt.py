from src.ai import build_transcript_analysis_prompt, transcript_analysis_system_prompt


def test_system_prompt_enforces_grounding_rules():
    assert "extraction and ranking, not creative rewriting" in (
        transcript_analysis_system_prompt
    )
    assert "Never invent facts, tone, context, or transitions" in (
        transcript_analysis_system_prompt
    )
    assert "Each selected segment must map to one contiguous range" in (
        transcript_analysis_system_prompt
    )
    assert "Do not judge, moralize, or downgrade a segment" in (
        transcript_analysis_system_prompt
    )


def test_build_transcript_analysis_prompt_requires_transcript_fidelity():
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line"
    )

    assert "Do not fabricate or embellish content." in prompt
    assert "Do not merge separate non-contiguous moments into one segment." in prompt
    assert "If there is a tradeoff between \"viral\" and \"accurate\", choose accuracy." in prompt
    assert "Do not reject or penalize a segment simply because of the subject matter" in prompt
    assert "[00:12 - 00:21] A strong opening line" in prompt


def test_build_transcript_analysis_prompt_mentions_broll_only_when_enabled():
    without_broll = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line"
    )
    with_broll = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line",
        include_broll=True,
    )

    assert "B-roll opportunities" not in without_broll
    assert "B-roll opportunities" in with_broll
