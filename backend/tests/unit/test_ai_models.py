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


def test_virality_rubric_clamps_and_normalizes_model_output():
    from src.ai import ViralityAnalysis, virality_breakdown_from_dump

    virality = ViralityAnalysis.model_validate(
        {
            "hook": 34,
            "narrative_arc": 21,
            "emotional_trigger": "17",
            "pacing": 12,
            "loop_potential": 8,
            "penalties_applied": 15,
            "hook_quote": "  Never do this.  ",
            "why_it_works": "Contrarian take.",
            "editor_fix": "Trim the first 2s.",
        }
    )

    assert virality.hook_score == 30
    assert virality.penalties_applied == -15
    assert virality.computed_total() == 30 + 21 + 17 + 12 + 8 - 15
    breakdown = virality_breakdown_from_dump(virality.model_dump())
    assert breakdown["hook_quote"] == "Never do this."
    assert breakdown["penalties_applied"] == -15
    assert virality_breakdown_from_dump({"hook_score": 20, "engagement_score": 20}) is None


def test_virality_penalties_cannot_push_total_below_zero():
    from src.ai import ViralityAnalysis

    virality = ViralityAnalysis(
        hook_score=5, narrative_arc_score=5, emotional_trigger_score=0,
        pacing_score=0, loop_score=0, penalties_applied=-50,
    )
    assert virality.computed_total() == 0
