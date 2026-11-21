"""Manual clip selection: the user's own AI chat picks the clips (``LLM=manual``).

With ``LLM=manual`` no model runs. After transcription the task pauses with a
prompt bundle (instructions, timestamped transcript, expected JSON). The user
pastes it into any chat assistant and submits the JSON answer, which is
validated and grounded on the transcript exactly like a model's answer, then
the task resumes and renders the clips.
"""

import json
import re
from typing import Any, List, Optional

from pydantic import ValidationError

from . import ai
from .ai import TranscriptAnalysis

MANUAL_LLM = "manual"
AWAITING_ANALYSIS_STATUS = "awaiting_analysis"
AWAITING_ANALYSIS_MESSAGE = "Waiting for your AI analysis. Download the prompt, run it in your AI chat, then upload the answer."
# Bounds the pasted answer; a real answer is a few kilobytes.
MAX_RESPONSE_CHARS = 200_000
MAX_ERRORS_SHOWN = 5

_EXAMPLE = {
    "most_relevant_segments": [
        {
            "start_time": "02:15",
            "end_time": "02:48",
            "text": "Verbatim transcript text of this exact span.",
            "relevance_score": 0.9,
            "reasoning": "Why this span works as a short clip.",
            "virality": {
                "hook_score": 20,
                "engagement_score": 18,
                "value_score": 17,
                "shareability_score": 16,
                "total_score": 71,
                "hook_type": "question",
                "virality_reasoning": "One sentence on the score.",
            },
            "hook_title": "A short punchy title",
        }
    ],
    "summary": "Two sentences on what the video is about.",
    "key_topics": ["topic one", "topic two"],
}


def is_manual_llm(model_name: Optional[str]) -> bool:
    return (model_name or "").strip().lower() == MANUAL_LLM


class ManualAnalysisPending(Exception):
    """The task needs the user's analysis before it can continue."""


class ManualAnalysisError(ValueError):
    """The submitted analysis cannot be used; the message tells the user why."""


def build_prompt_bundle(
    transcript: str,
    *,
    clip_signals: Optional[str] = None,
    performance_context: Optional[str] = None,
    clip_length: str = "auto",
    instructions: Optional[str] = None,
) -> str:
    """The text the user pastes into an AI chat."""
    task = ai.build_transcript_analysis_prompt(
        transcript=transcript,
        clip_signals=clip_signals,
        performance_context=performance_context,
        clip_length=clip_length,
        **({"instructions": instructions} if instructions else {}),
    )
    return (
        "# SupoClip clip selection\n\n"
        "You are the clip-selection model for a video clipping tool. Follow the instructions "
        "below and reply with ONE JSON object only: no Markdown, no code fences, no commentary. "
        "Copy timestamps exactly as they appear in the transcript.\n\n"
        "## Instructions\n\n"
        f"{ai.build_transcript_system_prompt(clip_length)}\n\n"
        "## Task\n\n"
        f"{task}\n\n"
        "## Required JSON shape (example values)\n\n"
        f"{json.dumps(_EXAMPLE, indent=2)}\n"
    )


def _extract_json(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        raise ManualAnalysisError("The response is empty.")
    if len(text) > MAX_RESPONSE_CHARS:
        raise ManualAnalysisError("The response is too long to be a clip analysis.")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end <= start:
        raise ManualAnalysisError(
            "The JSON looks cut off (it never closes). Ask the chat to resend the full answer."
        )
    if start == -1:
        raise ManualAnalysisError("No JSON object found. Ask the chat to reply with the JSON only.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise ManualAnalysisError(
            f"The response is not valid JSON ({error.msg} at line {error.lineno}). "
            "Ask the chat to resend it as one JSON object."
        ) from None


def parse_analysis(payload: Any) -> TranscriptAnalysis:
    """Parse a pasted chat answer (text) or an already decoded JSON object."""
    data = _extract_json(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        raise ManualAnalysisError("The analysis must be a JSON object.")
    try:
        return TranscriptAnalysis.model_validate(data)
    except ValidationError as error:
        shown: List[str] = []
        for item in error.errors()[:MAX_ERRORS_SHOWN]:
            location = ".".join(str(part) for part in item["loc"])
            shown.append(f"{location}: {item['msg']}")
        more = len(error.errors()) - len(shown)
        suffix = f" (and {more} more)" if more > 0 else ""
        raise ManualAnalysisError(
            "The JSON does not match the expected shape: " + "; ".join(shown) + suffix
        ) from None


def finalize_analysis(
    analysis: TranscriptAnalysis,
    transcript: str,
    *,
    clip_length: str = "auto",
    max_clips: int,
) -> TranscriptAnalysis:
    """Ground the user's segments on the transcript like a model's, then cap them.

    Same validation as the model path: timestamps snap onto transcript lines,
    out-of-range lengths are repaired or dropped, near-duplicates are removed
    and the best ones are kept, with a bounded reserve for picture checks.
    """
    lines = ai._parse_transcript_lines(transcript)
    spans = ai._parse_transcript_spans(transcript)
    if not lines:
        raise ManualAnalysisError("The task has no timestamped transcript to match the segments against.")

    candidates = ai._validate_segments(analysis.most_relevant_segments, lines, spans, clip_length)
    candidates = ai.dedupe_overlapping_segments(candidates)
    if not candidates:
        raise ManualAnalysisError(
            "None of the segments could be used: each needs a start_time and end_time that match "
            "the transcript timestamps, at least 3 words of text, and a clip-length duration."
        )
    source_seconds = max(line["end_seconds"] for line in lines)
    limit = ai.max_final_clips_for_duration(source_seconds, max_clips)
    return TranscriptAnalysis(
        most_relevant_segments=candidates[: min(24, limit * 2)],
        summary=analysis.summary,
        key_topics=analysis.key_topics,
    )
