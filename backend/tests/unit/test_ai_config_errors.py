"""Tests for ``src/ai.py`` config-error helper and prompt builder."""

import pytest

from src import ai as ai_module
from src.ai import (
    BRollOpportunity,
    TranscriptAnalysis,
    TranscriptSegment,
    ViralityAnalysis,
    _get_missing_llm_key_error,
    build_transcript_analysis_prompt,
)
from src.ai_prompt_defaults import BROLL_INSTRUCTION_SUFFIX, TRANSCRIPT_USER_TEMPLATE_DEFAULT


class TestGetMissingLlmKeyError:
    def test_google_without_key(self, monkeypatch):
        monkeypatch.setattr(ai_module.config, "google_api_key", None)
        err = _get_missing_llm_key_error("google-gla:gemini-3-flash-preview")
        assert err is not None
        assert "GOOGLE_API_KEY" in err

    def test_google_alias(self, monkeypatch):
        monkeypatch.setattr(ai_module.config, "google_api_key", None)
        assert _get_missing_llm_key_error("google:gemini-2.5-pro") is not None

    def test_google_with_key_ok(self, monkeypatch):
        monkeypatch.setattr(ai_module.config, "google_api_key", "present")
        assert _get_missing_llm_key_error("google-gla:gemini-2.5-pro") is None

    def test_openai_without_key(self, monkeypatch):
        monkeypatch.setattr(ai_module.config, "openai_api_key", None)
        err = _get_missing_llm_key_error("openai:gpt-4o-mini")
        assert err is not None
        assert "OPENAI_API_KEY" in err

    def test_openai_with_key_ok(self, monkeypatch):
        monkeypatch.setattr(ai_module.config, "openai_api_key", "present")
        assert _get_missing_llm_key_error("openai:gpt-4o-mini") is None

    def test_anthropic_without_key(self, monkeypatch):
        monkeypatch.setattr(ai_module.config, "anthropic_api_key", None)
        err = _get_missing_llm_key_error("anthropic:claude-sonnet-4-5")
        assert err is not None
        assert "ANTHROPIC_API_KEY" in err

    def test_ollama_no_key_is_fine(self):
        assert _get_missing_llm_key_error("ollama:llama3.2") is None

    def test_unknown_provider_silently_allowed(self):
        assert _get_missing_llm_key_error("unknownprovider:model") is None


class TestBuildTranscriptAnalysisPrompt:
    def test_basic_substitution(self):
        result = build_transcript_analysis_prompt("00:00 hello world", include_broll=False)
        assert "00:00 hello world" in result
        # With no broll, the broll instruction suffix must NOT be there
        assert BROLL_INSTRUCTION_SUFFIX not in result

    def test_broll_appended(self):
        result = build_transcript_analysis_prompt("t", include_broll=True)
        assert BROLL_INSTRUCTION_SUFFIX in result

    def test_custom_template(self):
        template = "TRANSCRIPT={transcript}|BROLL={broll_instruction}"
        result = build_transcript_analysis_prompt(
            "abc", include_broll=False, template=template
        )
        assert result == "TRANSCRIPT=abc|BROLL="

    def test_custom_template_with_broll(self):
        template = "T:{transcript}\nB:{broll_instruction}"
        result = build_transcript_analysis_prompt(
            "doc", include_broll=True, template=template
        )
        assert "T:doc" in result
        assert BROLL_INSTRUCTION_SUFFIX in result


class TestPydanticModels:
    def test_virality_analysis_valid(self):
        va = ViralityAnalysis(
            hook_score=10,
            engagement_score=15,
            value_score=20,
            shareability_score=5,
            total_score=50,
            hook_type="question",
            virality_reasoning="ok",
        )
        assert va.total_score == 50

    @pytest.mark.parametrize("field,value", [
        ("hook_score", -1),
        ("hook_score", 26),
        ("engagement_score", 26),
        ("total_score", 101),
    ])
    def test_virality_rejects_out_of_range(self, field, value):
        data = dict(
            hook_score=10,
            engagement_score=10,
            value_score=10,
            shareability_score=10,
            total_score=40,
            virality_reasoning="ok",
        )
        data[field] = value
        with pytest.raises(Exception):
            ViralityAnalysis(**data)

    def test_transcript_segment_valid(self):
        seg = TranscriptSegment(
            start_time="00:05",
            end_time="00:20",
            text="hello",
            relevance_score=0.8,
            reasoning="r",
            virality=ViralityAnalysis(
                hook_score=5, engagement_score=5, value_score=5, shareability_score=5,
                total_score=20, virality_reasoning="ok",
            ),
        )
        assert seg.start_time == "00:05"
        assert seg.relevance_score == 0.8

    def test_broll_opportunity_rejects_duration_bounds(self):
        # Duration ge=2.0 le=5.0
        with pytest.raises(Exception):
            BRollOpportunity(timestamp="00:05", duration=1.5, search_term="x", context="y")
        with pytest.raises(Exception):
            BRollOpportunity(timestamp="00:05", duration=6.0, search_term="x", context="y")

    def test_transcript_analysis_allows_empty_broll(self):
        analysis = TranscriptAnalysis(
            most_relevant_segments=[],
            summary="s",
            key_topics=["a"],
            broll_opportunities=None,
        )
        assert analysis.broll_opportunities is None
