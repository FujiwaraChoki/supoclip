"""
AI-related functions for transcript analysis with enhanced precision and virality scoring.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
import asyncio
import logging
import math
import random
import re

import httpx
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import Model
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic import AliasChoices, BaseModel, Field, field_validator

from .config import Config, get_config
from .runtime_settings import apply_settings_to_process_env

logger = logging.getLogger(__name__)

IDEAL_CLIP_MIN_SECONDS = 25
IDEAL_CLIP_MAX_SECONDS = 50
MIN_ACCEPTED_CLIP_SECONDS = 15
MAX_ACCEPTED_CLIP_SECONDS = 60
TRANSCRIPT_ANALYSIS_CACHE_VERSION = "windowed-rerank-v6"
TRANSCRIPT_ANALYSIS_MAX_ATTEMPTS = 3
TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS = 600
# Long transcripts are analysed in overlapping windows so every part of the
# source gets the model's full attention; candidates are then reranked together.
ANALYSIS_WINDOW_SECONDS = 12 * 60
ANALYSIS_WINDOW_OVERLAP_SECONDS = 2 * 60
ANALYSIS_SINGLE_WINDOW_MAX_SECONDS = 15 * 60
ANALYSIS_WINDOW_CONCURRENCY = 3
ANALYSIS_WINDOW_SEGMENT_TARGET = "3-5"
# Wall-clock budget for all windows together. Each window additionally keeps
# its own TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS budget once it starts running.
ANALYSIS_TOTAL_TIMEOUT_SECONDS = 1800
RERANK_TIMEOUT_SECONDS = 240
CANDIDATE_DEDUPE_OVERLAP_RATIO = 0.5
# How far a model timestamp may sit from a transcript line boundary and still be
# snapped onto it instead of the whole clip being dropped.
SEGMENT_SNAP_TOLERANCE_SECONDS = 8
# Final clip count scales with source length: one clip per five minutes,
# never fewer than the single-pass prompt's old maximum and never more than 20.
MIN_FINAL_CLIPS = 5
MAX_FINAL_CLIPS = 20
SECONDS_PER_FINAL_CLIP = 5 * 60
TRANSIENT_MODEL_STATUS_CODES = {408, 429, 500, 502, 503, 504, 529}
HOOK_TITLE_MAX_CHARS = 64
HOOK_TITLE_MAX_WORDS = 10
# The transcript formatter does not wrap minutes into hours, so sources of 100+
# minutes produce labels such as "100:05".
TRANSCRIPT_SPAN_RE = re.compile(
    r"^\[(?P<start>\d{1,3}:\d{2}(?::\d{2})?)\s*-\s*"
    r"(?P<end>\d{1,3}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*)$"
)
TRANSCRIPT_LINE_PATTERN = re.compile(
    r"^\[(\d{1,3}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,3}:\d{2}(?::\d{2})?)\]\s*(.*)$"
)
SIGNAL_LINE_PATTERN = re.compile(r"^- \[(?P<start>\d{1,3}:\d{2}(?::\d{2})?)\s*-")
SPEAKER_PREFIX_PATTERN = re.compile(r"^Speaker [^:]+:\s*")


def _normalize_transcript_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9']+", " ", value.lower())).strip()


def _parse_transcript_lines(transcript: str) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for raw_line in transcript.splitlines():
        match = TRANSCRIPT_LINE_PATTERN.match(raw_line.strip())
        if not match:
            continue
        line_text = SPEAKER_PREFIX_PATTERN.sub("", match.group(3).strip())
        lines.append(
            {
                "start_time": match.group(1),
                "end_time": match.group(2),
                "start_seconds": _parse_transcript_timestamp_seconds(match.group(1)),
                "end_seconds": _parse_transcript_timestamp_seconds(match.group(2)),
                "text": line_text,
            }
        )
    return lines


def _snap_bounds_to_transcript_lines(
    transcript_lines: List[Dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
    tolerance_seconds: int = SEGMENT_SNAP_TOLERANCE_SECONDS,
) -> Optional[tuple[Dict[str, Any], Dict[str, Any]]]:
    """Snap model timestamps onto the nearest transcript line start and end.

    Models often pick a second that sits inside a line rather than on its
    boundary. Grounding needs exact line labels, so move each bound to the
    closest boundary within the tolerance instead of discarding the clip.
    """
    if not transcript_lines:
        return None

    start_line = min(
        transcript_lines,
        key=lambda line: (abs(line["start_seconds"] - start_seconds), line["start_seconds"]),
    )
    if abs(start_line["start_seconds"] - start_seconds) > tolerance_seconds:
        return None

    end_candidates = [
        line
        for line in transcript_lines
        if line["end_seconds"] > start_line["start_seconds"]
    ]
    if not end_candidates:
        return None
    end_line = min(
        end_candidates,
        key=lambda line: (abs(line["end_seconds"] - end_seconds), -line["end_seconds"]),
    )
    if abs(end_line["end_seconds"] - end_seconds) > tolerance_seconds:
        return None
    return start_line, end_line


def _extract_transcript_text_for_segment(
    transcript_lines: List[Dict[str, str]],
    start_time: str,
    end_time: str,
) -> Optional[str]:
    for start_index, line in enumerate(transcript_lines):
        if line["start_time"] != start_time:
            continue

        collected_parts = [line["text"]] if line["text"] else []
        if line["end_time"] == end_time:
            return " ".join(part for part in collected_parts if part).strip()

        for next_line in transcript_lines[start_index + 1 :]:
            if next_line["text"]:
                collected_parts.append(next_line["text"])
            if next_line["end_time"] == end_time:
                return " ".join(part for part in collected_parts if part).strip()

        return None

    return None


class ViralityAnalysis(BaseModel):
    """Detailed virality breakdown for a segment."""

    hook_score: int = Field(
        default=15,
        description="How strong is the opening hook (0-25)",
        ge=0,
        le=25,
    )
    engagement_score: int = Field(
        default=15,
        description="How engaging/entertaining is the content (0-25)",
        ge=0,
        le=25,
    )
    value_score: int = Field(
        default=15,
        description="Educational/informational value (0-25)",
        ge=0,
        le=25,
    )
    shareability_score: int = Field(
        default=15,
        description="Likelihood of being shared (0-25)",
        ge=0,
        le=25,
    )
    total_score: int = Field(
        default=60,
        description="Combined virality score (0-100)",
        ge=0,
        le=100,
    )
    hook_type: Optional[
        Literal["question", "statement", "statistic", "story", "contrast", "none"]
    ] = Field(
        default="none",
        description="Type of hook: question, statement, statistic, story, contrast, or none",
    )
    virality_reasoning: str = Field(
        default="The model did not provide a detailed virality breakdown.",
        description="Explanation of the virality score",
    )


def _default_virality_analysis() -> ViralityAnalysis:
    return ViralityAnalysis()


class TranscriptSegment(BaseModel):
    """Represents a relevant segment of transcript with precise timing and virality analysis."""

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
        description="Detailed virality score breakdown",
    )
    hook_title: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("hook_title", "title", "headline"),
        description=(
            "Short punchy on-screen title for the clip (3-9 words). Grounded in "
            "the segment content, no hashtags, no emojis, no surrounding quotes."
        ),
    )

    hook_start_time: Optional[str] = Field(
        default=None,
        description=(
            "Optional MM:SS start of the single strongest 2-5 second line inside "
            "the segment, replayed as a cold-open teaser. Null when the segment "
            "already opens on its best line."
        ),
    )
    hook_end_time: Optional[str] = Field(
        default=None,
        description="Optional MM:SS end of that hook line (null with hook_start_time).",
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


# Enhanced system prompt with virality scoring and B-roll detection
transcript_analysis_system_prompt = """You are an expert transcript analyst for short-form video editing.

Your job is extraction and ranking, not creative rewriting. You must stay fully grounded in the transcript and choose the best clip candidates that already exist in the source material.

OUTPUT CONTRACT:
- Return valid JSON only. Do not output Markdown, headings, bullets, prose, code fences, explanations, or commentary outside the JSON object.
- The top-level JSON object must include: "most_relevant_segments", "summary", and "key_topics".
- Set "broll_opportunities" to null when B-roll was not requested.
- Each item in "most_relevant_segments" must include: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", and "hook_title".
- Do not use "segment" as an output field. Use "text".
- "virality" must include: "hook_score", "engagement_score", "value_score", "shareability_score", "total_score", "hook_type", and "virality_reasoning".
- Every returned segment must be 15-60 seconds long. Prefer 25-50 seconds.

CORE OBJECTIVES:
1. Identify segments that would be compelling on social media platforms
2. Focus on complete thoughts, insights, or entertaining moments
3. Prioritize content with hooks, emotional moments, or valuable information
4. Each segment should be engaging and worth watching
5. Score each segment's viral potential with detailed breakdown

GROUNDING RULES:
1. Use only the provided transcript lines and timestamps
2. Never invent facts, tone, context, or transitions that are not present
3. Treat this as span selection over a timestamped transcript, not open-ended summarization
4. Each selected segment must map to one contiguous range in the transcript
5. segment.text must match the chosen span closely and must not include content from outside the chosen range
6. Do not stitch together distant moments into one clip
7. If a speaker label appears, use it only if it is part of the spoken content and helps clarity

CONTENT NEUTRALITY RULES:
1. This is clipping software for legitimate editing workflows
2. Do not judge, moralize, or downgrade a segment just because the topic is controversial, sensitive, adult, political, criminal, medical, or otherwise intense
3. Evaluate segments only on clip quality: clarity, self-contained value, hook strength, emotional impact, specificity, and shareability
4. Do not refuse analysis just because the speaker describes risky, offensive, or uncomfortable subject matter
5. Only downgrade a segment when the transcript itself is weak, confusing, repetitive, unusable, or a poor standalone clip

SEGMENT SELECTION CRITERIA:
1. STRONG HOOKS: Attention-grabbing opening lines
2. VALUABLE CONTENT: Tips, insights, interesting facts, stories
3. EMOTIONAL MOMENTS: Excitement, surprise, humor, inspiration
4. COMPLETE THOUGHTS: Self-contained ideas that make sense alone
5. ENTERTAINING: Content people would want to share
6. HIGH SIGNAL: Prefer specific, concrete language over vague discussion
7. LOW FILLER: Avoid greetings, sponsor reads, repeated setup, throat-clearing, and housekeeping unless they are unusually compelling

WHAT A GOOD CLIP FEELS LIKE:
- A viewer should understand and care without the original title, thumbnail, or previous context
- Prefer a complete mini-story or argument: setup, tension or claim, specific detail, and payoff
- Expand a great short moment to nearby contiguous lines when that adds needed setup, stakes, or payoff
- Strong picks include contrarian claims, mistakes or lessons, concrete examples, before/after moments, frameworks, surprising results, emotionally charged reactions, and complete answers to interesting questions
- Bad picks include intros, sponsor or CTA sections, vague setup, contextless quote fragments, repeated points, definitions without payoff, meandering background, and answer fragments that require unseen context

VIRALITY SCORING (0-100 total, from four 0-25 subscores):
For each segment, provide a detailed virality breakdown:

1. HOOK STRENGTH (0-25):
   - 20-25: Immediately grabs attention (surprising fact, bold claim, intriguing question)
   - 15-19: Good opener that creates curiosity
   - 10-14: Decent start but could be stronger
   - 0-9: Weak or no hook

2. ENGAGEMENT (0-25):
   - 20-25: Highly entertaining, emotional, or dramatic
   - 15-19: Interesting and holds attention
   - 10-14: Moderately engaging
   - 0-9: Flat or boring delivery

3. VALUE (0-25):
   - 20-25: Actionable insights, unique knowledge, or transformative ideas
   - 15-19: Useful information most people don't know
   - 10-14: Somewhat informative
   - 0-9: Common knowledge or filler content

4. SHAREABILITY (0-25):
   - 20-25: "I need to send this to someone" content
   - 15-19: Content worth bookmarking
   - 10-14: Nice but not share-worthy
   - 0-9: Generic content

HOOK TITLES ("hook_title" per segment):
- Write a short on-screen headline (3-9 words) that is burned into the top of the clip
- It must make a scrolling viewer stop: a bold claim, curiosity gap, number, or stakes taken directly from the segment
- Stay grounded: only promise what the clip actually delivers; never invent facts or numbers
- Do not simply repeat the first spoken words verbatim; reframe them as a headline
- Plain text only: no hashtags, no emojis, no quotes around the title
- Write it in the same language as the transcript
- Good examples: "The $40k mistake I keep seeing", "Why nobody tells you this about VC", "Do this before your next interview"

CLIP BOUNDARIES — OPENING AND ENDING:
- start_time is the first word of a sentence a new viewer can understand with no prior context
- Do not start mid-sentence, on a filler (like, dude, um, well), on a bare agreement (yes, yeah, right, exactly, totally), or on a pronoun that points at someone the viewer has not met yet (he, she, they)
- Do not start on a back-reference ("as I said", "the thing we mentioned", "that point", "before we started recording") unless the clip also contains the thing being referenced
- If the moment only makes sense with the previous sentence's noun, claim, or question, move start_time back to include that sentence
- end_time is the last word of the sentence that lands the point. Do not stop mid-clause, and do not keep the opening words of the next unrelated thought
- The clip's real first sentence is the hook. Do not choose a weak opening because a later line could be replayed in front of it

COLD-OPEN HOOK ("hook_start_time"/"hook_end_time" per segment, optional):
- Prefer null. The clip is played from start_time; a later line is not replayed in front of it unless an operator explicitly enables that
- Only set a range when a 2-5 second punchline later in the segment stands completely alone AND the real opening is already a full setup
- Use timestamps from the transcript lines inside the segment, at least 3 seconds after start_time
- Set both to null when the clip already opens on its strongest line, or when replaying the line would just repeat it

HOOK TYPES to identify:
- "question": Opens with a question that creates curiosity
- "statement": Bold claim or surprising statement
- "statistic": Uses compelling numbers or data
- "story": Starts with narrative/anecdote
- "contrast": Before/after or problem/solution framing
- "none": No clear hook pattern

B-ROLL OPPORTUNITIES:
Identify 2-4 moments in each segment where B-roll footage could enhance the video:
- When specific objects, places, or concepts are mentioned
- During explanations that could benefit from visual illustration
- At emotional peaks that could use supporting imagery
- Use simple, searchable keywords (e.g., "coffee shop", "laptop coding", "money stack")

TIMING GUIDELINES:
- Target 25-50 seconds for most clips
- Use 15-24 seconds only when the moment is exceptionally dense, self-contained, and complete
- CRITICAL: start_time MUST be different from end_time (minimum 15 seconds apart)
- Focus on natural content boundaries rather than arbitrary time limits
- Include enough context for the segment to be understandable
- Prefer roughly 30-50 seconds when possible
- Start at the hook or the minimum setup needed to make the hook land, and end after the payoff
- If a highlight is only one good line, expand to include the surrounding setup and payoff rather than returning a tiny fragment
- Stop expanding when the topic drifts, the speaker repeats the same point, or the clip loses momentum

TIMESTAMP REQUIREMENTS - EXTREMELY IMPORTANT:
- Use EXACT timestamps as they appear in the transcript
- Never modify timestamp format (keep MM:SS structure)
- start_time MUST be LESS THAN end_time (start_time < end_time)
- MINIMUM segment duration: 15 seconds (end_time - start_time >= 15 seconds)
- IDEAL segment duration: 25-50 seconds
- Look at transcript ranges like [02:25 - 02:35] and use different start/end times
- NEVER use the same timestamp for both start_time and end_time
- Example: start_time: "02:25", end_time: "02:35" (NOT "02:25" and "02:25")

SCORING AND OUTPUT RULES:
- relevance_score should reflect how well the segment works as a standalone short clip, not just whether the topic is generally important
- Penalize clips that are only quotable but not self-contained, too generic, missing setup, missing payoff, or padded with filler
- virality_reasoning and reasoning should cite what is actually present in the chosen span
- summary and key_topics must also stay grounded in the transcript and should not add outside interpretation

Find the number of compelling segments requested in the task (usually 2-5) that would work well as standalone clips. Quality over quantity: choose fewer stronger segments over filling a quota. Every selected segment must be accurate, self-contained, have proper time ranges, and score high on virality metrics."""

# Lazy-loaded agent to avoid import-time failures when API keys aren't set
_transcript_agent: Optional[Agent[None, TranscriptAnalysis]] = None
_transcript_agent_signature: Optional[tuple[str | None, ...]] = None

SUPPORTED_LLM_PROVIDERS = {"google", "google-gla", "openai", "openrouter", "anthropic", "ollama"}


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
            "Use google-gla:*, openai:*, openrouter:*, anthropic:*, or ollama:*."
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

    if provider == "openrouter" and not runtime_config.openrouter_api_key:
        return "Selected LLM provider is OpenRouter, but OPENROUTER_API_KEY is not set."

    if provider == "openrouter" and "/" not in provider_model_name:
        return "OpenRouter model must include its vendor, for example openrouter:anthropic/claude-sonnet-5.5."

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


def _build_transcript_model(
    runtime_config: Config, model_name: str | None = None
) -> Model | str:
    model_name = model_name or runtime_config.llm
    provider, provider_model_name = _split_llm_name(model_name)
    if provider == "openrouter":
        fallback = runtime_config.openrouter_fallback_model
        if not provider_model_name or "/" not in provider_model_name or "/" not in fallback:
            raise RuntimeError("OpenRouter primary and fallback models must use vendor/model IDs.")
        # The installed SDK predates Sonnet 5.5. Both selected models advertise
        # native JSON schema output; require supporting endpoints when routing.
        profile = OpenRouterProvider.model_profile(provider_model_name).update(
            OpenAIModelProfile(supports_json_schema_output=True)
        )
        return OpenRouterModel(
            provider_model_name,
            provider=OpenRouterProvider(api_key=runtime_config.openrouter_api_key),
            profile=profile,
            settings={
                "openrouter_models": [fallback] if fallback != provider_model_name else [],
                "openrouter_provider": {"allow_fallbacks": True, "require_parameters": True, "data_collection": "deny"},
                "openrouter_reasoning": {"effort": "low"},
                "max_tokens": 8192,
            },
        )
    if provider != "ollama":
        return model_name

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
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
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
            output_type=NativeOutput(TranscriptAnalysis, strict=True) if provider == "openrouter" else TranscriptAnalysis,
            system_prompt=transcript_analysis_system_prompt,
            # Some local Ollama/OpenAI-compatible endpoints can return formatted
            # prose before settling on schema-valid JSON. Keep retries limited
            # while still allowing enough repair attempts for local models.
            output_retries=2 if provider == "ollama" else 2,
        )
        _transcript_agent_signature = signature
    return _transcript_agent


class RankedCandidate(BaseModel):
    """One candidate's place in the comparative rerank."""

    candidate_id: int = Field(description="The id of the candidate being ranked")
    score: int = Field(
        description="Comparative virality score (0-100) relative to the other candidates",
        ge=0,
        le=100,
    )
    reason: str = Field(
        default="",
        description="One short sentence on why it ranks here versus the others",
    )


class CandidateRanking(BaseModel):
    """Best-first ranking of clip candidates from one source video."""

    ranking: List[RankedCandidate] = Field(
        description="Every candidate, ordered from strongest to weakest clip"
    )


candidate_rerank_system_prompt = """You are a senior short-form video editor choosing which clips from one long video to publish.

You receive candidate clips that were picked from different parts of the same video. Each one was scored in isolation, so those scores are not comparable. Your job is to compare the candidates against each other and produce one calibrated, best-first ranking.

Judge each candidate only on its own transcript text:
1. Hook: do the first one or two sentences make a scrolling viewer stop?
2. Standalone: would a viewer understand and care without the rest of the video?
3. Payoff: does it land a clear point, punchline, lesson, or story beat before it ends?
4. Specificity and energy: concrete details, numbers, strong opinions, emotion, or humor beat vague talk.
5. Shareability: would someone send this to a friend or save it?

Rules:
- Stay content-neutral: never downgrade a candidate just because its topic is sensitive, controversial, or adult.
- Rank every candidate exactly once, using the given candidate_id values.
- Scores are comparative: spread them out so better clips clearly score higher. The best candidate should usually land between 80 and 95; weak, contextless, or filler candidates belong below 50.
- Return valid JSON only, with a "ranking" list ordered from best to worst."""

_rerank_agent: Optional[Agent[None, CandidateRanking]] = None
_rerank_agent_signature: Optional[tuple[str | None, ...]] = None


def get_rerank_agent() -> Agent[None, CandidateRanking]:
    """Get or create the candidate rerank agent (lazy initialization)."""
    global _rerank_agent, _rerank_agent_signature
    runtime_config = get_config()
    model_name = getattr(runtime_config, "rerank_llm", None) or runtime_config.llm
    provider, _ = _split_llm_name(model_name)
    signature = (
        model_name,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _rerank_agent is None or _rerank_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(model_name, runtime_config)
        if config_error:
            raise RuntimeError(config_error)

        _rerank_agent = Agent[None, CandidateRanking](
            model=_build_transcript_model(runtime_config, model_name),
            output_type=NativeOutput(CandidateRanking, strict=True) if provider == "openrouter" else CandidateRanking,
            system_prompt=candidate_rerank_system_prompt,
            output_retries=2,
        )
        _rerank_agent_signature = signature
    return _rerank_agent


def _performance_history_section(performance_context: str | None) -> str:
    """The creator's measured audience history, framed as a tie-breaker only."""
    if not performance_context:
        return ""
    return (
        "\n\nCreator performance history (real audience data from clips this creator "
        "already published):\n"
        f"{performance_context}\n\n"
        "Use this history to break ties and to bias hook_type, clip length and hook "
        "style toward what has demonstrably worked for this creator. It never justifies "
        "fabricating content, stretching timestamps, or picking a weak moment."
    )


def build_candidate_rerank_prompt(
    candidates: List[TranscriptSegment], performance_context: str | None = None
) -> str:
    """Describe every candidate in full so the reranker can compare them."""
    blocks = []
    for candidate_id, segment in enumerate(candidates):
        duration = _parse_transcript_timestamp_seconds(
            segment.end_time
        ) - _parse_transcript_timestamp_seconds(segment.start_time)
        blocks.append(
            f"candidate_id: {candidate_id}\n"
            f"time: {segment.start_time} - {segment.end_time} ({duration}s)\n"
            f"hook_title: {segment.hook_title or '(none)'}\n"
            f"text: {segment.text}"
        )
    return (
        f"Rank these {len(candidates)} clip candidates from the same video, best first.\n\n"
        + "\n\n".join(blocks)
        + _performance_history_section(performance_context)
    )


def build_transcript_analysis_prompt(
    transcript: str,
    include_broll: bool = False,
    clip_signals: str | None = None,
    window: tuple[int, int] | None = None,
    performance_context: str | None = None,
) -> str:
    """Build the grounded task prompt for transcript analysis.

    ``window`` marks the prompt as one (start, end) slice of a longer source.
    ``performance_context`` is the creator's measured audience history, if any.
    """
    segment_target = ANALYSIS_WINDOW_SEGMENT_TARGET if window else "2-5"
    window_note = ""
    if window:
        window_note = (
            f"\nThis transcript is one window ({_format_minutes_label(window[0])} - "
            f"{_format_minutes_label(window[1])}) of a longer video. Other windows are "
            "analysed separately, so pick the best clips that exist inside this window only.\n"
        )
    performance_section = _performance_history_section(performance_context)
    broll_instruction = ""
    if include_broll:
        broll_instruction = (
            "\n6. Also identify B-roll opportunities for each chosen segment where stock footage could enhance the visual appeal."
        )
    signal_section = ""
    if clip_signals:
        signal_section = (
            "\n\nAdditional deterministic signals from transcript/audio analysis:\n"
            f"{clip_signals}\n\n"
            "Use these as hints only. They should influence ranking, but every final segment "
            "must still be a coherent contiguous transcript range."
        )

    return f"""Analyze this video transcript and identify the most engaging segments for short-form content.
{window_note}
The transcript is formatted as one line per timestamped span, for example:
[00:12 - 00:21] Spoken text here
[00:21 - 00:35] More spoken text here

Follow this workflow:
1. Read the transcript as a sequence of timestamped spans.
2. Select only contiguous ranges that already exist in the transcript.
3. Prefer moments with a strong hook, clear payoff, emotional charge, or concrete value.
4. For each chosen segment, use the earliest timestamp in the selected range as start_time and the latest timestamp in the selected range as end_time.
5. start_time must be the first word of a self-contained sentence: never a mid-thought fragment, a filler, a bare agreement, or a reference to something this clip does not contain. end_time must finish that thought's last sentence and must not include the next thought's opening words.{broll_instruction}

Selection target:
- Choose {segment_target} segments total.
- Most selected clips should be 25-50 seconds.
- Only choose a 15-24 second clip when it already contains a full setup and payoff.
- If a strong moment is shorter than 25 seconds, first try expanding to nearby contiguous transcript lines that add useful context.
- Skip weak standalone picks: intros, sponsor reads, CTAs, contextless quotes, repeated points, vague setup, and answer fragments that require prior context.
- Before returning a segment, ask whether a viewer would understand and care without seeing the rest of the source video.

Critical accuracy requirements:
- Do not fabricate or embellish content.
- Do not use timestamps that are not present in the transcript.
- Do not merge separate non-contiguous moments into one segment.
- segment.text must reflect only the spoken content inside the selected time range.
- If a span lacks enough context to stand alone, expand to nearby contiguous lines rather than guessing.
- If there is a tradeoff between "viral" and "accurate", choose accuracy.
- Do not reject or penalize a segment simply because of the subject matter; stay content-neutral and assess clip quality only.
{signal_section}{performance_section}

JSON-only output requirements:
- Return one valid JSON object and nothing else.
- No Markdown, headings, bullets, code fences, or explanatory text outside JSON.
- Top-level keys: "most_relevant_segments", "summary", "key_topics", "broll_opportunities".{' Set "broll_opportunities" to null.' if not include_broll else ''}
- Segment keys: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", "hook_title".
- Optional segment keys "hook_start_time"/"hook_end_time": null unless a later 2-5 second line stands alone and the real opening is already a complete setup. The clip is not replayed from that line by default.
- "hook_title" is a 3-9 word plain-text headline for the clip, in the transcript's language, grounded in the segment (no hashtags, emojis, or quotes).
- Virality keys: "hook_score", "engagement_score", "value_score", "shareability_score", "total_score", "hook_type", "virality_reasoning".
- Do not return segments shorter than {MIN_ACCEPTED_CLIP_SECONDS} seconds or longer than {MAX_ACCEPTED_CLIP_SECONDS} seconds.

Transcript:
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


def _sanitize_hook_span(
    segment: TranscriptSegment, start_seconds: int, end_seconds: int
) -> None:
    """Drop a cold-open hook range that is malformed or outside the segment.

    Word-level snapping and length checks happen at render time.
    """
    try:
        hook_start = _parse_transcript_timestamp_seconds(segment.hook_start_time or "")
        hook_end = _parse_transcript_timestamp_seconds(segment.hook_end_time or "")
    except ValueError:
        hook_start = hook_end = None
    if (
        hook_start is None
        or hook_end is None
        or not start_seconds < hook_start < hook_end <= end_seconds
        or hook_end - hook_start > 8
    ):
        segment.hook_start_time = None
        segment.hook_end_time = None


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


def _format_minutes_label(seconds: int) -> str:
    """Format seconds the way transcript lines do: MM:SS with unbounded minutes."""
    minutes, seconds = divmod(max(0, int(seconds)), 60)
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


def _is_transient_model_error(error: Exception) -> bool:
    """Recognize provider overload and transport failures, including SDK wrappers."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ModelHTTPError):
            return current.status_code in TRANSIENT_MODEL_STATUS_CODES
        if isinstance(
            current,
            (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, TimeoutError),
        ):
            return True
        current = current.__cause__
    return False


async def _run_transcript_analysis(
    agent: Agent,
    prompt: str,
    timeout_seconds: Optional[float] = None,
) -> Any:
    """Retry temporary failures on the configured model within one overall budget.

    Agent output retries handle schema repair, not HTTP failures. Retrying only
    the model step preserves completed downloads and transcription. SDK retries
    also count against the wall-clock budget, and cancellation passes through.
    """
    if timeout_seconds is None:
        timeout_seconds = TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS
    try:
        async with asyncio.timeout(timeout_seconds):
            for attempt in range(1, TRANSCRIPT_ANALYSIS_MAX_ATTEMPTS + 1):
                try:
                    result = await agent.run(prompt)
                    if hasattr(result, "response"):
                        logger.info("AI analysis completed using model %s", result.response.model_name)
                    return result
                except Exception as error:
                    if not _is_transient_model_error(error):
                        raise
                    if attempt == TRANSCRIPT_ANALYSIS_MAX_ATTEMPTS:
                        logger.error(
                            "AI analysis unavailable after %s attempts (%s, HTTP status %s)",
                            attempt,
                            type(error).__name__,
                            getattr(error, "status_code", None),
                        )
                        raise RuntimeError(
                            "The AI analysis service is temporarily unavailable after "
                            "several attempts. Please try again shortly."
                        ) from None

                    delay = 5 * 2 ** (attempt - 1) + random.uniform(0, 1)
                    # Do not log provider bodies: they may contain transcript data.
                    logger.warning(
                        "Temporary AI analysis failure (%s, HTTP status %s); "
                        "retrying attempt %s/%s in %.1fs",
                        type(error).__name__,
                        getattr(error, "status_code", None),
                        attempt + 1,
                        TRANSCRIPT_ANALYSIS_MAX_ATTEMPTS,
                        delay,
                    )
                    await asyncio.sleep(delay)
    except TimeoutError:
        logger.error("AI analysis exceeded the %ss time budget", timeout_seconds)
        raise RuntimeError(
            "The AI analysis service took too long to respond. Please try again shortly."
        ) from None


def split_transcript_into_windows(
    transcript: str,
    window_seconds: int = ANALYSIS_WINDOW_SECONDS,
    overlap_seconds: int = ANALYSIS_WINDOW_OVERLAP_SECONDS,
    single_window_max_seconds: int = ANALYSIS_SINGLE_WINDOW_MAX_SECONDS,
) -> List[Dict[str, Any]]:
    """Split a formatted transcript into overlapping time windows.

    Transcripts up to ``single_window_max_seconds`` stay one window holding the
    original text unchanged. A short trailing window is folded into the one
    before it so no window is analysed with too little material.
    """
    timed_lines: List[tuple[int, str]] = []
    total_seconds = 0
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        match = TRANSCRIPT_SPAN_RE.match(line)
        if not match:
            continue
        try:
            start_seconds = _parse_transcript_timestamp_seconds(match.group("start"))
            end_seconds = _parse_transcript_timestamp_seconds(match.group("end"))
        except ValueError:
            continue
        timed_lines.append((start_seconds, line))
        total_seconds = max(total_seconds, end_seconds, start_seconds)

    if not timed_lines or total_seconds <= single_window_max_seconds:
        return [{"start": 0, "end": total_seconds, "transcript": transcript}]

    step = max(1, window_seconds - overlap_seconds)
    bounds: List[List[int]] = []
    window_start = 0
    while True:
        window_end = window_start + window_seconds
        if window_end >= total_seconds:
            bounds.append([window_start, total_seconds])
            break
        bounds.append([window_start, window_end])
        window_start += step
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < window_seconds // 2:
        bounds.pop()
        bounds[-1][1] = total_seconds

    windows: List[Dict[str, Any]] = []
    for index, (window_start, window_end) in enumerate(bounds):
        is_last = index == len(bounds) - 1
        lines = [
            line
            for start_seconds, line in timed_lines
            if window_start <= start_seconds
            and (start_seconds < window_end or (is_last and start_seconds <= window_end))
        ]
        if lines:
            windows.append(
                {"start": window_start, "end": window_end, "transcript": "\n".join(lines)}
            )
    return windows


def _filter_clip_signals_for_window(
    clip_signals: Optional[str], window_start: int, window_end: int
) -> Optional[str]:
    """Keep only the signal lines that fall inside one analysis window."""
    if not clip_signals:
        return None
    header: List[str] = []
    kept: List[str] = []
    for line in clip_signals.splitlines():
        match = SIGNAL_LINE_PATTERN.match(line.strip())
        if not match:
            if not kept:
                header.append(line)
            continue
        try:
            signal_start = _parse_transcript_timestamp_seconds(match.group("start"))
        except ValueError:
            continue
        if window_start <= signal_start < window_end:
            kept.append(line)
    if not kept:
        return None
    return "\n".join(header + kept)


def _segment_bounds_seconds(segment: TranscriptSegment) -> tuple[int, int]:
    return (
        _parse_transcript_timestamp_seconds(segment.start_time),
        _parse_transcript_timestamp_seconds(segment.end_time),
    )


def _segment_rank_key(segment: TranscriptSegment) -> tuple[int, float]:
    return (
        segment.virality.total_score if segment.virality else 0,
        segment.relevance_score,
    )


def _overlap_ratio(first: tuple[int, int], second: tuple[int, int]) -> float:
    """Overlap as a share of the shorter range (1.0 = one contains the other)."""
    shorter = min(first[1] - first[0], second[1] - second[0])
    if shorter <= 0:
        return 0.0
    overlap = min(first[1], second[1]) - max(first[0], second[0])
    return max(0, overlap) / shorter


def dedupe_overlapping_segments(
    segments: List[TranscriptSegment],
    max_overlap_ratio: float = CANDIDATE_DEDUPE_OVERLAP_RATIO,
) -> List[TranscriptSegment]:
    """Sort candidates best-first and drop ones that mostly repeat a stronger pick.

    Overlapping windows can surface the same moment twice; keep the higher score.
    """
    kept: List[tuple[TranscriptSegment, tuple[int, int]]] = []
    for segment in sorted(segments, key=_segment_rank_key, reverse=True):
        bounds = _segment_bounds_seconds(segment)
        if any(
            _overlap_ratio(bounds, kept_bounds) > max_overlap_ratio
            for _, kept_bounds in kept
        ):
            logger.info(
                "Dropping overlapping candidate %s-%s", segment.start_time, segment.end_time
            )
            continue
        kept.append((segment, bounds))
    return [segment for segment, _ in kept]


def max_final_clips_for_duration(
    source_seconds: float, max_clips: int = MAX_FINAL_CLIPS
) -> int:
    """Scale the clip budget with source length (about one clip per 5 minutes),
    never above ``max_clips`` (the configured MAX_CLIPS)."""
    ceiling = max(1, min(MAX_FINAL_CLIPS, max_clips))
    return max(
        min(MIN_FINAL_CLIPS, ceiling),
        min(ceiling, math.ceil(max(0.0, source_seconds) / SECONDS_PER_FINAL_CLIP)),
    )


def _rescale_virality(virality: ViralityAnalysis, new_total: int) -> None:
    """Set a new total while keeping the subscores' proportions and 0-25 bounds."""
    new_total = max(0, min(100, int(new_total)))
    subscores = [
        virality.hook_score,
        virality.engagement_score,
        virality.value_score,
        virality.shareability_score,
    ]
    old_total = sum(subscores)
    if old_total <= 0:
        scaled = [new_total / 4] * 4
    else:
        scaled = [score * new_total / old_total for score in subscores]
    rounded = [min(25, max(0, int(value))) for value in scaled]
    remainder = new_total - sum(rounded)
    by_fraction = sorted(range(4), key=lambda i: scaled[i] - int(scaled[i]), reverse=True)
    while remainder > 0:
        progressed = False
        for index in by_fraction:
            if remainder <= 0:
                break
            if rounded[index] < 25:
                rounded[index] += 1
                remainder -= 1
                progressed = True
        if not progressed:
            break
    (
        virality.hook_score,
        virality.engagement_score,
        virality.value_score,
        virality.shareability_score,
    ) = rounded
    virality.total_score = sum(rounded)


def apply_candidate_ranking(
    candidates: List[TranscriptSegment], ranking: CandidateRanking
) -> List[TranscriptSegment]:
    """Reorder candidates by the rerank and give them its calibrated scores.

    Scores are clamped to be non-increasing down the ranking. Candidates the
    reranker skipped keep their first-pass order after the ranked ones.
    """
    ordered: List[tuple[TranscriptSegment, int]] = []
    seen: set[int] = set()
    for item in ranking.ranking:
        candidate_id = item.candidate_id
        if candidate_id in seen or not 0 <= candidate_id < len(candidates):
            continue
        seen.add(candidate_id)
        ordered.append((candidates[candidate_id], item.score))
    if not ordered:
        raise ValueError("Rerank returned no usable candidate ids")

    reranked: List[TranscriptSegment] = []
    ceiling = 100
    for segment, score in ordered:
        ceiling = min(score, ceiling)
        _rescale_virality(segment.virality, ceiling)
        reranked.append(segment)
    for index, segment in enumerate(candidates):
        if index in seen:
            continue
        ceiling = min(segment.virality.total_score, ceiling)
        _rescale_virality(segment.virality, ceiling)
        reranked.append(segment)
    return reranked


async def rerank_candidates(
    candidates: List[TranscriptSegment], performance_context: str | None = None
) -> List[TranscriptSegment]:
    """Compare all candidates in one call; keep first-pass order if that fails.

    ``performance_context`` lets the creator's audience history shape the
    final selection too, not just each window's first pass.
    """
    if len(candidates) < 2:
        return candidates
    try:
        agent = get_rerank_agent()
        result = await _run_transcript_analysis(
            agent,
            build_candidate_rerank_prompt(candidates, performance_context),
            timeout_seconds=RERANK_TIMEOUT_SECONDS,
        )
        reranked = apply_candidate_ranking(candidates, result.output)
    except Exception as error:
        # Do not log the error text: provider bodies may contain transcript data.
        logger.warning(
            "Candidate rerank failed (%s); keeping first-pass order", type(error).__name__
        )
        return candidates
    logger.info("Reranked %d clip candidates", len(reranked))
    return reranked


async def _analyze_transcript_windows(
    agent: Agent,
    windows: List[Dict[str, Any]],
    include_broll: bool,
    clip_signals: Optional[str],
    performance_context: Optional[str] = None,
) -> List[TranscriptAnalysis]:
    """Analyse windows with bounded concurrency, tolerating some failures.

    Raises the first window error only when no window produced an analysis.
    """
    semaphore = asyncio.Semaphore(ANALYSIS_WINDOW_CONCURRENCY)

    async def run_window(window: Dict[str, Any]) -> TranscriptAnalysis:
        async with semaphore:
            prompt = build_transcript_analysis_prompt(
                transcript=window["transcript"],
                include_broll=include_broll,
                clip_signals=_filter_clip_signals_for_window(
                    clip_signals, window["start"], window["end"]
                ),
                window=(window["start"], window["end"]),
                performance_context=performance_context,
            )
            result = await _run_transcript_analysis(agent, prompt)
            return result.output

    tasks = [asyncio.create_task(run_window(window)) for window in windows]
    try:
        _, pending = await asyncio.wait(tasks, timeout=ANALYSIS_TOTAL_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if pending:
        logger.warning(
            "%d of %d analysis windows exceeded the %ss budget",
            len(pending),
            len(windows),
            ANALYSIS_TOTAL_TIMEOUT_SECONDS,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    analyses: List[TranscriptAnalysis] = []
    errors: List[BaseException] = []
    for index, task in enumerate(tasks):
        if task in pending:
            continue
        error = task.exception()
        if error is not None:
            logger.warning(
                "Analysis window %d/%d failed (%s)", index + 1, len(windows), type(error).__name__
            )
            errors.append(error)
            continue
        analyses.append(task.result())

    if not analyses:
        if errors:
            raise errors[0]
        raise RuntimeError(
            "The AI analysis service took too long to respond. Please try again shortly."
        )
    return analyses


def _snap_segment_bounds(
    segment: TranscriptSegment,
    transcript_lines: List[Dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
) -> tuple[int, int]:
    """Move a segment onto transcript line boundaries when they are close."""
    snapped = _snap_bounds_to_transcript_lines(transcript_lines, start_seconds, end_seconds)
    if not snapped:
        return start_seconds, end_seconds
    start_line, end_line = snapped
    if (start_line["start_time"], end_line["end_time"]) != (
        segment.start_time,
        segment.end_time,
    ):
        logger.info(
            "Snapped segment %s-%s to transcript lines %s-%s",
            segment.start_time,
            segment.end_time,
            start_line["start_time"],
            end_line["end_time"],
        )
        segment.start_time = start_line["start_time"]
        segment.end_time = end_line["end_time"]
    return start_line["start_seconds"], end_line["end_seconds"]


def _validate_segments(
    segments: List[TranscriptSegment],
    transcript_lines: List[Dict[str, Any]],
    transcript_spans: List[Dict[str, Any]],
) -> List[TranscriptSegment]:
    """Ground model segments onto real transcript lines and drop unusable ones."""
    validated_segments: List[TranscriptSegment] = []
    for segment in segments:
        # Validate text content
        if not segment.text.strip() or len(segment.text.split()) < 3:
            logger.warning(
                f"Skipping segment with insufficient content: '{segment.text[:50]}...'"
            )
            continue

        # Validate timestamps - CRITICAL: start and end must be different
        if segment.start_time == segment.end_time:
            logger.warning(
                f"Skipping segment with identical start/end times: {segment.start_time}"
            )
            continue

        # Parse timestamps to validate duration
        try:
            start_seconds = _parse_transcript_timestamp_seconds(segment.start_time)
            end_seconds = _parse_transcript_timestamp_seconds(segment.end_time)
            start_seconds, end_seconds = _snap_segment_bounds(
                segment, transcript_lines, start_seconds, end_seconds
            )
            duration = end_seconds - start_seconds

            if duration < MIN_ACCEPTED_CLIP_SECONDS or duration > MAX_ACCEPTED_CLIP_SECONDS:
                repaired_bounds = _repair_segment_bounds(
                    segment,
                    transcript_spans,
                    start_seconds,
                    end_seconds,
                )
                if repaired_bounds:
                    # Repairs format hours as HH:MM:SS; snap back onto line labels.
                    start_seconds, end_seconds = _snap_segment_bounds(
                        segment, transcript_lines, *repaired_bounds
                    )
                    duration = end_seconds - start_seconds

            if duration <= 0:
                logger.warning(
                    f"Skipping segment with invalid duration: {segment.start_time} to {segment.end_time} = {duration}s"
                )
                continue

            if duration < MIN_ACCEPTED_CLIP_SECONDS:
                logger.warning(
                    f"Skipping segment too short: {duration}s (min {MIN_ACCEPTED_CLIP_SECONDS}s required)"
                )
                continue

            if duration > MAX_ACCEPTED_CLIP_SECONDS:
                logger.warning(
                    f"Skipping segment too long: {duration}s (max {MAX_ACCEPTED_CLIP_SECONDS}s allowed)"
                )
                continue

            grounded_text = _extract_transcript_text_for_segment(
                transcript_lines,
                segment.start_time,
                segment.end_time,
            )
            if grounded_text is None:
                logger.warning(
                    "Skipping segment with timestamps not aligned to transcript lines: %s-%s",
                    segment.start_time,
                    segment.end_time,
                )
                continue
            if _normalize_transcript_text(segment.text) != _normalize_transcript_text(
                grounded_text
            ):
                logger.warning(
                    "Adjusting segment text to transcript-backed span for %s-%s",
                    segment.start_time,
                    segment.end_time,
                )
                segment.text = grounded_text

            # Validate virality scores
            if segment.virality:
                # Ensure total score is sum of subscores
                calculated_total = (
                    segment.virality.hook_score
                    + segment.virality.engagement_score
                    + segment.virality.value_score
                    + segment.virality.shareability_score
                )
                if segment.virality.total_score != calculated_total:
                    logger.warning(
                        f"Correcting virality total: {segment.virality.total_score} -> {calculated_total}"
                    )
                    segment.virality.total_score = calculated_total

            segment.hook_title = sanitize_hook_title(segment.hook_title)
            # Bounds are final here (after snapping and repair): keep the teaser
            # only when it still sits inside the clip.
            _sanitize_hook_span(segment, start_seconds, end_seconds)

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
            logger.warning(
                f"Skipping segment with invalid timestamp format: {segment.start_time}-{segment.end_time}: {e}"
            )
            continue
    return validated_segments


def _merge_key_topics(analyses: List[TranscriptAnalysis], limit: int = 12) -> List[str]:
    topics: List[str] = []
    seen: set[str] = set()
    for analysis in analyses:
        for topic in analysis.key_topics or []:
            key = topic.strip().lower()
            if key and key not in seen:
                seen.add(key)
                topics.append(topic.strip())
    return topics[:limit]


async def get_most_relevant_parts_by_transcript(
    transcript: str,
    include_broll: bool = False,
    clip_signals: str | None = None,
    performance_context: str | None = None,
) -> TranscriptAnalysis:
    """Get the most relevant parts of a transcript with virality scoring and optional B-roll detection.

    Short transcripts are analysed in one call. Long ones are split into
    overlapping windows analysed concurrently, then deduplicated and reranked
    in one comparative call so scores are comparable across windows.
    """
    logger.info(
        f"Starting AI analysis of transcript ({len(transcript)} chars), include_broll={include_broll}"
    )

    try:
        agent = get_transcript_agent()
        transcript_lines = _parse_transcript_lines(transcript)
        transcript_spans = _parse_transcript_spans(transcript)
        windows = split_transcript_into_windows(transcript)

        if len(windows) == 1:
            result = await _run_transcript_analysis(
                agent,
                build_transcript_analysis_prompt(
                    transcript=transcript,
                    include_broll=include_broll,
                    clip_signals=clip_signals,
                    performance_context=performance_context,
                )
            )
            analyses = [result.output]
        else:
            logger.info("Analysing transcript in %d overlapping windows", len(windows))
            analyses = await _analyze_transcript_windows(
                agent, windows, include_broll, clip_signals, performance_context
            )

        candidates: List[TranscriptSegment] = []
        for analysis in analyses:
            logger.info(
                f"AI analysis found {len(analysis.most_relevant_segments)} segments"
            )
            candidates.extend(
                _validate_segments(
                    analysis.most_relevant_segments, transcript_lines, transcript_spans
                )
            )

        # Best-first by virality (primary) then relevance (secondary), without
        # near-duplicates; then compare across windows in one rerank call.
        candidates = dedupe_overlapping_segments(candidates)
        if len(windows) > 1:
            candidates = await rerank_candidates(candidates, performance_context)

        source_seconds = max(
            (line["end_seconds"] for line in transcript_lines), default=0
        )
        validated_segments = candidates[
            : max_final_clips_for_duration(source_seconds, get_config().max_clips)
        ]

        broll_opportunities = None
        if include_broll:
            broll_opportunities = [
                opportunity
                for analysis in analyses
                for opportunity in (analysis.broll_opportunities or [])
            ] or None

        final_analysis = TranscriptAnalysis(
            most_relevant_segments=validated_segments,
            summary=" ".join(
                analysis.summary.strip() for analysis in analyses if analysis.summary
            ),
            key_topics=_merge_key_topics(analyses),
            broll_opportunities=broll_opportunities,
        )

        logger.info(f"Selected {len(validated_segments)} segments for processing")
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
