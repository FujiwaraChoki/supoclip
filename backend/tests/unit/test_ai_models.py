from src.ai import TranscriptAnalysis


def test_transcript_segment_accepts_minimal_local_llm_shape():
    analysis = TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:15",
                    "segment": "Este é um candidato a clipe autônomo de um modelo local.",
                }
            ],
            "summary": "Um breve resumo.",
            "key_topics": ["saída do modelo local"],
        }
    )

    segment = analysis.most_relevant_segments[0]

    assert segment.text == "Este é um candidato a clipe autônomo de um modelo local."
    assert segment.relevance_score == 0.75
    assert segment.reasoning == "Selected by the AI model as a clip candidate."
    assert segment.virality.total_score == 50


def test_transcript_analysis_accepts_local_llm_broll_shape():
    analysis = TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:15",
                    "text": "Este clipe tem palavras suficientes para passar na validação de texto.",
                }
            ],
            "summary": "Um breve resumo.",
            "key_topics": ["saída do modelo local"],
            "broll_opportunities": [
                {
                    "segment_start_time": "00:00",
                    "segment_end_time": "00:15",
                    "broll": ["tutorial de programação", "gráfico de comparação de IA"],
                }
            ],
        }
    )

    broll = analysis.broll_opportunities[0]

    assert broll.timestamp == "00:00"
    assert broll.duration == 3.0
    assert broll.search_term == "tutorial de programação, gráfico de comparação de IA"


def test_rells_engine_scoring_fields():
    analysis = TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:30",
                    "text": "Um momento forte de testemunho que emociona o público.",
                    "virality": {
                        "hook_score": 8,
                        "retention_score": 7,
                        "emotion_score": 9,
                        "identification_score": 8,
                        "shareability_score": 7,
                        "comment_score": 6,
                        "save_score": 7,
                        "emotional_curve_score": 8,
                        "profile_compatibility_score": 18,
                        "sermon_fidelity_score": 9,
                        "total_score": 87,
                        "classification": "A",
                    },
                    "category": "testemunho",
                    "audience": "ansiosos",
                    "cover_title": "A cura que ninguém esperava",
                }
            ],
            "summary": "Testemunho de cura.",
            "key_topics": ["cura", "fé", "superação"],
        }
    )

    segment = analysis.most_relevant_segments[0]

    assert segment.virality.hook_score == 8
    assert segment.virality.retention_score == 7
    assert segment.virality.emotion_score == 9
    assert segment.virality.identification_score == 8
    assert segment.virality.shareability_score == 7
    assert segment.virality.comment_score == 6
    assert segment.virality.save_score == 7
    assert segment.virality.emotional_curve_score == 8
    assert segment.virality.profile_compatibility_score == 18
    assert segment.virality.sermon_fidelity_score == 9
    assert segment.virality.total_score == 87
    assert segment.virality.classification == "A"

    assert segment.category == "testemunho"
    assert segment.audience == "ansiosos"
    assert segment.cover_title == "A cura que ninguém esperava"
