from types import SimpleNamespace

from pydantic_ai.models.ollama import OllamaModel

from src.ai import (
    IDEAL_CLIP_MAX_SECONDS,
    IDEAL_CLIP_MIN_SECONDS,
    MIN_ACCEPTED_CLIP_SECONDS,
    TranscriptSegment,
    _build_transcript_model,
    _choose_repaired_bounds,
    _extract_transcript_text,
    _format_transcript_timestamp,
    _get_missing_llm_key_error,
    _parse_transcript_spans,
    _parse_transcript_timestamp_seconds,
    build_transcript_analysis_prompt,
    build_transcript_system_prompt,
)


def test_system_prompt_enforces_grounding_rules():
    transcript_analysis_system_prompt = build_transcript_system_prompt()
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
    assert f"{IDEAL_CLIP_MIN_SECONDS}-{IDEAL_CLIP_MAX_SECONDS} seconds" in (
        transcript_analysis_system_prompt
    )
    assert "Bad picks include intros" in transcript_analysis_system_prompt
    assert "The clip's real first sentence is the hook" in (
        transcript_analysis_system_prompt
    )
    assert "bare agreement" in transcript_analysis_system_prompt
    assert "Return valid JSON only" in transcript_analysis_system_prompt
    assert "Do not use \"segment\" as an output field. Use \"text\"." in (
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
    assert f"Most selected clips should be {IDEAL_CLIP_MIN_SECONDS}-{IDEAL_CLIP_MAX_SECONDS} seconds." in prompt
    assert "viewer would understand and care without seeing the rest" in prompt
    assert "first word of a self-contained sentence" in prompt
    assert "Return one valid JSON object and nothing else." in prompt
    assert "No Markdown, headings, bullets, code fences" in prompt
    assert "[00:12 - 00:21] A strong opening line" in prompt


def test_clip_selection_prompts_never_ask_for_broll():
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line"
    )

    assert "b-roll" not in prompt.lower()
    assert "broll" not in prompt.lower()
    assert "b-roll" not in build_transcript_system_prompt("auto").lower()


def test_ollama_llm_builds_native_ollama_model():
    runtime_config = SimpleNamespace(
        llm="ollama:gpt-oss:20b",
        ollama_api_key=None,
        resolve_ollama_base_url=lambda: "http://ollama.example/v1",
    )

    model = _build_transcript_model(runtime_config)

    assert isinstance(model, OllamaModel)
    assert model.model_name == "gpt-oss:20b"
    assert model.base_url == "http://ollama.example/v1/"


def test_parse_transcript_timestamp_supports_minute_and_hour_formats():
    assert _parse_transcript_timestamp_seconds("02:35") == 155
    assert _parse_transcript_timestamp_seconds("01:02:35") == 3755
    assert _format_transcript_timestamp(155) == "02:35"
    assert _format_transcript_timestamp(3755) == "01:02:35"
    assert MIN_ACCEPTED_CLIP_SECONDS == 15


def test_transcript_span_helpers_repair_near_miss_durations():
    spans = _parse_transcript_spans(
        "\n".join(
            [
                "[00:00 - 00:10] Setup",
                "[00:10 - 00:24] Short highlight",
                "[00:24 - 00:36] Payoff",
                "[00:36 - 01:20] Too much background",
            ]
        )
    )

    assert _extract_transcript_text(spans, 10, 36) == "Short highlight Payoff"
    assert _choose_repaired_bounds(spans, 10, 24) == (10, 36)
    assert _choose_repaired_bounds(spans, 0, 80) == (0, 36)


def test_transcript_segment_normalizes_percent_relevance_score():
    segment = TranscriptSegment(
        start_time="00:00",
        end_time="00:30",
        text="A complete standalone moment with useful context.",
        relevance_score=100,
    )

    assert segment.relevance_score == 1.0


def test_llm_validation_rejects_unsupported_or_incomplete_model_names():
    runtime_config = SimpleNamespace(
        google_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
    )

    assert "Unsupported LLM provider" in _get_missing_llm_key_error(
        "local:model", runtime_config
    )
    assert "missing a model name" in _get_missing_llm_key_error(
        "ollama:", runtime_config
    )
    assert _get_missing_llm_key_error("ollama:gpt-oss:20b", runtime_config) is None


# -- highlights with instructions (workflows) ---------------------------------------

import hashlib  # noqa: E402

import pytest  # noqa: E402

from src import ai as ai_module  # noqa: E402
from src.ai import (  # noqa: E402
    ViralityAnalysis,
    build_candidate_rerank_prompt,
)

GOLDEN_TRANSCRIPT = (
    "[00:00 - 00:09] Speaker A: We almost ran out of money.\n"
    "[00:09 - 00:21] Speaker B: What happened next?"
)


def _golden_candidates():
    return [
        TranscriptSegment(
            start_time="00:00", end_time="00:21", text="We almost ran out of money.",
            hook_title="Out of money", virality=ViralityAnalysis(),
        ),
        TranscriptSegment(start_time="01:00", end_time="01:30", text="Then the bank called."),
    ]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_prompts_without_instructions_are_byte_identical_to_before_workflows():
    # Hashes of the prompts as built before instructions existed (invariant I1).
    assert _sha(build_transcript_analysis_prompt(transcript=GOLDEN_TRANSCRIPT)) == (
        "5bb0569392010fae1b4649912ce1f22e15bb5a39f3d26d7abe52b871f51d43cb"
    )
    assert _sha(
        build_transcript_analysis_prompt(
            transcript=GOLDEN_TRANSCRIPT, clip_signals="- [00:03 - 00:05] laugh", window=(0, 720),
            performance_context="hook_type question wins", clip_length="short", instructions=None,
        )
    ) == "f4aed4bf309e08ed8beeb6a111793d4ec5f55aeb1644349758fddfc5f96eb337"
    assert _sha(build_candidate_rerank_prompt(_golden_candidates())) == (
        "dbc69a7497e6093a8590e8abb3d92a81de31131534c70eb7a269f792c9194a8c"
    )
    assert _sha(build_candidate_rerank_prompt(_golden_candidates(), "Clips under 30s do best.", instructions="")) == (
        "606d1691948d6367129ebcfee04d3b3c62d4dcaf04dd8406cb44d8bb64f6cd45"
    )


def test_instructions_go_into_the_user_prompt_just_before_the_transcript():
    plain = build_transcript_analysis_prompt(transcript=GOLDEN_TRANSCRIPT)
    steered = build_transcript_analysis_prompt(transcript=GOLDEN_TRANSCRIPT, instructions="Only the money story")

    block = "<creator_instructions>\nOnly the money story\n</creator_instructions>"
    assert block in steered and block not in plain
    assert steered.index(block) < steered.index("Transcript:\n[00:00 - 00:09]")
    assert steered.replace(steered[steered.index(block): steered.index("Transcript:")], "") == plain
    assert "Only the money story" not in build_transcript_system_prompt("auto")


def test_rerank_prompt_ends_with_the_instructions():
    prompt = build_candidate_rerank_prompt(_golden_candidates(), "Clips under 30s do best.", instructions="Funny ones first")
    assert prompt.index("Creator performance history") < prompt.index("<creator_instructions>\nFunny ones first")
    assert prompt.startswith(build_candidate_rerank_prompt(_golden_candidates(), "Clips under 30s do best."))


def _windowed_transcript(minutes: int) -> str:
    lines = []
    for start in range(0, minutes * 60, 10):
        lines.append(
            f"[{start // 60:02d}:{start % 60:02d} - {(start + 10) // 60:02d}:{(start + 10) % 60:02d}] "
            f"Speaker A: Line at {start} seconds is a complete sentence."
        )
    return "\n".join(lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("instructions", [None, "Focus on the bank"])
async def test_analysis_forwards_instructions_to_every_window_and_the_rerank(monkeypatch, instructions):
    prompts = []

    async def fake_run(agent, prompt, timeout_seconds=None):
        prompts.append(prompt)
        return SimpleNamespace(output=ai_module.TranscriptAnalysis(most_relevant_segments=[], summary="", key_topics=[]))

    rerank_calls = []

    async def fake_rerank(candidates, performance_context=None, **kwargs):
        rerank_calls.append(kwargs)
        return candidates

    monkeypatch.setattr(ai_module, "get_transcript_agent", lambda clip_length: object())
    monkeypatch.setattr(ai_module, "_run_transcript_analysis", fake_run)
    monkeypatch.setattr(ai_module, "rerank_candidates", fake_rerank)
    monkeypatch.setattr(ai_module, "get_config", lambda: SimpleNamespace(max_clips=10))

    kwargs = {"instructions": instructions} if instructions else {}
    await ai_module.get_most_relevant_parts_by_transcript(_windowed_transcript(30), **kwargs)

    assert len(prompts) == 3
    for prompt in prompts:
        assert ("<creator_instructions>\nFocus on the bank" in prompt) == bool(instructions)
    assert rerank_calls == [kwargs]
