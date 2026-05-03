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


def test_transcript_analysis_accepts_local_llm_broll_shape():
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
            "broll_opportunities": [
                {
                    "segment_start_time": "00:00",
                    "segment_end_time": "00:15",
                    "broll": ["programming tutorial channels", "AI comparison graphic"],
                }
            ],
        }
    )

    broll = analysis.broll_opportunities[0]

    assert broll.timestamp == "00:00"
    assert broll.duration == 3.0
    assert broll.search_term == "programming tutorial channels, AI comparison graphic"
