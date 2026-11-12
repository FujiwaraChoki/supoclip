from types import SimpleNamespace

from src.ai import TranscriptAnalysis, _build_transcript_model, _get_missing_llm_key_error
from pydantic_ai.models.openai import OpenAIChatModel


def test_antigravity_uses_openai_compatible_provider():
    config = SimpleNamespace(
        llm="antigravity:available-model",
        antigravity_base_url="http://antigravity-manager:8045/v1",
        antigravity_api_key="test-key",
    )
    model = _build_transcript_model(config)
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "available-model"
    assert _get_missing_llm_key_error(config.llm, config) is None


def test_antigravity_requires_endpoint_and_api_key():
    config = SimpleNamespace(
        antigravity_base_url="",
        antigravity_api_key="",
    )
    assert "ANTIGRAVITY_BASE_URL and ANTIGRAVITY_API_KEY" in _get_missing_llm_key_error(
        "antigravity:model-id", config
    )


from src.ai import TranscriptAnalysis


def test_transcript_segment_accepts_minimal_local_llm_shape():
    analysis = TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:15",
                    "segment": "This is a standalone clip candidate from a local model.",
                }
            ],
            "summary": "A short summary.",
            "key_topics": ["local model output"],
        }
    )

    segment = analysis.most_relevant_segments[0]

    assert segment.text == "This is a standalone clip candidate from a local model."
    assert segment.relevance_score == 0.75
    assert segment.reasoning == "Selected by the AI model as a clip candidate."
    assert segment.virality.total_score == 60


def test_transcript_analysis_ignores_legacy_broll_opportunities():
    # Older prompts and cached analyses still carry this key; clip B-roll is
    # now planned per rendered clip, so it must not break validation.
    analysis = TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:15",
                    "text": "This clip has enough words to pass the text validation.",
                }
            ],
            "summary": "A short summary.",
            "key_topics": ["local model output"],
            "broll_opportunities": [{"broll": ["programming tutorial channels"]}],
        }
    )

    assert len(analysis.most_relevant_segments) == 1
    assert not hasattr(analysis, "broll_opportunities")
