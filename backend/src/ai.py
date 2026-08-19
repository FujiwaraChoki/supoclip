"""
AI-related functions for transcript analysis with enhanced precision and virality scoring.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
import asyncio
import logging
import re

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic import AliasChoices, BaseModel, Field, field_validator

from .config import Config, get_config
from .runtime_settings import apply_settings_to_process_env
from .prompt_manager import PromptManager

logger = logging.getLogger(__name__)


async def _retry_with_backoff(async_fn, max_retries: int = 3, backoff_delays: List[int] = None):
    """
    Retry async function with exponential backoff for HTTP 429 and 503 errors.
    backoff_delays: list of delays in seconds [5, 10, 20] for each retry
    """
    if backoff_delays is None:
        backoff_delays = [5, 10, 20]

    from pydantic_ai.exceptions import ModelHTTPError

    for attempt in range(max_retries):
        try:
            return await async_fn()
        except ModelHTTPError as e:
            if e.status_code not in (429, 503):
                raise

            if attempt < max_retries - 1:
                delay = backoff_delays[attempt] if attempt < len(backoff_delays) else backoff_delays[-1]
                logger.warning(
                    f"HTTP {e.status_code} from LLM. Retrying in {delay}s (attempt {attempt + 1}/{max_retries - 1})"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"HTTP {e.status_code} from LLM after {max_retries} attempts. Giving up.")
                raise

IDEAL_CLIP_MIN_SECONDS = 25
IDEAL_CLIP_MAX_SECONDS = 50
MIN_ACCEPTED_CLIP_SECONDS = 10
MAX_ACCEPTED_CLIP_SECONDS = 90
TRANSCRIPT_ANALYSIS_CACHE_VERSION = "hook-titles-v4"
HOOK_TITLE_MAX_CHARS = 64
HOOK_TITLE_MAX_WORDS = 10
TRANSCRIPT_SPAN_RE = re.compile(
    r"^\[(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*)$"
)


RELLS_CATEGORIES = Literal[
    "familia", "pais", "filhos", "casamento", "dor", "esperanca",
    "testemunho", "confronto", "ensino", "politica",
    "guerra_espiritual", "perdao", "salvacao"
]

RELLS_AUDIENCES = Literal[
    "pais", "maes", "casais", "jovens", "lideres",
    "empresarios", "ansiosos", "enfermos"
]

RELLS_CLASSIFICATIONS = Literal["S++", "S+", "S", "A", "B", "Arquivar"]


class ViralityAnalysis(BaseModel):
    """RELLS Engine v2.0 scoring breakdown (0-100 points total)."""

    hook_score: int = Field(
        default=5,
        description="Força do gancho (0-10)",
        ge=0,
        le=10,
    )
    retention_score: int = Field(
        default=5,
        description="Potencial de retenção (0-10)",
        ge=0,
        le=10,
    )
    emotion_score: int = Field(
        default=5,
        description="Impacto emocional (0-10)",
        ge=0,
        le=10,
    )
    identification_score: int = Field(
        default=5,
        description="Potencial de identificação (0-10)",
        ge=0,
        le=10,
    )
    shareability_score: int = Field(
        default=5,
        description="Potencial de compartilhamento (0-10)",
        ge=0,
        le=10,
    )
    comment_score: int = Field(
        default=5,
        description="Potencial de gerar comentários (0-10)",
        ge=0,
        le=10,
    )
    save_score: int = Field(
        default=5,
        description="Potencial de ser salvo (0-10)",
        ge=0,
        le=10,
    )
    emotional_curve_score: int = Field(
        default=5,
        description="Curva emocional completa (0-10)",
        ge=0,
        le=10,
    )
    profile_compatibility_score: int = Field(
        default=10,
        description="Compatibilidade com o perfil (0-20)",
        ge=0,
        le=20,
    )
    sermon_fidelity_score: int = Field(
        default=5,
        description="Fidelidade ao sermão/mensagem (0-10)",
        ge=0,
        le=10,
    )
    total_score: int = Field(
        default=50,
        description="Pontuação total (0-100)",
        ge=0,
        le=100,
    )
    classification: RELLS_CLASSIFICATIONS = Field(
        default="B",
        description="Classificação: S++ (98-100), S+ (95-97), S (90-94), A (85-89), B (80-84), Arquivar (<80)",
    )
    reasoning: str = Field(
        default="Análise não fornecida pelo modelo.",
        description="Justificativa da pontuação",
    )


def _default_virality_analysis() -> ViralityAnalysis:
    return ViralityAnalysis()


class TranscriptSegment(BaseModel):
    """Represents a relevant segment of transcript with RELLS Engine v2.0 analysis."""

    start_time: str = Field(description="Start timestamp in MM:SS format")
    end_time: str = Field(description="End timestamp in MM:SS format")
    text: str = Field(
        validation_alias=AliasChoices("text", "segment"),
        description=(
            "Transcript text taken only from the selected timestamp range. "
            "Keep it verbatim or near-verbatim, and do not paraphrase or merge non-contiguous lines."
        )
    )
    relevance_score: float = Field(
        default=0.75,
        description="Relevance score from 0.0 to 1.0", ge=0.0, le=1.0
    )
    reasoning: str = Field(
        default="Selected by the AI model as a clip candidate.",
        description=(
            "Brief factual explanation of why this exact segment works as a clip. "
            "Base it only on the provided transcript content."
        )
    )
    virality: ViralityAnalysis = Field(
        default_factory=_default_virality_analysis,
        description="RELLS Engine v2.0 scoring breakdown",
    )
    hook_title: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("hook_title", "title", "headline"),
        description=(
            "Short punchy on-screen title for the clip (3-9 words). Grounded in "
            "the segment content, no hashtags, no emojis, no surrounding quotes."
        ),
    )
    category: Optional[RELLS_CATEGORIES] = Field(
        default=None,
        validation_alias=AliasChoices("category", "categoria"),
        description=(
            "Categoria do trecho: familia, pais, filhos, casamento, dor, esperanca, "
            "testemunho, confronto, ensino, politica, guerra_espiritual, perdao, salvação"
        ),
    )
    audience: Optional[RELLS_AUDIENCES] = Field(
        default=None,
        validation_alias=AliasChoices("audience", "publico"),
        description=(
            "Público-alvo: pais, maes, casais, jovens, lideres, "
            "empresarios, ansiosos, enfermos"
        ),
    )
    cover_title: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("cover_title", "capa"),
        description=(
            "Título para capa/thumbnail (até 6 palavras). "
            "Curioso, emocional e fiel ao conteúdo."
        ),
    )
    memorable_quotes: Optional[List[str]] = Field(
        default=None,
        validation_alias=AliasChoices("memorable_quotes", "frases"),
        description="Frases memoráveis que possam virar artes e capas",
    )
    series_part: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("series_part", "parte"),
        description="Se o trecho faz parte de uma série (Parte 1, 2, 3...)",
    )

    @field_validator("relevance_score", mode="before")
    @classmethod
    def _coerce_percent_relevance_score(cls, value: Any) -> Any:
        if value is None:
            return value
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return value
        if numeric_value > 1 and numeric_value <= 100:
            return numeric_value / 100
        return value


class BRollOpportunity(BaseModel):
    """Identifies an opportunity to insert B-roll footage."""

    timestamp: str = Field(
        default="00:00",
        validation_alias=AliasChoices("timestamp", "segment_start_time", "start_time"),
        description="When to insert B-roll (MM:SS format)",
    )
    duration: float = Field(
        default=3.0,
        description="How long to show B-roll (2-5 seconds)",
        ge=2.0,
        le=5.0,
    )
    search_term: str = Field(
        default="related visual",
        validation_alias=AliasChoices("search_term", "broll", "visual", "query"),
        description="Keyword to search for B-roll footage",
    )
    context: str = Field(
        default="Suggested B-roll opportunity from the model.",
        validation_alias=AliasChoices("context", "description"),
        description="What's being discussed at this point",
    )

    @field_validator("search_term", "context", mode="before")
    @classmethod
    def _coerce_textish_value(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if item is not None)
        return str(value)


class TranscriptAnalysis(BaseModel):
    """Analysis result for transcript segments with virality and B-roll opportunities."""

    most_relevant_segments: List[TranscriptSegment]
    summary: str = Field(description="Brief summary of the video content")
    key_topics: List[str] = Field(description="List of main topics discussed")
    broll_opportunities: Optional[List[BRollOpportunity]] = Field(
        default=None, description="Opportunities to insert B-roll footage"
    )


transcript_analysis_system_prompt = PromptManager.get("rells_engine")

# Lazy-loaded agent to avoid import-time failures when API keys aren't set
_transcript_agent: Optional[Agent[None, TranscriptAnalysis]] = None
_transcript_agent_signature: Optional[tuple[str | None, ...]] = None

SUPPORTED_LLM_PROVIDERS = {"google", "google-gla", "openai", "anthropic", "ollama"}


def _split_llm_name(model_name: str) -> tuple[str, str | None]:
    if ":" not in model_name:
        return model_name.strip().lower(), None

    provider, provider_model_name = model_name.split(":", 1)
    return provider.strip().lower(), provider_model_name.strip() or None


def _get_missing_llm_key_error(model_name: str, runtime_config: Config) -> Optional[str]:
    """Return a clear configuration error when the selected LLM key is missing."""
    provider, provider_model_name = _split_llm_name(model_name)

    if provider not in SUPPORTED_LLM_PROVIDERS:
        return (
            f"Unsupported LLM provider '{provider}'. "
            "Use google-gla:*, openai:*, anthropic:*, or ollama:*."
        )

    if not provider_model_name:
        return (
            "Selected LLM is missing a model name. "
            "Use the format provider:model, for example ollama:gpt-oss:20b."
        )

    if provider in {"google", "google-gla"} and not runtime_config.google_api_key:
        return (
            "Selected LLM provider is Google, but GOOGLE_API_KEY is not set. "
            "Set GOOGLE_API_KEY or set LLM to openai:* / anthropic:* / ollama:* with the matching API key."
        )

    if provider == "openai" and not runtime_config.openai_api_key:
        return (
            "Selected LLM provider is OpenAI, but OPENAI_API_KEY is not set. "
            "Set OPENAI_API_KEY or choose another provider with a matching API key."
        )

    if provider == "anthropic" and not runtime_config.anthropic_api_key:
        return (
            "Selected LLM provider is Anthropic, but ANTHROPIC_API_KEY is not set. "
            "Set ANTHROPIC_API_KEY or choose another provider with a matching API key."
        )

    if provider == "ollama":
        # Ollama can run locally without an API key. OLLAMA_BASE_URL/OLLAMA_API_KEY
        # are optional and passed through as environment variables.
        return None

    return None


def _build_transcript_model(runtime_config: Config) -> Model | str:
    provider, provider_model_name = _split_llm_name(runtime_config.llm)
    if provider != "ollama":
        return runtime_config.llm

    if not provider_model_name:
        raise RuntimeError(
            "Selected LLM provider is Ollama, but no model name was provided. "
            "Use the format ollama:<model>, for example ollama:gpt-oss:20b."
        )

    return OllamaModel(
        provider_model_name,
        provider=OllamaProvider(
            base_url=runtime_config.resolve_ollama_base_url(),
            api_key=runtime_config.ollama_api_key,
        ),
    )


def get_transcript_agent() -> Agent[None, TranscriptAnalysis]:
    """Get or create the transcript analysis agent (lazy initialization)."""
    global _transcript_agent, _transcript_agent_signature
    runtime_config = get_config()
    provider, _ = _split_llm_name(runtime_config.llm)
    signature = (
        runtime_config.llm,
        runtime_config.openai_api_key,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _transcript_agent is None or _transcript_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
        if config_error:
            raise RuntimeError(config_error)

        _transcript_agent = Agent[None, TranscriptAnalysis](
            model=_build_transcript_model(runtime_config),
            output_type=TranscriptAnalysis,
            system_prompt=transcript_analysis_system_prompt,
        )
        _transcript_agent_signature = signature
    return _transcript_agent


def build_transcript_analysis_prompt(
    transcript: str, include_broll: bool = False, clip_signals: str | None = None
) -> str:
    """Build the RELLS Engine v2.0 task prompt for transcript analysis."""
    broll_instruction = ""
    if include_broll:
        broll_instruction = (
            "\n5. Identifique oportunidades de B-roll para cada segmento escolhido, "
            "onde imagens de stock poderiam melhorar o apelo visual."
        )
    signal_section = ""
    if clip_signals:
        signal_section = (
            "\n\nSinais determinísticos adicionais da análise de transcrição/áudio:\n"
            f"{clip_signals}\n\n"
            "Use apenas como dicas. Eles devem influenciar o ranqueamento, mas todo segmento final "
            "deve ser um intervalo contíguo e coerente da transcrição."
        )

    return f"""Analise esta transcrição de vídeo e identifique os trechos mais envolventes para conteúdo de formato curto.

A transcrição é formatada como uma linha por intervalo com timestamp, por exemplo:
[00:12 - 00:21] Texto falado aqui
[00:21 - 00:35] Mais texto falado aqui

Siga este fluxo de trabalho:
1. Leia a transcrição como uma sequência de intervalos com timestamps.
2. Selecione apenas intervalos contíguos que já existem na transcrição.
3. Priorize momentos com gancho forte, resultado claro, carga emocional ou valor concreto.
4. Para cada segmento escolhido, use o timestamp mais cedo no intervalo selecionado como start_time e o mais tarde como end_time.
5. Classifique cada trecho por categorias (familia, pais, filhos, casamento, dor, esperanca, testemunho, confronto, ensino, politica, guerra_espiritual, perdao, salvação).
6. Identifique o público-alvo principal (pais, maes, casais, jovens, lideres, empresarios, ansiosos, enfermos).
7. Gere um título de capa de até 6 palavras.{broll_instruction}

Alvo de seleção:
- Extraia pelo menos 10 segmentos no total para conteúdo típico.
- Para conteúdo mais curto (5-10 minutos): mínimo 5 segmentos.
- Para conteúdo mais longo (60+ minutos): mira em 20+ segmentos.
- A maioria dos clipes deve ter 25-50 segundos.
- Só escolha um clipe de 15-24 segundos quando já contiver setup e resultado completos.
- Se um momento forte tiver menos de 25 segundos, primeiro tente expandir para linhas adjacentes da transcrição que adicionam contexto útil.
- Pule seleções fracas: intros, leituras de patrocinadores, CTAs, citações sem contexto, pontos repetidos, setup vago e fragmentos de respostas que precisam de contexto anterior.
- Antes de retornar um segmento, pergunte se o espectador entenderia e se importaria sem ver o resto do vídeo original.
- Qualidade por segmento importa, mas quantidade também é importante para construção de biblioteca.

Requisitos de fidelidade:
- Nunca altere o sentido da mensagem.
- Nunca invente falas.
- O impacto nunca pode comprometer a verdade.
- Não invente ou embeleze conteúdo.
- Não use timestamps que não estejam na transcrição.
- Não junte momentos separados não contíguos em um único segmento.
- O texto do segmento deve refletir apenas o conteúdo falado dentro do intervalo de tempo selecionado.
- Se um intervalo não tiver contexto suficiente para ser autônomo, expanda para linhas adjacentes contíguas em vez de adivinhar.
- Se houver um tradeoff entre "viral" e "fiel", escolha fidelidade.
- Não rejeite ou penalize um segmento apenas por causa do tema; avalie apenas a qualidade do clipe.
{signal_section}

Regras de pontuação RELLS Engine (0-100 pontos):
| Critério | Pontos |
|----------|--------|
| Gancho | 10 |
| Retenção | 10 |
| Emoção | 10 |
| Identificação | 10 |
| Compartilhamento | 10 |
| Comentários | 10 |
| Salvamentos | 10 |
| Curva emocional | 10 |
| Compatibilidade com o perfil | 20 |
| Fidelidade ao sermão | 10 |

Classificação: S++ (98-100), S+ (95-97), S (90-94), A (85-89), B (80-84), Arquivar (<80)

Curva emocional obrigatória em cada corte:
Curiosidade → Identificação → Confronto → Esperança → Fechamento

Pergunta obrigatória: "Alguém enviaria este vídeo para outra pessoa?"

Requisitos de saída JSON apenas:
- Retorne um único objeto JSON válido e nada mais.
- Sem Markdown, títulos, listas, cercas de código ou texto explicativo fora do JSON.
- Chaves de nível superior: "most_relevant_segments", "summary", "key_topics"{', "broll_opportunities"' if include_broll else ''}.
- Chaves do segmento: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", "hook_title", "category", "audience", "cover_title".
- "hook_title": Título de 3-9 palavras para overlay no clipe (sem hashtags, emojis ou aspas).
- "cover_title": Título de até 6 palavras para capa/thumbnail.
- Chaves da virality: "hook_score", "retention_score", "emotion_score", "identification_score", "shareability_score", "comment_score", "save_score", "emotional_curve_score", "profile_compatibility_score", "sermon_fidelity_score", "total_score", "classification", "reasoning".
- Cada segmento deve ter 15-90 segundos (ideal: 25-50 segundos).

Transcrição:
{transcript}"""


def sanitize_hook_title(raw: Optional[str]) -> Optional[str]:
    """Normalize an AI-provided hook title for on-screen rendering.

    Strips wrapping quotes/markdown, collapses whitespace, drops hashtags, and
    trims to a word-boundary length cap. Returns None when nothing usable is
    left so callers can simply skip the overlay.
    """
    if not raw:
        return None
    title = str(raw).strip()
    title = title.strip("\"'`“”‘’").strip()
    title = re.sub(r"#\w+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    # Drop trailing sentence punctuation but keep ?/! (they carry the hook).
    title = title.rstrip(".,;:-–— ").strip()
    if not title:
        return None

    words = title.split()
    if len(words) > HOOK_TITLE_MAX_WORDS:
        words = words[:HOOK_TITLE_MAX_WORDS]
        title = " ".join(words)
    if len(title) > HOOK_TITLE_MAX_CHARS:
        clipped = title[: HOOK_TITLE_MAX_CHARS + 1]
        cut = clipped.rfind(" ")
        title = (clipped[:cut] if cut > 20 else title[:HOOK_TITLE_MAX_CHARS]).rstrip(
            ".,;:-–— "
        )
    return title or None


def _parse_transcript_timestamp_seconds(timestamp: str) -> int:
    """Parse MM:SS or HH:MM:SS transcript timestamps into seconds."""
    parts = [int(part) for part in timestamp.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"Unsupported timestamp format: {timestamp}")


def _format_transcript_timestamp(seconds: int) -> str:
    """Format seconds as a transcript timestamp."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _parse_transcript_spans(transcript: str) -> list[dict[str, Any]]:
    """Parse timestamped transcript lines into spans."""
    spans = []
    for line in transcript.splitlines():
        match = TRANSCRIPT_SPAN_RE.match(line.strip())
        if not match:
            continue
        try:
            start_seconds = _parse_transcript_timestamp_seconds(match.group("start"))
            end_seconds = _parse_transcript_timestamp_seconds(match.group("end"))
        except ValueError:
            continue
        if end_seconds <= start_seconds:
            continue
        spans.append(
            {
                "start": start_seconds,
                "end": end_seconds,
                "text": match.group("text").strip(),
            }
        )
    return spans


def _extract_transcript_text(
    transcript_spans: list[dict[str, Any]], start_seconds: int, end_seconds: int
) -> str:
    """Return transcript text overlapping a selected time range."""
    selected_text = [
        span["text"]
        for span in transcript_spans
        if span["text"]
        and span["end"] > start_seconds
        and span["start"] < end_seconds
    ]
    return " ".join(selected_text).strip()


def _choose_repaired_bounds(
    transcript_spans: list[dict[str, Any]], start_seconds: int, end_seconds: int
) -> tuple[int, int] | None:
    """Repair model-selected bounds to the nearest acceptable contiguous range."""
    if not transcript_spans:
        return None

    starts = sorted({span["start"] for span in transcript_spans})
    ends = sorted({span["end"] for span in transcript_spans})
    current_duration = end_seconds - start_seconds

    if current_duration > MAX_ACCEPTED_CLIP_SECONDS:
        target_end = start_seconds + IDEAL_CLIP_MAX_SECONDS
        candidate_ends = [
            candidate
            for candidate in ends
            if start_seconds + MIN_ACCEPTED_CLIP_SECONDS
            <= candidate
            <= min(target_end, end_seconds)
        ]
        if candidate_ends:
            return start_seconds, max(candidate_ends)
        if start_seconds + MIN_ACCEPTED_CLIP_SECONDS <= target_end:
            return start_seconds, target_end
        return None

    if current_duration < MIN_ACCEPTED_CLIP_SECONDS:
        candidate_ranges: list[tuple[int, int, int]] = []
        for candidate_start in starts:
            if candidate_start > start_seconds:
                continue
            for candidate_end in ends:
                if candidate_end < end_seconds:
                    continue
                duration = candidate_end - candidate_start
                if MIN_ACCEPTED_CLIP_SECONDS <= duration <= MAX_ACCEPTED_CLIP_SECONDS:
                    extra_context = (start_seconds - candidate_start) + (
                        candidate_end - end_seconds
                    )
                    ideal_penalty = 0
                    if duration < IDEAL_CLIP_MIN_SECONDS:
                        ideal_penalty = IDEAL_CLIP_MIN_SECONDS - duration
                    elif duration > IDEAL_CLIP_MAX_SECONDS:
                        ideal_penalty = duration - IDEAL_CLIP_MAX_SECONDS
                    candidate_ranges.append(
                        (ideal_penalty * 1000 + extra_context, candidate_start, candidate_end)
                    )
        if candidate_ranges:
            _, repaired_start, repaired_end = min(candidate_ranges)
            return repaired_start, repaired_end

    return None


def _repair_segment_bounds(
    segment: TranscriptSegment,
    transcript_spans: list[dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
) -> tuple[int, int] | None:
    """Adjust near-miss model ranges to usable transcript-aligned bounds."""
    repaired_bounds = _choose_repaired_bounds(
        transcript_spans,
        start_seconds,
        end_seconds,
    )
    if not repaired_bounds:
        return None

    repaired_start, repaired_end = repaired_bounds
    segment.start_time = _format_transcript_timestamp(repaired_start)
    segment.end_time = _format_transcript_timestamp(repaired_end)
    repaired_text = _extract_transcript_text(
        transcript_spans,
        repaired_start,
        repaired_end,
    )
    if repaired_text:
        segment.text = repaired_text
    logger.info(
        "Repaired segment duration: %s-%s -> %s-%s",
        _format_transcript_timestamp(start_seconds),
        _format_transcript_timestamp(end_seconds),
        segment.start_time,
        segment.end_time,
    )
    return repaired_start, repaired_end


async def get_most_relevant_parts_by_transcript(
    transcript: str, include_broll: bool = False, clip_signals: str | None = None
) -> TranscriptAnalysis:
    """Get the most relevant parts of a transcript with virality scoring and optional B-roll detection."""
    logger.info(
        f"Starting AI analysis of transcript ({len(transcript)} chars), include_broll={include_broll}"
    )

    try:
        agent = get_transcript_agent()

        async def run_analysis():
            return await agent.run(
                build_transcript_analysis_prompt(
                    transcript=transcript,
                    include_broll=include_broll,
                    clip_signals=clip_signals,
                )
            )

        result = await _retry_with_backoff(run_analysis, max_retries=3, backoff_delays=[5, 10, 20])
        analysis = result.output
        logger.info(
            f"🔍 [GEMINI_RETURNED] AI analysis returned {len(analysis.most_relevant_segments)} raw segments from LLM"
        )
        for idx, seg in enumerate(analysis.most_relevant_segments):
            logger.info(
                f"  ├─ Segment {idx}: {seg.start_time} → {seg.end_time} | "
                f"Text: {seg.text[:40]}... | Score: {getattr(seg.virality, 'total_score', 'N/A') if seg.virality else 'N/A'}"
            )

        # Validation with virality data handling
        validated_segments = []
        transcript_spans = _parse_transcript_spans(transcript)
        skipped_insufficient_content = 0
        skipped_identical_timestamps = 0
        skipped_invalid_duration = 0
        skipped_too_short = 0
        skipped_too_long = 0
        skipped_bad_timestamps = 0

        for idx, segment in enumerate(analysis.most_relevant_segments):
            # Validate text content
            if not segment.text.strip() or len(segment.text.split()) < 3:
                logger.debug(
                    f"Skipping segment {idx} with insufficient content: '{segment.text[:50]}...'"
                )
                skipped_insufficient_content += 1
                continue

            # Validate timestamps - CRITICAL: start and end must be different
            if segment.start_time == segment.end_time:
                logger.debug(
                    f"Skipping segment {idx} with identical start/end times: {segment.start_time}"
                )
                skipped_identical_timestamps += 1
                continue

            # Parse timestamps to validate duration
            try:
                start_seconds = _parse_transcript_timestamp_seconds(
                    segment.start_time
                )
                end_seconds = _parse_transcript_timestamp_seconds(segment.end_time)

                duration = end_seconds - start_seconds

                if duration < MIN_ACCEPTED_CLIP_SECONDS or duration > MAX_ACCEPTED_CLIP_SECONDS:
                    repaired_bounds = _repair_segment_bounds(
                        segment,
                        transcript_spans,
                        start_seconds,
                        end_seconds,
                    )
                    if repaired_bounds:
                        start_seconds, end_seconds = repaired_bounds
                        duration = end_seconds - start_seconds

                if duration <= 0:
                    logger.debug(
                        f"Skipping segment {idx} with invalid duration: {segment.start_time} to {segment.end_time} = {duration}s"
                    )
                    skipped_invalid_duration += 1
                    continue

                if duration < MIN_ACCEPTED_CLIP_SECONDS:
                    logger.debug(
                        f"Skipping segment {idx} too short: {duration}s (min {MIN_ACCEPTED_CLIP_SECONDS}s required)"
                    )
                    skipped_too_short += 1
                    continue

                if duration > MAX_ACCEPTED_CLIP_SECONDS:
                    logger.debug(
                        f"Skipping segment {idx} too long: {duration}s (max {MAX_ACCEPTED_CLIP_SECONDS}s allowed)"
                    )
                    skipped_too_long += 1
                    continue

                # Validate virality scores
                if segment.virality:
                    # Ensure total score is sum of subscores (RELLS Engine v2.0)
                    calculated_total = (
                        segment.virality.hook_score
                        + segment.virality.retention_score
                        + segment.virality.emotion_score
                        + segment.virality.identification_score
                        + segment.virality.shareability_score
                        + segment.virality.comment_score
                        + segment.virality.save_score
                        + segment.virality.emotional_curve_score
                        + segment.virality.profile_compatibility_score
                        + segment.virality.sermon_fidelity_score
                    )
                    if segment.virality.total_score != calculated_total:
                        logger.warning(
                            f"Correcting virality total: {segment.virality.total_score} -> {calculated_total}"
                        )
                        segment.virality.total_score = calculated_total

                    # Auto-classify based on score
                    if segment.virality.total_score >= 98:
                        segment.virality.classification = "S++"
                    elif segment.virality.total_score >= 95:
                        segment.virality.classification = "S+"
                    elif segment.virality.total_score >= 90:
                        segment.virality.classification = "S"
                    elif segment.virality.total_score >= 85:
                        segment.virality.classification = "A"
                    elif segment.virality.total_score >= 80:
                        segment.virality.classification = "B"
                    else:
                        segment.virality.classification = "Arquivar"

                segment.hook_title = sanitize_hook_title(segment.hook_title)

                validated_segments.append(segment)
                virality_info = (
                    f", virality={segment.virality.total_score}"
                    if segment.virality
                    else ""
                )
                logger.info(
                    f"Validated segment: {segment.start_time}-{segment.end_time} ({duration}s){virality_info}"
                )

            except (ValueError, IndexError) as e:
                logger.debug(
                    f"Skipping segment {idx} with invalid timestamp format: {segment.start_time}-{segment.end_time}: {e}"
                )
                skipped_bad_timestamps += 1
                continue

        # Sort by virality score (primary) then relevance (secondary)
        validated_segments.sort(
            key=lambda x: (
                x.virality.total_score if x.virality else 0,
                x.relevance_score,
            ),
            reverse=True,
        )

        final_analysis = TranscriptAnalysis(
            most_relevant_segments=validated_segments,
            summary=analysis.summary,
            key_topics=analysis.key_topics,
            broll_opportunities=analysis.broll_opportunities if include_broll else None,
        )

        logger.info(
            f"🔍 [VALIDATION_COMPLETE] Gemini returned {len(analysis.most_relevant_segments)} → "
            f"Validated {len(validated_segments)} segments "
            f"(skipped: {skipped_insufficient_content} content, {skipped_identical_timestamps} identical_ts, "
            f"{skipped_invalid_duration} invalid_dur, {skipped_too_short} too_short, "
            f"{skipped_too_long} too_long, {skipped_bad_timestamps} bad_ts)"
        )
        for idx, seg in enumerate(validated_segments):
            logger.info(
                f"  ├─ Validated Segment {idx}: {seg.start_time} → {seg.end_time} | "
                f"Virality: {seg.virality.total_score if seg.virality else 0}"
            )
        logger.info(f"📋 [AFTER_VALIDATION] {len(validated_segments)} segments ready for processing")
        if validated_segments:
            top = validated_segments[0]
            logger.info(
                f"Top segment - relevance: {top.relevance_score:.2f}, virality: {top.virality.total_score if top.virality else 'N/A'}"
            )

        return final_analysis

    except Exception as e:
        logger.error(f"Error in transcript analysis: {e}")
        raise RuntimeError(f"Transcript analysis failed: {str(e)}") from e


def get_most_relevant_parts_sync(transcript: str) -> TranscriptAnalysis:
    """Synchronous wrapper for the async function."""
    return asyncio.run(get_most_relevant_parts_by_transcript(transcript))
