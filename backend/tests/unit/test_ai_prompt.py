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
    transcript_analysis_system_prompt,
)


def test_system_prompt_enforces_rells_engine_pillars():
    assert "Fidelidade" in transcript_analysis_system_prompt
    assert "DNA do Perfil" in transcript_analysis_system_prompt
    assert "Leitura Total" in transcript_analysis_system_prompt
    assert "Mapeamento" in transcript_analysis_system_prompt
    assert "Público" in transcript_analysis_system_prompt
    assert "Retenção" in transcript_analysis_system_prompt
    assert "Curva Emocional" in transcript_analysis_system_prompt
    assert "Compartilhamento" in transcript_analysis_system_prompt
    assert "Comentários" in transcript_analysis_system_prompt
    assert "Salvamentos" in transcript_analysis_system_prompt


def test_system_prompt_contains_rells_scoring():
    assert "Gancho" in transcript_analysis_system_prompt
    assert "Retenção" in transcript_analysis_system_prompt
    assert "Emoção" in transcript_analysis_system_prompt
    assert "Identificação" in transcript_analysis_system_prompt
    assert "Compartilhamento" in transcript_analysis_system_prompt
    assert "Comentários" in transcript_analysis_system_prompt
    assert "Salvamentos" in transcript_analysis_system_prompt
    assert "Curva emocional" in transcript_analysis_system_prompt
    assert "Compatibilidade com o perfil" in transcript_analysis_system_prompt
    assert "Fidelidade ao sermão" in transcript_analysis_system_prompt


def test_system_prompt_contains_classifications():
    assert "S++" in transcript_analysis_system_prompt
    assert "S+" in transcript_analysis_system_prompt
    assert "98-100" in transcript_analysis_system_prompt
    assert "95-97" in transcript_analysis_system_prompt
    assert "90-94" in transcript_analysis_system_prompt
    assert "85-89" in transcript_analysis_system_prompt
    assert "80-84" in transcript_analysis_system_prompt
    assert "Arquivar" in transcript_analysis_system_prompt


def test_system_prompt_contains_categories():
    assert "Família" in transcript_analysis_system_prompt
    assert "Dor" in transcript_analysis_system_prompt
    assert "Esperança" in transcript_analysis_system_prompt
    assert "Testemunho" in transcript_analysis_system_prompt
    assert "Confronto" in transcript_analysis_system_prompt
    assert "Salvação" in transcript_analysis_system_prompt


def test_system_prompt_contains_audiences():
    assert "Pais" in transcript_analysis_system_prompt
    assert "Mães" in transcript_analysis_system_prompt
    assert "Casais" in transcript_analysis_system_prompt
    assert "Jovens" in transcript_analysis_system_prompt
    assert "Líderes" in transcript_analysis_system_prompt
    assert "Empresários" in transcript_analysis_system_prompt


def test_build_transcript_analysis_prompt_requires_transcript_fidelity():
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] Uma linha de abertura forte"
    )

    assert "Nunca altere o sentido da mensagem." in prompt
    assert "Nunca invente falas." in prompt
    assert "O impacto nunca pode comprometer a verdade." in prompt
    assert "Não junte momentos separados não contíguos" in prompt
    assert "fidelidade" in prompt.lower()
    assert f"{IDEAL_CLIP_MIN_SECONDS}-{IDEAL_CLIP_MAX_SECONDS}" in prompt
    assert "espectador entenderia e se importaria" in prompt
    assert "Retorne um único objeto JSON válido" in prompt
    assert "[00:12 - 00:21] Uma linha de abertura forte" in prompt


def test_build_transcript_analysis_prompt_mentions_broll_only_when_enabled():
    without_broll = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] Uma linha de abertura forte"
    )
    with_broll = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] Uma linha de abertura forte",
        include_broll=True,
    )

    assert "B-roll" not in without_broll
    assert "B-roll" in with_broll


def test_build_transcript_analysis_prompt_includes_rells_scoring():
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] Uma linha de abertura forte"
    )

    assert "Gancho" in prompt
    assert "Retenção" in prompt
    assert "Emoção" in prompt
    assert "Identificação" in prompt
    assert "Compartilhamento" in prompt
    assert "Comentários" in prompt
    assert "Salvamentos" in prompt
    assert "Curva emocional" in prompt
    assert "Compatibilidade com o perfil" in prompt
    assert "Fidelidade ao sermão" in prompt


def test_build_transcript_analysis_prompt_includes_new_output_fields():
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] Uma linha de abertura forte"
    )

    assert "category" in prompt
    assert "audience" in prompt
    assert "cover_title" in prompt
    assert "hook_title" in prompt


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
                "[00:10 - 00:24] Destaque curto",
                "[00:24 - 00:36] Resultado",
                "[00:36 - 01:20] Muito contexto",
            ]
        )
    )

    assert _extract_transcript_text(spans, 10, 36) == "Destaque curto Resultado"
    assert _choose_repaired_bounds(spans, 10, 24) == (10, 36)
    assert _choose_repaired_bounds(spans, 0, 80) == (0, 36)


def test_transcript_segment_normalizes_percent_relevance_score():
    segment = TranscriptSegment(
        start_time="00:00",
        end_time="00:30",
        text="Um momento completo e autônomo com contexto útil.",
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
