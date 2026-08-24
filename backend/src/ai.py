"""
AI-related functions for transcript analysis with enhanced precision and virality scoring.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Literal, Tuple
import asyncio
import logging
import re

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.settings import ModelSettings
from pydantic import AliasChoices, BaseModel, Field, field_validator

from .config import Config, get_config
from .runtime_settings import apply_settings_to_process_env

logger = logging.getLogger(__name__)

IDEAL_CLIP_MIN_SECONDS = 25
IDEAL_CLIP_MAX_SECONDS = 50
MIN_ACCEPTED_CLIP_SECONDS = 15
MAX_ACCEPTED_CLIP_SECONDS = 60
TRANSCRIPT_ANALYSIS_CACHE_VERSION = "hook-titles-v5-grounded"
HOOK_TITLE_MAX_CHARS = 64
HOOK_TITLE_MAX_WORDS = 10
TRANSCRIPT_SPAN_RE = re.compile(
    r"^\[(?P<start>\d{1,4}:\d{2}(?::\d{2})?)\s*-\s*"
    r"(?P<end>\d{1,4}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*)$"
)
TRANSCRIPT_LINE_PATTERN = re.compile(
    r"^\[(\d{1,4}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,4}:\d{2}(?::\d{2})?)\]\s*(.*)$"
)
SPEAKER_PREFIX_PATTERN = re.compile(r"^Speaker [^:]+:\s*")


def _normalize_transcript_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w']+", " ", value.lower(), flags=re.UNICODE)).strip()


def _parse_transcript_lines(transcript: str) -> List[Dict[str, str]]:
    lines: List[Dict[str, str]] = []
    for raw_line in transcript.splitlines():
        match = TRANSCRIPT_LINE_PATTERN.match(raw_line.strip())
        if not match:
            continue
        line_text = SPEAKER_PREFIX_PATTERN.sub("", match.group(3).strip())
        lines.append(
            {
                "start_time": match.group(1),
                "end_time": match.group(2),
                "text": line_text,
            }
        )
    return lines


def _extract_transcript_text_for_segment(
    transcript_lines: List[Dict[str, str]],
    start_time: str,
    end_time: str,
) -> Optional[str]:
    target_start_sec = parse_timestamp_to_seconds(start_time)
    target_end_sec = parse_timestamp_to_seconds(end_time)

    for start_index, line in enumerate(transcript_lines):
        line_start_sec = parse_timestamp_to_seconds(line["start_time"])
        # Match either exact string or within 1.0s timestamp tolerance
        if line["start_time"] != start_time and abs(line_start_sec - target_start_sec) > 1.0:
            continue

        collected_parts = [line["text"]] if line["text"] else []
        line_end_sec = parse_timestamp_to_seconds(line["end_time"])
        if line["end_time"] == end_time or abs(line_end_sec - target_end_sec) <= 1.0:
            return " ".join(part for part in collected_parts if part).strip()

        for next_line in transcript_lines[start_index + 1 :]:
            if next_line["text"]:
                collected_parts.append(next_line["text"])
            n_end_sec = parse_timestamp_to_seconds(next_line["end_time"])
            if next_line["end_time"] == end_time or abs(n_end_sec - target_end_sec) <= 1.0:
                return " ".join(part for part in collected_parts if part).strip()

        return " ".join(part for part in collected_parts if part).strip() or None

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


class TransliteratedSegmentVerification(BaseModel):
    """Verified and refined transliteration for a single segment."""

    id: int = Field(description="Segment ID matching the input request")
    verified_transliteration: str = Field(
        description="The verified, natural, and accurately spelled phonetic Latin/English transliteration"
    )


class TransliterationBatchResponse(BaseModel):
    """Batch response of verified transliterations from the LLM."""

    segments: List[TransliteratedSegmentVerification] = Field(
        default_factory=list,
        description="List of verified and refined transliterated segments",
    )


TRANSLITERATION_VERIFICATION_SYSTEM_PROMPT = """You are an expert multilingual phonetic linguist and transliterator.
You are given pairs of:
1. Native Text: Spoken transcription in native script (Devanagari/Hindi, Japanese Kanji/Kana, Arabic, Russian/Cyrillic, Chinese, Spanish, etc.)
2. Raw Transliteration: Generated by a rule-based phonetic engine (anyascii).

Your goal:
Verify, correct, and refine the phonetic English/Latin transliteration so it reads naturally and accurately represents the native pronunciation.

CRITICAL RULES:
1. Natural Romanization: Replace rigid, robotic, or awkward transliterations with standard, colloquial Romanizations (e.g. Hindi: "nmste" -> "Namaste", "bhart" -> "Bharat", "duniya" -> "Duniya", "ap kaise ho" -> "Aap kaise ho", "shukriya" -> "Shukriya"; Japanese: "konnichiha" -> "Konnichiwa", "arigatou" -> "Arigato"; Arabic: "shukran", "marhaban").
2. Exact Word Count & Sequence: Ensure the verified transliteration maintains the EXACT SAME sequence and number of words as the input so subtitle timings and karaoke highlights remain synchronized.
3. Keep Numbers and Punctuation: Preserve punctuation marks, numbers, and symbols intact.
4. Output valid JSON matching the schema for all provided segment IDs."""


# Enhanced system prompt with virality scoring and B-roll detection
transcript_analysis_system_prompt = """You are an expert transcript analyst for short-form video editing.

Your job is extraction and ranking, not creative rewriting. You must stay fully grounded in the transcript and choose the best clip candidates that already exist in the source material.

OUTPUT CONTRACT:
- Return valid JSON only. Do not output Markdown, headings, bullets, prose, code fences, explanations, or commentary outside the JSON object.
- The top-level JSON object must include: "most_relevant_segments", "summary", and "key_topics".
- Only include "broll_opportunities" when B-roll was requested.
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
- Good examples: "The $40k mistake I keep seeing", "Why nobody tells you this about VC", "Do this before your next interview"

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

Find 2-5 compelling segments that would work well as standalone clips. Quality over quantity: choose fewer stronger segments over filling a quota. Every selected segment must be accurate, self-contained, have proper time ranges, and score high on virality metrics."""

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


def _parse_thinking_level(level_val: Any) -> Any:
    """Parse configured thinking level into Pydantic AI ThinkingLevel or integer budget."""
    if level_val is None:
        return "high"
    if isinstance(level_val, bool):
        return level_val
    level_s = str(level_val).strip().lower()
    if level_s in {"off", "false", "none", "0", "disabled"}:
        return False
    if level_s in {"minimal", "low", "medium", "high", "xhigh"}:
        return level_s
    try:
        return int(level_s)
    except ValueError:
        return "high"


def _resolve_single_model(raw_name: str | None, runtime_config: Config) -> Model | str | None:
    """Resolve a provider-prefixed model string into a Pydantic AI model identifier."""
    if not raw_name or str(raw_name).strip().lower() in {"none", "false", "disabled", "null", ""}:
        return None

    provider, provider_model_name = _split_llm_name(raw_name)
    if provider == "google-gla":
        return f"google:{provider_model_name}"
    if provider != "ollama":
        return raw_name

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


def _build_transcript_model(runtime_config: Config) -> Model | str:
    """
    Build transcript model with automatic fallback chaining.
    If the primary model is busy/unavailable (503/429/errors), FallbackModel
    automatically fails over to the backup model.
    """
    primary = _resolve_single_model(runtime_config.llm, runtime_config)
    if not primary:
        primary = "google:gemini-3-flash-preview"

    fallback = _resolve_single_model(runtime_config.fallback_llm, runtime_config)

    if fallback and fallback != primary:
        logger.info(
            "Configured LLM with Fallback Chaining: primary=%s -> backup=%s",
            primary,
            fallback,
        )
        return FallbackModel(primary, fallback)

    return primary


def get_transcript_agent() -> Agent[None, TranscriptAnalysis]:
    """Get or create the transcript analysis agent with thinking level and fallback chaining."""
    global _transcript_agent, _transcript_agent_signature
    runtime_config = get_config()
    provider, _ = _split_llm_name(runtime_config.llm)
    thinking_level = _parse_thinking_level(runtime_config.llm_thinking_level)
    signature = (
        runtime_config.llm,
        runtime_config.fallback_llm,
        str(thinking_level),
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

        model_settings = ModelSettings(thinking=thinking_level)

        _transcript_agent = Agent[None, TranscriptAnalysis](
            model=_build_transcript_model(runtime_config),
            output_type=TranscriptAnalysis,
            system_prompt=transcript_analysis_system_prompt,
            model_settings=model_settings,
            # Some local Ollama/OpenAI-compatible endpoints can return formatted
            # prose before settling on schema-valid JSON. Keep retries limited
            # while still allowing enough repair attempts for local models.
            retries=2 if provider == "ollama" else 2,
        )
        _transcript_agent_signature = signature
    return _transcript_agent


def parse_timestamp_to_seconds(ts: str) -> float:
    """Parse 'MM:SS' or 'HH:MM:SS' or float string to seconds."""
    if not ts:
        return 0.0
    ts = str(ts).strip()
    try:
        parts = [float(p) for p in ts.split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 1:
            return parts[0]
    except Exception:
        pass
    return 0.0


def compute_available_clip_capacity(
    total_duration_sec: float,
    excluded_ranges: Optional[List[Tuple[str, str]]] = None,
    min_clip_duration: float = 15.0,
) -> Dict[str, Any]:
    """
    Calculate untouched gap durations across the video, determining
    how much unallocated time remains and the maximum number of
    non-overlapping clips that can be created.
    """
    if total_duration_sec <= 0:
        return {
            "total_unallocated_seconds": 0.0,
            "usable_unallocated_seconds": 0.0,
            "max_possible_clips": 0,
            "available_intervals": [],
        }

    # 1. Parse and merge excluded intervals
    parsed_intervals = []
    for start, end in (excluded_ranges or []):
        s_sec = parse_timestamp_to_seconds(start)
        e_sec = parse_timestamp_to_seconds(end)
        if e_sec > s_sec:
            parsed_intervals.append([max(0.0, s_sec), min(total_duration_sec, e_sec)])

    parsed_intervals.sort(key=lambda x: x[0])
    merged_exclusions = []
    for interval in parsed_intervals:
        if not merged_exclusions:
            merged_exclusions.append(interval)
        else:
            prev = merged_exclusions[-1]
            if interval[0] <= prev[1]:
                prev[1] = max(prev[1], interval[1])
            else:
                merged_exclusions.append(interval)

    # 2. Extract untouched complement gaps
    available_gaps = []
    curr = 0.0
    for ex_start, ex_end in merged_exclusions:
        if ex_start > curr:
            available_gaps.append((curr, ex_start))
        curr = max(curr, ex_end)
    if curr < total_duration_sec:
        available_gaps.append((curr, total_duration_sec))

    # 3. Calculate metrics for gaps that can fit at least min_clip_duration
    valid_gaps = []
    total_unallocated_sec = 0.0
    max_possible_clips = 0
    for gap_start, gap_end in available_gaps:
        gap_dur = gap_end - gap_start
        total_unallocated_sec += gap_dur
        if gap_dur >= min_clip_duration:
            valid_gaps.append((gap_start, gap_end))
            max_possible_clips += int(gap_dur // min_clip_duration)

    return {
        "total_unallocated_seconds": round(total_unallocated_sec, 2),
        "usable_unallocated_seconds": round(sum(g[1] - g[0] for g in valid_gaps), 2),
        "max_possible_clips": max_possible_clips,
        "available_intervals": valid_gaps,
    }


def _filter_overlapping_segments(
    segments: List[Any],
    excluded_ranges: Optional[List[Tuple[str, str]]],
    min_overlap_sec: float = 2.0,
    min_clip_duration: float = 10.0,
) -> List[Any]:
    """Filter out segments that overlap with previously generated clip time ranges or are too short."""
    valid_segments = []
    parsed_exclusions = [
        (parse_timestamp_to_seconds(start), parse_timestamp_to_seconds(end))
        for start, end in (excluded_ranges or [])
    ]

    for segment in segments:
        start_time = getattr(segment, "start_time", None) if not isinstance(segment, dict) else segment.get("start_time")
        end_time = getattr(segment, "end_time", None) if not isinstance(segment, dict) else segment.get("end_time")
        if not start_time or not end_time:
            continue

        seg_start = parse_timestamp_to_seconds(start_time)
        seg_end = parse_timestamp_to_seconds(end_time)
        if seg_end - seg_start < min_clip_duration:
            logger.info("Filtered segment [%s - %s] shorter than min duration %.1fs", start_time, end_time, min_clip_duration)
            continue

        overlaps = False
        for ex_start, ex_end in parsed_exclusions:
            overlap = min(seg_end, ex_end) - max(seg_start, ex_start)
            if overlap > min_overlap_sec:
                overlaps = True
                logger.info(
                    "Filtered overlapping segment [%s - %s] (overlaps with [%.1fs - %.1fs] by %.1fs)",
                    start_time,
                    end_time,
                    ex_start,
                    ex_end,
                    overlap,
                )
                break

        if not overlaps:
            valid_segments.append(segment)

    return valid_segments


def build_transcript_analysis_prompt(
    transcript: str,
    include_broll: bool = False,
    clip_signals: str | None = None,
    excluded_ranges: Optional[List[Tuple[str, str]]] = None,
    target_clip_count: Optional[int] = None,
    min_clip_duration: Optional[int] = None,
) -> str:
    """Build the grounded task prompt for transcript analysis."""
    broll_instruction = ""
    if include_broll:
        broll_instruction = (
            "\n5. Also identify B-roll opportunities for each chosen segment where stock footage could enhance the visual appeal."
        )
    signal_section = ""
    if clip_signals:
        signal_section = (
            "\n\nAdditional deterministic signals from transcript/audio analysis:\n"
            f"{clip_signals}\n\n"
            "Use these as hints only. They should influence ranking, but every final segment "
            "must still be a coherent contiguous transcript range."
        )

    exclusion_section = ""
    if excluded_ranges:
        ranges_formatted = "\n".join(f"- [{start} - {end}]" for start, end in excluded_ranges)
        exclusion_section = (
            "\n\nCRITICAL EXCLUSIONS — PREVIOUSLY GENERATED CLIPS:\n"
            "The following timestamp ranges have ALREADY been clipped into videos. "
            "You MUST NOT select any segment that overlaps with these timestamp spans:\n"
            f"{ranges_formatted}\n"
            "Pick entirely new moments from other parts of the video transcript.\n"
        )

    count_target = (
        f"Choose exactly {target_clip_count} segments total."
        if target_clip_count
        else "Choose 2-5 segments total."
    )

    eff_min_dur = int(min_clip_duration or MIN_ACCEPTED_CLIP_SECONDS)
    eff_max_dur = max(MAX_ACCEPTED_CLIP_SECONDS, eff_min_dur + 35)

    min_dur_target = (
        f"- Each chosen clip MUST be between {eff_min_dur} and {eff_max_dur} seconds in duration."
        if min_clip_duration
        else f"- Most selected clips should be {IDEAL_CLIP_MIN_SECONDS}-{IDEAL_CLIP_MAX_SECONDS} seconds."
    )

    return f"""Analyze this video transcript and identify the most engaging segments for short-form content.

The transcript is formatted as one line per timestamped span, for example:
[00:12 - 00:21] Spoken text here
[00:21 - 00:35] More spoken text here

Follow this workflow:
1. Read the transcript as a sequence of timestamped spans.
2. Select only contiguous ranges that already exist in the transcript.
3. Prefer moments with a strong hook, clear payoff, emotional charge, or concrete value.
4. For each chosen segment, use the earliest timestamp in the selected range as start_time and the latest timestamp in the selected range as end_time.{broll_instruction}

Selection target:
- {count_target}
{min_dur_target}
- Only choose a shorter clip when it already contains a full setup and payoff.
- If a strong moment is shorter than {eff_min_dur} seconds, first try expanding to nearby contiguous transcript lines that add useful context.
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
{signal_section}{exclusion_section}

JSON-only output requirements:
- Return one valid JSON object and nothing else.
- No Markdown, headings, bullets, code fences, or explanatory text outside JSON.
- Top-level keys: "most_relevant_segments", "summary", "key_topics"{', "broll_opportunities"' if include_broll else ''}.
- Segment keys: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", "hook_title".
- "hook_title" is a 3-9 word plain-text headline for the clip, grounded in the segment (no hashtags, emojis, or quotes).
- Virality keys: "hook_score", "engagement_score", "value_score", "shareability_score", "total_score", "hook_type", "virality_reasoning".
- Do not return segments shorter than {eff_min_dur} seconds or longer than {eff_max_dur} seconds.

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
    transcript_spans: list[dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
    min_duration: int = MIN_ACCEPTED_CLIP_SECONDS,
    max_duration: int = MAX_ACCEPTED_CLIP_SECONDS,
) -> tuple[int, int] | None:
    """Repair model-selected bounds to the nearest acceptable contiguous range."""
    if not transcript_spans:
        return None

    starts = sorted({span["start"] for span in transcript_spans})
    ends = sorted({span["end"] for span in transcript_spans})
    current_duration = end_seconds - start_seconds

    if current_duration > max_duration:
        target_end = start_seconds + max_duration
        candidate_ends = [
            candidate
            for candidate in ends
            if start_seconds + min_duration
            <= candidate
            <= min(target_end, end_seconds)
        ]
        if candidate_ends:
            return start_seconds, max(candidate_ends)
        if start_seconds + min_duration <= target_end:
            return start_seconds, target_end
        return None

    if current_duration < min_duration:
        candidate_ranges: list[tuple[int, int, int]] = []
        for candidate_start in starts:
            if candidate_start > start_seconds:
                continue
            for candidate_end in ends:
                if candidate_end < end_seconds:
                    continue
                duration = candidate_end - candidate_start
                if min_duration <= duration <= max_duration:
                    extra_context = (start_seconds - candidate_start) + (
                        candidate_end - end_seconds
                    )
                    candidate_ranges.append(
                        (extra_context, candidate_start, candidate_end)
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
    min_duration: int = MIN_ACCEPTED_CLIP_SECONDS,
    max_duration: int = MAX_ACCEPTED_CLIP_SECONDS,
) -> tuple[int, int] | None:
    """Adjust near-miss model ranges to usable transcript-aligned bounds."""
    repaired_bounds = _choose_repaired_bounds(
        transcript_spans,
        start_seconds,
        end_seconds,
        min_duration=min_duration,
        max_duration=max_duration,
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
    transcript: str,
    include_broll: bool = False,
    clip_signals: str | None = None,
    excluded_ranges: Optional[List[Tuple[str, str]]] = None,
    target_clip_count: Optional[int] = None,
    min_clip_duration: Optional[int] = None,
) -> TranscriptAnalysis:
    """Get the most relevant parts of a transcript with virality scoring, optional B-roll detection, and exclusion filters."""
    eff_min_dur = int(min_clip_duration or MIN_ACCEPTED_CLIP_SECONDS)
    eff_max_dur = max(MAX_ACCEPTED_CLIP_SECONDS, eff_min_dur + 35)

    logger.info(
        f"Starting AI analysis of transcript ({len(transcript)} chars), include_broll={include_broll}, excluded_ranges={len(excluded_ranges or [])}, min_clip_duration={min_clip_duration} (eff_window: {eff_min_dur}s-{eff_max_dur}s)"
    )

    try:
        agent = get_transcript_agent()
        transcript_lines = _parse_transcript_lines(transcript)

        result = await agent.run(
            build_transcript_analysis_prompt(
                transcript=transcript,
                include_broll=include_broll,
                clip_signals=clip_signals,
                excluded_ranges=excluded_ranges,
                target_clip_count=target_clip_count,
                min_clip_duration=min_clip_duration,
            )
        )

        analysis = result.output
        logger.info(
            f"AI analysis found {len(analysis.most_relevant_segments)} segments"
        )

        # Validation with virality data handling
        validated_segments = []
        transcript_spans = _parse_transcript_spans(transcript)
        for segment in analysis.most_relevant_segments:
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
                start_seconds = _parse_transcript_timestamp_seconds(
                    segment.start_time
                )
                end_seconds = _parse_transcript_timestamp_seconds(segment.end_time)

                duration = end_seconds - start_seconds

                if duration < eff_min_dur or duration > eff_max_dur:
                    repaired_bounds = _repair_segment_bounds(
                        segment,
                        transcript_spans,
                        start_seconds,
                        end_seconds,
                        min_duration=eff_min_dur,
                        max_duration=eff_max_dur,
                    )
                    if repaired_bounds:
                        start_seconds, end_seconds = repaired_bounds
                        duration = end_seconds - start_seconds

                if duration <= 0:
                    logger.warning(
                        f"Skipping segment with non-positive duration: {segment.start_time}-{segment.end_time}"
                    )
                    continue

                grounded_text = _extract_transcript_text_for_segment(
                    transcript_lines,
                    segment.start_time,
                    segment.end_time,
                )
                if not grounded_text:
                    grounded_text = _extract_transcript_text(
                        transcript_spans,
                        start_seconds,
                        end_seconds,
                    )
                if not grounded_text:
                    logger.warning(
                        f"Skipping segment with no grounded text: {segment.start_time}-{segment.end_time}"
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

        # Deterministically filter out any segments overlapping with excluded ranges or too short
        validated_segments = _filter_overlapping_segments(
            validated_segments,
            excluded_ranges,
            min_clip_duration=float(min_clip_duration or MIN_ACCEPTED_CLIP_SECONDS),
        )

        # Sort by virality score (primary) then relevance (secondary)
        validated_segments.sort(
            key=lambda x: (
                x.virality.total_score if x.virality else 0,
                x.relevance_score,
            ),
            reverse=True,
        )

        if target_clip_count and len(validated_segments) > target_clip_count:
            validated_segments = validated_segments[:target_clip_count]

        final_analysis = TranscriptAnalysis(
            most_relevant_segments=validated_segments,
            summary=analysis.summary,
            key_topics=analysis.key_topics,
            broll_opportunities=analysis.broll_opportunities if include_broll else None,
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


_transliteration_agent: Optional[Agent[None, TransliterationBatchResponse]] = None
_transliteration_agent_signature: Optional[tuple[str | None, ...]] = None


def get_transliteration_agent() -> Agent[None, TransliterationBatchResponse]:
    """Get or create the agent for verifying and refining phonetic transliterations."""
    global _transliteration_agent, _transliteration_agent_signature
    runtime_config = get_config()
    signature = (
        runtime_config.llm,
        runtime_config.fallback_llm,
        runtime_config.openai_api_key,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _transliteration_agent is None or _transliteration_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
        if config_error:
            raise RuntimeError(config_error)

        _transliteration_agent = Agent[None, TransliterationBatchResponse](
            model=_build_transcript_model(runtime_config),
            output_type=TransliterationBatchResponse,
            system_prompt=TRANSLITERATION_VERIFICATION_SYSTEM_PROMPT,
            retries=2,
        )
        _transliteration_agent_signature = signature
    return _transliteration_agent


async def verify_transliteration_with_llm(
    native_text: str,
    raw_transliterated_text: str,
    language_code: Optional[str] = None,
) -> str:
    """Verify and refine a single transliteration using LLM, with fallback to raw transliteration."""
    if not native_text or not raw_transliterated_text:
        return raw_transliterated_text or native_text or ""

    if native_text.isascii():
        return native_text

    try:
        agent = get_transliteration_agent()
        lang_hint = f" Language: {language_code}." if language_code else ""
        prompt = (
            f"Please verify and refine this transliteration.{lang_hint}\n"
            f"Segment ID: 0\n"
            f"Native Text: {native_text}\n"
            f"Raw Transliteration: {raw_transliterated_text}"
        )
        result = await asyncio.wait_for(agent.run(prompt), timeout=10.0)
        if result and result.output and result.output.segments:
            verified = result.output.segments[0].verified_transliteration.strip()
            if verified:
                return verified
    except Exception as e:
        logger.debug(
            f"LLM transliteration verification skipped/failed (falling back to raw): {e}"
        )

    return raw_transliterated_text


async def batch_verify_transliterations_with_llm(
    items: List[Dict[str, Any]],
    language_code: Optional[str] = None,
) -> List[str]:
    """Batch verify a list of items: [{'id': i, 'native': '...', 'raw': '...'}]

    Returns a list of verified strings in the same order as items.
    """
    if not items:
        return []

    fallback_results = [
        str(item.get("raw") or item.get("native") or "") for item in items
    ]

    if all(str(item.get("native", "")).isascii() for item in items):
        return fallback_results

    try:
        agent = get_transliteration_agent()
        lang_hint = f"Language: {language_code}\n" if language_code else ""
        lines = [f"{lang_hint}Verify and refine each of the following transliterated segments:"]
        for item in items:
            lines.append(
                f"Segment ID: {item.get('id', 0)}\n"
                f"Native Text: {item.get('native', '')}\n"
                f"Raw Transliteration: {item.get('raw', '')}\n"
            )
        prompt = "\n".join(lines)

        result = await asyncio.wait_for(agent.run(prompt), timeout=15.0)
        if result and result.output and result.output.segments:
            seg_map = {
                s.id: s.verified_transliteration.strip()
                for s in result.output.segments
                if s.verified_transliteration
            }
            verified_list = []
            for i, item in enumerate(items):
                item_id = item.get("id", i)
                verified_list.append(seg_map.get(item_id, fallback_results[i]))
            return verified_list
    except Exception as e:
        logger.debug(f"Batch LLM transliteration verification skipped/failed: {e}")

    return fallback_results


def sync_verify_transliteration(
    native_text: str,
    raw_transliterated_text: str,
    language_code: Optional[str] = None,
) -> str:
    """Synchronous helper for transliteration verification."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    verify_transliteration_with_llm(
                        native_text, raw_transliterated_text, language_code
                    ),
                )
                return future.result(timeout=10.0)
        else:
            return asyncio.run(
                verify_transliteration_with_llm(
                    native_text, raw_transliterated_text, language_code
                )
            )
    except Exception:
        return raw_transliterated_text

