"""
AI-related functions for transcript analysis with enhanced precision and virality scoring.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
import asyncio
import logging
import re

from pydantic_ai import Agent
from pydantic import BaseModel, Field

from .config import Config

logger = logging.getLogger(__name__)
config = Config()


class ViralityAnalysis(BaseModel):
    """Detailed virality breakdown for a segment."""

    hook_score: int = Field(
        description="How strong is the opening hook (0-25)", ge=0, le=25
    )
    engagement_score: int = Field(
        description="How engaging/entertaining is the content (0-25)", ge=0, le=25
    )
    value_score: int = Field(
        description="Educational/informational value (0-25)", ge=0, le=25
    )
    shareability_score: int = Field(
        description="Likelihood of being shared (0-25)", ge=0, le=25
    )
    total_score: int = Field(
        description="Combined virality score (0-100)", ge=0, le=100
    )
    hook_type: Optional[
        Literal["question", "statement", "statistic", "story", "contrast", "none"]
    ] = Field(
        default="none",
        description="Type of hook: question, statement, statistic, story, contrast, or none",
    )
    virality_reasoning: str = Field(description="Explanation of the virality score")


class TranscriptSegment(BaseModel):
    """Represents a relevant segment of transcript with precise timing and virality analysis."""

    start_time: str = Field(description="Start timestamp in MM:SS format")
    end_time: str = Field(description="End timestamp in MM:SS format")
    text: str = Field(description="The transcript text for this segment")
    relevance_score: float = Field(
        description="Relevance score from 0.0 to 1.0", ge=0.0, le=1.0
    )
    reasoning: str = Field(description="Explanation for why this segment is relevant")
    virality: ViralityAnalysis = Field(description="Detailed virality score breakdown")


class BRollOpportunity(BaseModel):
    """Identifies an opportunity to insert B-roll footage."""

    timestamp: str = Field(description="When to insert B-roll (MM:SS format)")
    duration: float = Field(
        description="How long to show B-roll (2-5 seconds)", ge=2.0, le=5.0
    )
    search_term: str = Field(description="Keyword to search for B-roll footage")
    context: str = Field(description="What's being discussed at this point")


class TranscriptAnalysis(BaseModel):
    """Analysis result for transcript segments with virality and B-roll opportunities."""

    most_relevant_segments: List[TranscriptSegment]
    summary: str = Field(description="Brief summary of the video content")
    key_topics: List[str] = Field(description="List of main topics discussed")
    broll_opportunities: Optional[List[BRollOpportunity]] = Field(
        default=None, description="Opportunities to insert B-roll footage"
    )


# Enhanced system prompt with virality scoring and B-roll detection
simplified_system_prompt = """You are an expert at analyzing video transcripts to find the most engaging segments for short-form content creation with viral potential.

CORE OBJECTIVES:
1. Identify segments that would be compelling on social media platforms
2. Focus on complete thoughts, insights, or entertaining moments
3. Prioritize content with hooks, emotional moments, or valuable information
4. Each segment should be engaging and worth watching
5. Score each segment's viral potential with detailed breakdown

SEGMENT SELECTION CRITERIA:
1. STRONG HOOKS: Attention-grabbing opening lines
2. VALUABLE CONTENT: Tips, insights, interesting facts, stories
3. EMOTIONAL MOMENTS: Excitement, surprise, humor, inspiration
4. COMPLETE THOUGHTS: Self-contained ideas that make sense alone
5. ENTERTAINING: Content people would want to share

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
- Segments MUST be between 10-45 seconds for optimal engagement
- CRITICAL: start_time MUST be different from end_time (minimum 10 seconds apart)
- Focus on natural content boundaries rather than arbitrary time limits
- Include enough context for the segment to be understandable

TIMESTAMP REQUIREMENTS - EXTREMELY IMPORTANT:
- Use EXACT timestamps as they appear in the transcript
- Never modify timestamp format (keep MM:SS structure)
- start_time MUST be LESS THAN end_time (start_time < end_time)
- MINIMUM segment duration: 10 seconds (end_time - start_time >= 10 seconds)
- Look at transcript ranges like [02:25 - 02:35] and use different start/end times
- NEVER use the same timestamp for both start_time and end_time
- Example: start_time: "02:25", end_time: "02:35" (NOT "02:25" and "02:25")

Find 3-7 compelling segments that would work well as standalone clips. Quality over quantity - choose segments that would genuinely engage viewers, have proper time ranges, and score high on virality metrics."""

# Lazy-loaded agent to avoid import-time failures when API keys aren't set
_transcript_agent: Optional[Agent[None, TranscriptAnalysis]] = None


def _get_missing_llm_key_error(model_name: str) -> Optional[str]:
    """Return a clear configuration error when the selected LLM key is missing."""
    provider = model_name.split(":", 1)[0].strip().lower()

    if provider in {"google", "google-gla"} and not config.google_api_key:
        return (
            "Selected LLM provider is Google, but GOOGLE_API_KEY is not set. "
            "Set GOOGLE_API_KEY or set LLM to openai:* / anthropic:* / ollama:* with the matching API key."
        )

    if provider == "openai" and not config.openai_api_key:
        return (
            "Selected LLM provider is OpenAI, but OPENAI_API_KEY is not set. "
            "Set OPENAI_API_KEY or choose another provider with a matching API key."
        )

    if provider == "anthropic" and not config.anthropic_api_key:
        return (
            "Selected LLM provider is Anthropic, but ANTHROPIC_API_KEY is not set. "
            "Set ANTHROPIC_API_KEY or choose another provider with a matching API key."
        )

    if provider == "ollama":
        # Ollama can run locally without an API key. OLLAMA_BASE_URL/OLLAMA_API_KEY
        # are optional and passed through as environment variables.
        return None

    return None


def get_transcript_agent() -> Agent[None, TranscriptAnalysis]:
    """Get or create the transcript analysis agent (lazy initialization)."""
    global _transcript_agent
    if _transcript_agent is None:
        config_error = _get_missing_llm_key_error(config.llm)
        if config_error:
            raise RuntimeError(config_error)

        _transcript_agent = Agent[None, TranscriptAnalysis](
            model=config.llm,
            result_type=TranscriptAnalysis,
            system_prompt=simplified_system_prompt,
        )
    return _transcript_agent


async def get_most_relevant_parts_by_transcript(
    transcript: str, include_broll: bool = False
) -> TranscriptAnalysis:
    """Get the most relevant parts of a transcript with virality scoring and optional B-roll detection."""
    logger.info(
        f"Starting AI analysis of transcript ({len(transcript)} chars), include_broll={include_broll}"
    )

    try:
        agent = get_transcript_agent()

        broll_instruction = ""
        if include_broll:
            broll_instruction = "\n\nAlso identify B-roll opportunities for each segment where stock footage could enhance the visual appeal."

        result = await agent.run(
            f"""Analyze this video transcript and identify the most engaging segments for short-form content.

Find segments that would be compelling as standalone clips for social media.
For each segment, provide a detailed virality score breakdown.{broll_instruction}

Transcript:
{transcript}"""
        )

        analysis = result.data
        logger.info(
            f"AI analysis found {len(analysis.most_relevant_segments)} segments"
        )

        # Validation with virality data handling
        validated_segments = []
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
                start_parts = segment.start_time.split(":")
                end_parts = segment.end_time.split(":")

                start_seconds = int(start_parts[0]) * 60 + int(start_parts[1])
                end_seconds = int(end_parts[0]) * 60 + int(end_parts[1])

                duration = end_seconds - start_seconds

                if duration <= 0:
                    logger.warning(
                        f"Skipping segment with invalid duration: {segment.start_time} to {segment.end_time} = {duration}s"
                    )
                    continue

                if duration < 5:  # Minimum 5 seconds
                    logger.warning(
                        f"Skipping segment too short: {duration}s (min 5s required)"
                    )
                    continue

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
