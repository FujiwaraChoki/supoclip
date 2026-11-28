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
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.profiles.anthropic import AnthropicModelProfile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic import AliasChoices, BaseModel, Field, field_validator

from .config import Config, get_config
from .clip_duration import clip_duration
from .runtime_settings import apply_settings_to_process_env
from .workflows.prompts import instructions_block

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
# Two clips of one video may share a little footage at their edges, not a third of the shorter one:
# a viewer of both would watch the same moment twice.
CANDIDATE_DEDUPE_OVERLAP_RATIO = 1 / 3
# A pick that sits between two stronger ones can stay under that limit with each and still be mostly
# footage they already show.
CANDIDATE_DEDUPE_COVERED_RATIO = 0.5
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


def short_source_speech_bounds(
    transcript: str, source_duration: float | None, clip_length: str,
) -> tuple[int, int] | None:
    """Return the spoken range when the source is already within the clip length cap."""
    maximum = clip_duration(clip_length).maximum
    if source_duration is None or not 0 < source_duration <= maximum:
        return None
    lines = [line for line in _parse_transcript_lines(transcript) if line["text"]]
    if sum(len(line["text"].split()) for line in lines) < 3:
        return None
    start = min(line["start_seconds"] for line in lines)
    end = max(line["end_seconds"] for line in lines)
    if start < end <= math.ceil(source_duration):
        return start, end
    return None


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


class TranscriptAnalysis(BaseModel):
    """Analysis result for transcript segments with virality scores."""

    most_relevant_segments: List[TranscriptSegment]
    summary: str = Field(description="Brief summary of the video content")
    key_topics: List[str] = Field(description="List of main topics discussed")


# Enhanced system prompt with virality scoring
transcript_analysis_system_prompt = """You are an expert transcript analyst for short-form video editing.

Your job is extraction and ranking, not creative rewriting. You must stay fully grounded in the transcript and choose the best clip candidates that already exist in the source material.

OUTPUT CONTRACT:
- Return valid JSON only. Do not output Markdown, headings, bullets, prose, code fences, explanations, or commentary outside the JSON object.
- The top-level JSON object must include: "most_relevant_segments", "summary", and "key_topics".
- Each item in "most_relevant_segments" must include: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", and "hook_title".
- Do not use "segment" as an output field. Use "text".
- "virality" must include: "hook_score", "engagement_score", "value_score", "shareability_score", "total_score", "hook_type", and "virality_reasoning".
- Every returned segment must be {minimum}-{maximum} seconds long. Prefer {ideal_minimum}-{ideal_maximum} seconds.

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
- Sentence case: capitalise only the first word, names and acronyms, never Every Word
- Keep the sign on a number exactly as it was said: 90%, $40k, £4,000
- Write it in the same language as the transcript
- Good examples: "The $40k mistake I keep seeing", "Why nobody tells you this about VC", "Do this before your next interview"

CLIP BOUNDARIES — OPENING AND ENDING:
- start_time is the first word of a sentence a new viewer can understand with no prior context
- Do not start mid-sentence, on a filler (like, dude, um, well), on a bare agreement (yes, yeah, right, exactly, totally), or on a pronoun that points at someone the viewer has not met yet (he, she, they)
- Do not start on a back-reference ("as I said", "the thing we mentioned", "that point", "before we started recording") unless the clip also contains the thing being referenced
- If the moment only makes sense with the previous sentence's noun, claim, or question, move start_time back to include that sentence
- end_time is the last word of the sentence that lands the point. Do not stop mid-clause, and do not keep the opening words of the next unrelated thought
- The clip's real first sentence is the hook. Do not choose a weak opening because a later line could be replayed in front of it
- Judge the first spoken seconds on their own, separately from hook_title: a viewer who hears only them must know the subject or claim
- Skip lead-in banter before the point (calling someone over, "No. No? Ah!", greetings, reactions to something off-screen) and start on the sentence that delivers it
- Do not start on a one-word answer to a question the clip leaves out ("Catharsis."), or on a conjunction that continues an earlier point ("But the main reason...", "And then...", "So that's why..."); start where the thought stands alone, or include the question or claim it answers
- Lower hook_score for a segment whose opening still depends on missing context

VISUAL SUITABILITY:
- The clip is a video. Avoid segments where the speech says the picture is interrupted, unavailable or broken: a technical problem, "these images cannot be shown", "we'll be right back", a frozen or lost stream, a test or placeholder screen
- Those passages usually play over a still slate or black, so they are not ready-to-post highlights no matter how strong the words are

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

TIMING GUIDELINES:
- Target {ideal_minimum}-{ideal_maximum} seconds for most clips
- Shorter picks within the requested range must still be self-contained and complete
- CRITICAL: start_time MUST be different from end_time (minimum {minimum} seconds apart)
- Focus on natural content boundaries rather than arbitrary time limits
- Include enough context for the segment to be understandable
- Stay within the requested duration range
- Start at the hook or the minimum setup needed to make the hook land, and end after the payoff
- If a highlight is only one good line, expand to include the surrounding setup and payoff rather than returning a tiny fragment
- Stop expanding when the topic drifts, the speaker repeats the same point, or the clip loses momentum

TIMESTAMP REQUIREMENTS - EXTREMELY IMPORTANT:
- Use EXACT timestamps as they appear in the transcript
- Never modify timestamp format (keep MM:SS structure)
- start_time MUST be LESS THAN end_time (start_time < end_time)
- MINIMUM segment duration: {minimum} seconds (end_time - start_time >= {minimum} seconds)
- MAXIMUM segment duration: {maximum} seconds
- IDEAL segment duration: {ideal_minimum}-{ideal_maximum} seconds
- Look at transcript ranges like [02:25 - 02:35] and use different start/end times
- NEVER use the same timestamp for both start_time and end_time
- Example: start_time: "02:25", end_time: "02:35" (NOT "02:25" and "02:25")

SCORING AND OUTPUT RULES:
- relevance_score should reflect how well the segment works as a standalone short clip, not just whether the topic is generally important
- Penalize clips that are only quotable but not self-contained, too generic, missing setup, missing payoff, or padded with filler
- virality_reasoning and reasoning should cite what is actually present in the chosen span
- summary and key_topics must also stay grounded in the transcript and should not add outside interpretation

Find the number of compelling segments requested in the task (usually 2-5) that would work well as standalone clips. Quality over quantity: choose fewer stronger segments over filling a quota. Every selected segment must be accurate, self-contained, have proper time ranges, and score high on virality metrics.

When the task identifies an already-short source, follow its selection target and feasible duration range. Evaluate the speech that is actually available, even if the source begins or ends in the middle of a longer exchange. A modest virality score is valid for an otherwise usable short selection."""

def build_transcript_system_prompt(clip_length: str = "auto") -> str:
    bounds = clip_duration(clip_length)
    return transcript_analysis_system_prompt.format(
        minimum=bounds.minimum, maximum=bounds.maximum,
        ideal_minimum=bounds.ideal_minimum, ideal_maximum=bounds.ideal_maximum,
    )


# Lazy-loaded agent to avoid import-time failures when API keys aren't set
_transcript_agent: Optional[Agent[None, TranscriptAnalysis]] = None
_transcript_agent_signature: Optional[tuple[str | None, ...]] = None

SUPPORTED_LLM_PROVIDERS = {
    "google", "google-gla", "openai", "nvidia", "openrouter", "anthropic", "ollama"
}
NVIDIA_API_BASE_URL = "https://integrate.api.nvidia.com/v1"


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
            "Use google-gla:*, openai:*, nvidia:*, openrouter:*, anthropic:*, or ollama:*."
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

    if provider == "nvidia" and not runtime_config.nvidia_api_key:
        return (
            "Selected LLM provider is NVIDIA, but NVIDIA_API_KEY is not set. "
            "Set NVIDIA_API_KEY or choose another provider with a matching API key."
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


# Anthropic models the installed SDK has no profile for. They support native
# structured output; without it, output falls back to a tool call with the
# SDK's 4096-token cap, which cuts long schemas such as the source outline short.
_NATIVE_OUTPUT_ANTHROPIC_MODELS = frozenset({"claude-sonnet-5-5", "claude-haiku-5-5"})


def _uses_native_output(model_name: str) -> bool:
    provider, name = _split_llm_name(model_name)
    return provider == "openrouter" or (
        provider == "anthropic" and name in _NATIVE_OUTPUT_ANTHROPIC_MODELS
    )


def _build_transcript_model(
    runtime_config: Config, model_name: str | None = None
) -> Model | str:
    model_name = model_name or runtime_config.llm
    provider, provider_model_name = _split_llm_name(model_name)
    if provider == "anthropic" and _uses_native_output(model_name):
        # The installed SDK predates Sonnet 5.5 and Haiku 5.5. Sonnet 5.5 rejects
        # forced tool calls; both need native structured output.
        profile = AnthropicProvider.model_profile(provider_model_name).update(
            AnthropicModelProfile(supports_json_schema_output=True)
        )
        return AnthropicModel(
            provider_model_name,
            provider=AnthropicProvider(api_key=runtime_config.anthropic_api_key),
            profile=profile,
            settings={"max_tokens": 8192},
        )
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

    if provider == "nvidia":
        return OpenAIModel(
            provider_model_name,
            provider=OpenAIProvider(
                base_url=NVIDIA_API_BASE_URL,
                api_key=runtime_config.nvidia_api_key,
            ),
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


def get_transcript_agent(clip_length: str = "auto") -> Agent[None, TranscriptAnalysis]:
    """Get or create the transcript analysis agent (lazy initialization)."""
    global _transcript_agent, _transcript_agent_signature
    runtime_config = get_config()
    provider, _ = _split_llm_name(runtime_config.llm)
    signature = (
        clip_length,
        runtime_config.llm,
        runtime_config.openai_api_key,
        runtime_config.nvidia_api_key,
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
            output_type=NativeOutput(TranscriptAnalysis, strict=True) if _uses_native_output(runtime_config.llm) else TranscriptAnalysis,
            system_prompt=build_transcript_system_prompt(clip_length),
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
1. Hook: do the first one or two sentences make a scrolling viewer stop? Judge them apart from hook_title; lead-in banter, a lone one-word answer, or an opening conjunction ("But the main reason...") that leans on unseen context is a weak hook.
2. Standalone: would a viewer understand and care without the rest of the video?
3. Payoff: does it land a clear point, punchline, lesson, or story beat before it ends?
4. Specificity and energy: concrete details, numbers, strong opinions, emotion, or humor beat vague talk.
5. Shareability: would someone send this to a friend or save it?

Rules:
- Stay content-neutral: never downgrade a candidate just because its topic is sensitive, controversial, or adult.
- Rank a candidate last when its speech says the picture is interrupted, unavailable or broken (a technical problem, "these images cannot be shown"); it plays over a slate, not footage.
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
        runtime_config.nvidia_api_key,
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
            output_type=NativeOutput(CandidateRanking, strict=True) if _uses_native_output(model_name) else CandidateRanking,
            system_prompt=candidate_rerank_system_prompt,
            output_retries=2,
        )
        _rerank_agent_signature = signature
    return _rerank_agent


class ClipEdges(BaseModel):
    """Where one clip should open and close, as sentence ids from the lists it was shown."""

    clip: int = Field(description="The clip number as given")
    opening_problem: str = Field(
        description="What is wrong with the current opening: none, answers_unseen_line, unnamed_reference, "
                    "continues_earlier_point, leftover, or mid_sentence")
    closing_problem: str = Field(
        description="What is wrong with the current closing: none, unanswered_question, missing_payoff, "
                    "next_topic, or fragment")
    opening_id: int = Field(description="Id of the sentence the clip opens on, from its OPENING list")
    closing_id: int = Field(description="Id of the sentence the clip closes on, from its CLOSING list")


class ClipEdgeReview(BaseModel):
    """Opening and closing sentence for every clip in the request."""

    clips: List[ClipEdges] = Field(description="One entry per clip, in the order given")


clip_edge_system_prompt = """You are a short-form video editor checking where a clip starts and ends. A viewer sees only the clip: nothing that was said before it, nothing that is said after it.

The clip was cut from a longer video on whole sentences. You get what the clip is about (its title and why it was picked), then numbered sentences around its current first sentence (OPENING) and around its current last sentence (CLOSING), in the order they are spoken, with the speaker when known. Sentences marked "in clip" are currently part of the clip; the others are just before or just after it. First name what is wrong with each edge, then choose the sentence the clip should open on and the sentence it should close on.

OPENING. The first sentence a viewer hears must make sense on its own and belong to what the clip is about. Problems, and the fix:
- answers_unseen_line: it answers, echoes or reacts to a line that is not in the clip (a bare reply, a question that repeats what somebody just said, "that is what I mean"). Open on that line.
- unnamed_reference: it points at someone or something only an earlier sentence names ("he", "she", "they", "their", "it", "that", "in there", "this guy") and the clip's first two sentences never say who or what. Open on the nearest earlier sentence that names it.
- continues_earlier_point: it starts with a connector that carries on an earlier point ("but", "so", "because", "and then", "which", "that is", and their equivalents in the clip's language) and does not make sense without that point. Open on the sentence it continues.
- leftover: the first sentence or two belong to the topic before, or are a scrap of it (a bare agreement, a laugh, filler, the end of another story), and what the clip is about starts after them. Open on the sentence where the clip's own subject starts.
- mid_sentence: it is the second half of a sentence whose first half is just before it. Open on the first half.

CLOSING. The last sentence must finish the thought the clip is about. Problems, and the fix:
- unanswered_question: it is a question, or a line said to somebody, and the answer or reply follows (a short reply by the other person counts). Close on the answer.
- missing_payoff: the clip builds to something (what the title promises, a reveal, a punchline, the result of an attempt, a count such as "one..." with "two" right after) and it is said in the sentences that follow. Close on the sentence that delivers it.
- next_topic: it only announces the next topic, thanks the viewer or asks them to subscribe. Close one sentence earlier.
- fragment: it is a trailing fragment that is not a finished thought. Close one sentence earlier, or on the sentence that finishes it.

Rules:
- Make the smallest change that fixes the problem. Never open earlier or close later than needed, and never open on a sentence that itself needs earlier context when a nearby one stands alone.
- Do not add sentences that start a new, unrelated thought.
- Use only ids from the clip's own lists: opening_id from OPENING, closing_id from CLOSING.
- When an edge has no problem, say "none" and return its current id. Many clips need no change; do not move an edge that is fine.
- Judge only what the sentences say. Stay content-neutral.
- Return valid JSON only, with a "clips" list holding one entry per clip."""

CLIP_EDGE_TIMEOUT_SECONDS = 60
CLIP_EDGE_CONCURRENCY = 4

_edge_agent: Optional[Agent[None, ClipEdgeReview]] = None
_edge_agent_signature: Optional[tuple[str | None, ...]] = None


def get_clip_edge_agent() -> Agent[None, ClipEdgeReview]:
    """Get or create the clip-edge review agent (lazy initialization)."""
    global _edge_agent, _edge_agent_signature
    runtime_config = get_config()
    model_name = (getattr(runtime_config, "clip_edge_llm", None)
                  or getattr(runtime_config, "rerank_llm", None) or runtime_config.llm)
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
    if _edge_agent is None or _edge_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(model_name, runtime_config)
        if config_error:
            raise RuntimeError(config_error)
        _edge_agent = Agent[None, ClipEdgeReview](
            model=_build_transcript_model(runtime_config, model_name),
            output_type=NativeOutput(ClipEdgeReview, strict=True) if _uses_native_output(model_name) else ClipEdgeReview,
            system_prompt=clip_edge_system_prompt,
            output_retries=2,
        )
        _edge_agent_signature = signature
    return _edge_agent


def build_clip_edge_prompt(clips: List[Dict[str, Any]]) -> str:
    """The request for ``review_clip_edges``: per clip what it is about and the two sentence lists.

    Each clip is ``{"clip": n, "title": str, "about": str, "opening": [...], "closing": [...], "first": id, "last": id}``
    with sentences as ``{"id": int, "speaker": str | None, "text": str, "in_clip": bool}``.
    """
    def listing(sentences: List[Dict[str, Any]]) -> str:
        lines = []
        for sentence in sentences:
            speaker = f"[{sentence['speaker']}] " if sentence.get("speaker") else ""
            mark = " (in clip)" if sentence.get("in_clip") else ""
            lines.append(f"#{sentence['id']}{mark} {speaker}{sentence['text']}")
        return "\n".join(lines)

    parts = []
    for clip in clips:
        about = "".join(
            f"{label}: {clip[key]}\n" for label, key in (("Title", "title"), ("Why it was picked", "about"))
            if clip.get(key)
        )
        parts.append(
            f"CLIP {clip['clip']}\n{about}"
            f"OPENING sentences (current opening: #{clip['first']}):\n{listing(clip['opening'])}\n"
            f"CLOSING sentences (current closing: #{clip['last']}):\n{listing(clip['closing'])}"
        )
    return "Check these clips and return the opening and closing sentence id for each.\n\n" + "\n\n".join(parts)


async def review_clip_edges(clips: List[Dict[str, Any]]) -> Dict[int, tuple[int, int]]:
    """Ask the model where each clip should open and close: {clip number: (opening id, closing id)}.

    One small call per clip, a few at a time: asked about a dozen clips at once the model waves them all
    through. A clip whose call fails is left out (its resolved edges stand); ids outside a clip's own lists
    are dropped here, everything else (sentence starts, durations) is checked by the caller.
    """
    if not clips:
        return {}
    try:
        agent = get_clip_edge_agent()
    except Exception as error:
        logger.warning("Clip edge review unavailable (%s); keeping the resolved edges", type(error).__name__)
        return {}
    gate = asyncio.Semaphore(CLIP_EDGE_CONCURRENCY)

    async def review(clip: Dict[str, Any]) -> Optional[tuple[int, int]]:
        async with gate:
            try:
                result = await _run_transcript_analysis(
                    agent, build_clip_edge_prompt([clip]), timeout_seconds=CLIP_EDGE_TIMEOUT_SECONDS)
            except Exception as error:
                # Do not log the error text: provider bodies may contain transcript data.
                logger.warning("Clip edge review failed (%s); keeping the resolved edges", type(error).__name__)
                return None
        opening = {sentence["id"] for sentence in clip["opening"]}
        closing = {sentence["id"] for sentence in clip["closing"]}
        for item in result.output.clips:
            if item.opening_id in opening and item.closing_id in closing:
                return item.opening_id, item.closing_id
        return None

    answers = await asyncio.gather(*(review(clip) for clip in clips))
    return {clip["clip"]: answer for clip, answer in zip(clips, answers) if answer is not None}


class BRollCue(BaseModel):
    """One stock-footage cutaway inside a finished clip."""

    start: float = Field(
        description="Clip second where the cutaway starts: the timestamp of the line that names the subject"
    )
    duration: float = Field(description="Seconds to show the footage, 2 to 4")
    query: str = Field(
        description="2-4 word English stock-footage search describing what the footage shows"
    )
    fallback_query: str = Field(
        default="",
        description="Broader 1-2 word English search used when the first finds nothing",
    )


class BRollPlan(BaseModel):
    """Stock-footage cutaways for one clip, in play order."""

    cues: List[BRollCue] = Field(description="Cutaways in play order; empty when nothing fits")


broll_plan_system_prompt = """You are a short-form video editor adding stock-footage cutaways (B-roll) to a vertical talking-head clip.

The footage plays full screen for a few seconds while the speaker keeps talking and the captions stay on screen. Good B-roll shows exactly the thing being said at that moment. Loose or generic footage makes a clip feel cheap, so leave a moment uncovered rather than force a weak match.

Choose a moment only when the speaker names something concrete and filmable: an object, a place, an activity, or a scene (for example "biking to work", "Mount Kilimanjaro", "a stack of cash", "writing a novel").
Skip abstract ideas, opinions, feelings without a scene, jokes, and anything about the speaker themselves on camera.
Never cover a moment where the speaker points at something the viewer is meant to see ("like this", "look at this", "here's what it looked like", "this slide"): the original picture is the payoff there.

Rules:
- "start" is the timestamp of the line that names the subject, so the footage appears as the word is spoken.
- "duration" is 2 to 4 seconds and must end before the next cutaway starts.
- "query" is a 2-4 word English description of what the footage shows ("man cycling city street", "mountain summit hikers", "hands typing laptop"), even when the clip is in another language. No people's names, brands, logos, text, charts or screenshots: stock libraries do not have them.
- Match the tone of what is said. Describe the scene literally and neutrally ("dark quiet bedroom", not "lonely night"), and never imply illness, pills, drugs, distress, violence or death unless the speaker is talking about exactly that: stock footage tagged that way changes what the clip seems to say.
- "fallback_query" is a broader 1-2 word English search ("cycling", "mountain").
- Never place a cutaway in the opening seconds or the final second: the viewer needs to see the speaker.
- Return valid JSON only, with a "cues" list. Return an empty list when no moment has a concrete, filmable subject."""

_broll_agent: Optional[Agent[None, BRollPlan]] = None
_broll_agent_signature: Optional[tuple[str | None, ...]] = None
BROLL_PLAN_TIMEOUT_SECONDS = 60


def get_broll_agent() -> Agent[None, BRollPlan]:
    """Get or create the B-roll planning agent (lazy initialization)."""
    global _broll_agent, _broll_agent_signature
    runtime_config = get_config()
    model_name = runtime_config.llm
    signature = (
        model_name,
        runtime_config.openai_api_key,
        runtime_config.nvidia_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _broll_agent is None or _broll_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(model_name, runtime_config)
        if config_error:
            raise RuntimeError(config_error)
        _broll_agent = Agent[None, BRollPlan](
            model=_build_transcript_model(runtime_config, model_name),
            output_type=NativeOutput(BRollPlan, strict=True) if _uses_native_output(model_name) else BRollPlan,
            system_prompt=broll_plan_system_prompt,
            output_retries=2,
        )
        _broll_agent_signature = signature
    return _broll_agent


def build_broll_plan_prompt(
    timed_lines: str, clip_duration: float, max_cues: int, earliest_start: float
) -> str:
    """Ask for cutaways over one clip's transcript (timestamps in clip seconds)."""
    return (
        f"Clip length: {clip_duration:.1f} seconds.\n"
        f"Return at most {max_cues} cutaways. None may start before second "
        f"{earliest_start:.1f} or run past second {max(0.0, clip_duration - 1):.1f}.\n\n"
        "Transcript, one line per phrase, prefixed with the clip second it starts at:\n"
        f"{timed_lines}"
    )


async def plan_broll_cues(
    timed_lines: str, clip_duration: float, max_cues: int, earliest_start: float
) -> List[BRollCue]:
    """Pick stock-footage moments for one clip. Raises when the model is unavailable."""
    result = await _run_transcript_analysis(
        get_broll_agent(),
        build_broll_plan_prompt(timed_lines, clip_duration, max_cues, earliest_start),
        timeout_seconds=BROLL_PLAN_TIMEOUT_SECONDS,
    )
    return list(result.output.cues)


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
    candidates: List[TranscriptSegment],
    performance_context: str | None = None,
    *,
    instructions: str | None = None,
) -> str:
    """Describe every candidate in full so the reranker can compare them.

    ``instructions`` (the creator's steering text) follows the candidates.
    """
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
    prompt = (
        f"Rank these {len(candidates)} clip candidates from the same video, best first.\n\n"
        + "\n\n".join(blocks)
        + _performance_history_section(performance_context)
    )
    if instructions:
        prompt += "\n\n" + instructions_block(instructions)
    return prompt


def build_transcript_analysis_prompt(
    transcript: str,
    clip_signals: str | None = None,
    window: tuple[int, int] | None = None,
    performance_context: str | None = None,
    clip_length: str = "auto",
    *,
    instructions: str | None = None,
    short_speech_bounds: tuple[int, int] | None = None,
) -> str:
    """Build the grounded task prompt for transcript analysis.

    ``window`` marks the prompt as one (start, end) slice of a longer source.
    ``performance_context`` is the creator's measured audience history, if any.
    ``short_speech_bounds`` describes the spoken extent of an already-short source.
    ``instructions`` (the creator's steering text) goes just before the transcript.
    """
    bounds = clip_duration(clip_length)
    minimum = bounds.minimum
    segment_target = ANALYSIS_WINDOW_SEGMENT_TARGET if window else "2-5"
    window_note = ""
    if window:
        window_note = (
            f"\nThis transcript is one window ({_format_minutes_label(window[0])} - "
            f"{_format_minutes_label(window[1])}) of a longer video. Other windows are "
            "analysed separately, so pick the best clips that exist inside this window only.\n"
        )
    performance_section = _performance_history_section(performance_context)
    signal_section = ""
    if clip_signals:
        signal_section = (
            "\n\nAdditional deterministic signals from transcript/audio analysis:\n"
            f"{clip_signals}\n\n"
            "Use these as hints only. They should influence ranking, but every final segment "
            "must still be a coherent contiguous transcript range."
        )
    instructions_section = f"{instructions_block(instructions)}\n\n" if instructions else ""
    boundary_guidance = (
        "start_time must be the first word of a self-contained sentence: never a mid-thought fragment, "
        "a filler, a bare agreement, or a reference to something this clip does not contain. "
        "end_time must finish that thought's last sentence and must not include the next thought's opening words."
    )
    selection_guidance = f"""- Most selected clips should be {bounds.ideal_minimum}-{bounds.ideal_maximum} seconds.
- Every clip must contain a full setup and payoff within {bounds.minimum}-{bounds.maximum} seconds.
- If a strong moment is shorter than {bounds.ideal_minimum} seconds, first try expanding to nearby contiguous transcript lines that add useful context.
- Skip weak standalone picks: intros, sponsor reads, CTAs, contextless quotes, repeated points, vague setup, and answer fragments that require prior context.
- Before returning a segment, ask whether a viewer would understand and care without seeing the rest of the source video."""
    if short_speech_bounds is not None and window is None:
        start, end = short_speech_bounds
        minimum = min(minimum, end - start)
        segment_target = "1-2"
        boundary_guidance = (
            "Prefer natural openings and endings. If the source begins or ends during an exchange, "
            "use the available boundaries when the exchange is still understandable."
        )
        selection_guidance = (
            "- The source is already a short clip. Pick its best spoken moment, or use the full "
            f"spoken range ({_format_minutes_label(start)} to {_format_minutes_label(end)}) "
            "when it works best together.\n"
            "- Use the context available in the clip. A quick reaction, exchange, or observation "
            "can work without a long-form setup and payoff.\n"
            "- Include a grounded hook_title and score what the clip actually offers. "
            "Return at least one usable selection when the speech is clear, even at a modest score."
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
5. {boundary_guidance}

Selection target:
- Choose {segment_target} segments total.
{selection_guidance}

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
- Top-level keys: "most_relevant_segments", "summary", "key_topics".
- Segment keys: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", "hook_title".
- Optional segment keys "hook_start_time"/"hook_end_time": null unless a later 2-5 second line stands alone and the real opening is already a complete setup. The clip is not replayed from that line by default.
- "hook_title" is a 3-9 word plain-text headline for the clip, in the transcript's language, grounded in the segment (no hashtags, emojis, or quotes).
- Virality keys: "hook_score", "engagement_score", "value_score", "shareability_score", "total_score", "hook_type", "virality_reasoning".
- Do not return segments shorter than {minimum} seconds or longer than {bounds.maximum} seconds.

{instructions_section}Transcript:
{transcript}"""


_TITLE_NUMBER = re.compile(r"^(\d+(?:,\d{3})*(?:\.\d+)?)([.,!?:;]*)$")
_UNIT_WORDS = frozenset({"percent", "per", "pounds", "pound", "dollars", "dollar", "euros", "euro", "quid", "bucks"})


def restore_number_signs(title: str, source_text: Optional[str]) -> str:
    """Give a bare number in the title back the sign the speaker used with it.

    Models sometimes write "90 of guests" for a transcript's "90% of guests".
    A number gets its "%" (or "$", "£", "€") back only when every time that
    number appears in ``source_text`` it carries that sign, so nothing is
    invented.
    """
    if not source_text or not any(ch.isdigit() for ch in title):
        return title
    words = title.split()
    for index, word in enumerate(words):
        match = _TITLE_NUMBER.match(word)
        if not match:
            continue
        following = words[index + 1].lower().strip(".,!?") if index + 1 < len(words) else ""
        if following in _UNIT_WORDS:
            continue
        number, tail = match.group(1), match.group(2)
        found = re.findall(rf"(?<![\d.,])([$£€]?)\s?{re.escape(number)}(?![\d]|[.,]\d)(\s?%)?", source_text)
        if not found:
            continue
        if all(percent for _currency, percent in found):
            words[index] = f"{number}%{tail}"
        else:
            currencies = {currency for currency, _percent in found}
            if len(currencies) == 1 and "" not in currencies:
                words[index] = f"{currencies.pop()}{number}{tail}"
    return " ".join(words)


_WORD_EDGE = re.compile(r"^[^\w]+|[^\w]+$")
TITLE_CASE_WORD_LETTERS = 4  # shorter words (of, the, im) are lower case in Title Case too


def _bare_word(word: str) -> str:
    """A word as the transcript is searched for it: lower case, without a possessive ending."""
    return re.sub(r"['’]s$", "", word.lower())


def sentence_case_title(title: str, source_text: Optional[str] = None) -> str:
    """Bring a Title Case headline to sentence case so one task's clips read alike.

    Models mix "That Tastes Like Dirt, Bro" with "It just tastes like texture".
    Only a title in Title Case is touched (every later word of some length
    capitalised), and only when the transcript accounts for each of those
    words. A capitalised word is then lowered when the transcript has it in
    lower case; one it spells with a capital in mid-sentence, or has only at
    the start of sentences, may be a name and keeps its capital, as do words
    with capitals inside (CEOs, iPhone) and "I". A title with a capitalised
    word the transcript does not have at all is left as it is. The first word
    of each sentence stays capitalised.
    """
    words = title.split()
    if len(words) < 3:
        return title
    source = source_text or ""
    proper = set(re.findall(r"(?<=[\w,;:'\"”’)]\s)([A-ZÀ-Þ][\w'’-]*)", source))
    common = set(re.findall(r"\b([a-zß-ÿ][\w'’-]*)", source))
    spoken = {_bare_word(found) for found in re.findall(r"[^\W\d_][\w'’-]*", source)}

    def simple_capital(core: str) -> bool:
        return len(core) > 1 and core[0].isupper() and core[1:] == core[1:].lower() and core[1:] != core[1:].upper()

    later = []
    for index, word in enumerate(words):
        core = _WORD_EDGE.sub("", word)
        starts = index == 0 or words[index - 1][-1:] in ".!?:"
        if core[:1].isalpha() and not starts:
            later.append(core)
    capped = [core for core in later if simple_capital(core) and not core.startswith(("I'", "I’"))]
    # Title Case capitalises every word of some length. A German sentence does that to its nouns only, and its
    # verbs give it away: such a title is left as it is.
    longer = [core for core in later if len(core) >= TITLE_CASE_WORD_LETTERS]
    if len(capped) < 2 or any(not simple_capital(core) for core in longer if core == core.lower() or core[0].islower()):
        return title
    # All of it or none: a capitalised word the transcript does not have at all could be a name or an ordinary
    # word, and a title lowered by half reads worse than one left in Title Case.
    if any(core not in proper and core[0].lower() + core[1:] not in common and _bare_word(core) not in spoken
           for core in capped):
        return title
    out = []
    for index, word in enumerate(words):
        core = _WORD_EDGE.sub("", word)
        starts = index == 0 or words[index - 1][-1:] in ".!?:"
        if starts or not simple_capital(core) or core.startswith(("I'", "I’")) or core in proper:
            out.append(word)
            continue
        lowered = core[0].lower() + core[1:]
        # Only what the transcript shows in lower case: a word it does not have at all may be a name.
        if lowered in common:
            word = word.replace(core, lowered, 1)
        out.append(word)
    return " ".join(out)


def sanitize_hook_title(raw: Optional[str], source_text: Optional[str] = None) -> Optional[str]:
    """Normalize an AI-provided hook title for on-screen rendering.

    Strips wrapping quotes/markdown, collapses whitespace, drops hashtags, and
    trims to a word-boundary length cap. Returns None when nothing usable is
    left so callers can simply skip the overlay. With ``source_text`` (the
    clip's transcript) it also restores a number's dropped sign and brings a
    Title Case headline to sentence case.
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
    if source_text is not None:
        title = sentence_case_title(restore_number_signs(title, source_text), source_text)

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
    transcript_spans: list[dict[str, Any]], start_seconds: int, end_seconds: int,
    clip_length: str = "auto",
) -> tuple[int, int] | None:
    """Repair model-selected bounds to the nearest acceptable contiguous range."""
    bounds = clip_duration(clip_length)
    if not transcript_spans:
        return None

    starts = sorted({span["start"] for span in transcript_spans})
    ends = sorted({span["end"] for span in transcript_spans})
    current_duration = end_seconds - start_seconds

    if current_duration > bounds.maximum:
        target_end = start_seconds + bounds.ideal_maximum
        candidate_ends = [
            candidate
            for candidate in ends
            if start_seconds + bounds.minimum
            <= candidate
            <= min(target_end, end_seconds)
        ]
        if candidate_ends:
            return start_seconds, max(candidate_ends)
        if start_seconds + bounds.minimum <= target_end:
            return start_seconds, target_end
        return None

    if current_duration < bounds.minimum:
        candidate_ranges: list[tuple[int, int, int]] = []
        for candidate_start in starts:
            if candidate_start > start_seconds:
                continue
            for candidate_end in ends:
                if candidate_end < end_seconds:
                    continue
                duration = candidate_end - candidate_start
                if bounds.minimum <= duration <= bounds.maximum:
                    extra_context = (start_seconds - candidate_start) + (
                        candidate_end - end_seconds
                    )
                    ideal_penalty = 0
                    if duration < bounds.ideal_minimum:
                        ideal_penalty = bounds.ideal_minimum - duration
                    elif duration > bounds.ideal_maximum:
                        ideal_penalty = duration - bounds.ideal_maximum
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
    clip_length: str = "auto",
) -> tuple[int, int] | None:
    """Adjust near-miss model ranges to usable transcript-aligned bounds."""
    repaired_bounds = _choose_repaired_bounds(
        transcript_spans,
        start_seconds,
        end_seconds,
        clip_length,
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


def _covered_ratio(bounds: tuple[int, int], others: List[tuple[int, int]]) -> float:
    """Share of ``bounds`` that the other ranges, taken together, already show."""
    start, end = bounds
    if end <= start:
        return 0.0
    covered, cursor = 0, start
    for other_start, other_end in sorted(others):
        low, high = max(other_start, cursor), min(other_end, end)
        if high > low:
            covered += high - low
            cursor = high
    return covered / (end - start)


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
        ) or _covered_ratio(bounds, [kept_bounds for _, kept_bounds in kept]) > CANDIDATE_DEDUPE_COVERED_RATIO:
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
    candidates: List[TranscriptSegment],
    performance_context: str | None = None,
    *,
    instructions: str | None = None,
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
            build_candidate_rerank_prompt(
                candidates, performance_context, **({"instructions": instructions} if instructions else {})
            ),
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
    clip_signals: Optional[str],
    performance_context: Optional[str] = None,
    clip_length: str = "auto",
    *,
    instructions: Optional[str] = None,
) -> List[TranscriptAnalysis]:
    """Analyse windows with bounded concurrency, tolerating some failures.

    Raises the first window error only when no window produced an analysis.
    """
    semaphore = asyncio.Semaphore(ANALYSIS_WINDOW_CONCURRENCY)

    async def run_window(window: Dict[str, Any]) -> TranscriptAnalysis:
        async with semaphore:
            prompt = build_transcript_analysis_prompt(
                transcript=window["transcript"],
                clip_signals=_filter_clip_signals_for_window(
                    clip_signals, window["start"], window["end"]
                ),
                window=(window["start"], window["end"]),
                performance_context=performance_context,
                clip_length=clip_length,
                **({"instructions": instructions} if instructions else {}),
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
    clip_length: str = "auto",
    *,
    short_speech_bounds: tuple[int, int] | None = None,
) -> List[TranscriptSegment]:
    """Ground model segments onto real transcript lines and drop unusable ones."""
    bounds = clip_duration(clip_length)
    minimum = bounds.minimum
    if short_speech_bounds is not None:
        first_speech, last_speech = short_speech_bounds
        minimum = min(minimum, last_speech - first_speech)
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
            if short_speech_bounds is not None:
                # Whole-source picks can include silence outside the transcript labels.
                start_seconds = max(start_seconds, first_speech)
                end_seconds = min(end_seconds, last_speech)
            if end_seconds <= start_seconds:
                continue
            start_seconds, end_seconds = _snap_segment_bounds(
                segment, transcript_lines, start_seconds, end_seconds
            )
            duration = end_seconds - start_seconds

            if duration < minimum or duration > bounds.maximum:
                repaired_bounds = _repair_segment_bounds(
                    segment,
                    transcript_spans,
                    start_seconds,
                    end_seconds,
                    clip_length,
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

            if duration < minimum:
                logger.warning(
                    f"Skipping segment too short: {duration}s (min {minimum}s required)"
                )
                continue

            if duration > bounds.maximum:
                logger.warning(
                    f"Skipping segment too long: {duration}s (max {bounds.maximum}s allowed)"
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

            segment.hook_title = sanitize_hook_title(segment.hook_title, source_text=segment.text or "")
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
    clip_signals: str | None = None,
    performance_context: str | None = None,
    clip_length: str = "auto",
    include_reserve: bool = False,
    *,
    instructions: str | None = None,
    source_duration: float | None = None,
) -> TranscriptAnalysis:
    """Get the most relevant parts of a transcript with virality scoring.

    Short transcripts are normally analysed in one call. Long ones are split into
    overlapping windows analysed concurrently, then deduplicated and reranked
    in one comparative call so scores are comparable across windows.
    ``instructions`` (the creator's steering text) reaches every user prompt
    when set; system prompts and agents never change.
    """
    steering = {"instructions": instructions} if instructions else {}
    logger.info(
        f"Starting AI analysis of transcript ({len(transcript)} chars)"
    )

    try:
        agent = get_transcript_agent(clip_length)
        transcript_lines = _parse_transcript_lines(transcript)
        transcript_spans = _parse_transcript_spans(transcript)
        windows = split_transcript_into_windows(transcript)
        short_speech_bounds = short_source_speech_bounds(transcript, source_duration, clip_length)

        if len(windows) == 1:
            prompt = build_transcript_analysis_prompt(
                transcript=transcript,
                clip_signals=clip_signals,
                performance_context=performance_context,
                clip_length=clip_length,
                short_speech_bounds=short_speech_bounds,
                **steering,
            )
            result = await _run_transcript_analysis(agent, prompt)
            analyses = [result.output]
        else:
            logger.info("Analysing transcript in %d overlapping windows", len(windows))
            analyses = await _analyze_transcript_windows(
                agent, windows, clip_signals, performance_context, clip_length, **steering
            )

        candidates: List[TranscriptSegment] = []
        for analysis in analyses:
            logger.info(
                f"AI analysis found {len(analysis.most_relevant_segments)} segments"
            )
            candidates.extend(
                _validate_segments(
                    analysis.most_relevant_segments, transcript_lines, transcript_spans, clip_length,
                    short_speech_bounds=short_speech_bounds,
                )
            )

        if not candidates and short_speech_bounds is not None and len(windows) == 1:
            logger.info("Retrying short-source analysis after no usable selections")
            try:
                result = await _run_transcript_analysis(
                    agent,
                    prompt + "\n\nThe first pass yielded no usable selection. Evaluate the full spoken range "
                    "above as one candidate. Use its transcript boundaries, give it a grounded hook_title, "
                    "and score it on the actual speech, even if it earns a modest score.",
                )
            except Exception as exc:
                logger.warning("Short-source analysis retry failed (%s)", type(exc).__name__)
            else:
                candidates = _validate_segments(
                    result.output.most_relevant_segments, transcript_lines, transcript_spans, clip_length,
                    short_speech_bounds=short_speech_bounds,
                )
                if candidates:
                    analyses = [result.output]

        # Best-first by virality (primary) then relevance (secondary), without
        # near-duplicates; then compare across windows in one rerank call.
        candidates = dedupe_overlapping_segments(candidates)
        if len(windows) > 1:
            candidates = await rerank_candidates(candidates, performance_context, **steering)

        source_seconds = max(
            (line["end_seconds"] for line in transcript_lines), default=0
        )
        limit = max_final_clips_for_duration(source_seconds, get_config().max_clips)
        # Retain already-ranked alternatives until picture suitability checks
        # finish. No extra model call is needed when a slate is rejected.
        validated_segments = candidates[: min(24, limit * 2) if include_reserve else limit]

        final_analysis = TranscriptAnalysis(
            most_relevant_segments=validated_segments,
            summary=" ".join(
                analysis.summary.strip() for analysis in analyses if analysis.summary
            ),
            key_topics=_merge_key_topics(analyses),
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
