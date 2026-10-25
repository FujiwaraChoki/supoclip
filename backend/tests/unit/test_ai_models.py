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
