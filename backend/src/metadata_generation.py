"""Per-clip SEO metadata (title/description/tags) generation.

One LLM call per video (not per clip) for the "generate all" path, batching
every clip's transcript excerpt into a single prompt/response — this is the
main cost-minimization lever per spec. A lighter single-clip prompt exists
separately for the per-clip "Regenerate" button, since re-running the full
video batch for one clip would be disproportionate.
"""

import re
from typing import List, Optional

from pydantic import BaseModel, Field

from .ai import run_with_llm_fallback

TITLE_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 100
MAX_TRANSCRIPT_WORDS_PER_CLIP = 200
MAX_TAGS = 10

# Short, fixed system prompt reused verbatim for every call — no per-call
# variation and no step-by-step reasoning instructions, both of which would
# cost tokens (and therefore local-inference time) without improving output.
METADATA_SYSTEM_PROMPT = """You write short-form video metadata: SEO-optimized titles, descriptions, and tags for viral short clips.

For each clip, output:
- title: 30-60 characters exactly (not a hard word count, but never shorter than 30 or longer than 60), SEO-optimized, short-form-platform-friendly (TikTok/Reels/Shorts style)
- description: 50-100 characters, SEO-focused, no hashtags
- tags: 5-10 short lowercase tags (hyphenate multi-word tags, no spaces), covering these dimensions where applicable: content type (funny/educational/ranking/reaction/story/opinion), theme (free-form, e.g. football/sleep/finance), tone (serious/humorous/surprising/inspirational), hook_style (question/ranking/contrast/warning)

Keep the theme tag consistent across all clips in the same batch when they share a common subject.

Examples of good output (for calibration only, not the actual clips to generate):

Transcript: "So I tried waking up at 5am for 30 days straight and honestly the first week almost broke me..."
{"title": "I Woke Up At 5AM For 30 Days Straight", "description": "The brutal first week of a 30-day 5am challenge", "tags": ["story", "productivity", "challenge", "self-improvement", "morning-routine"]}

Transcript: "Here's why your sourdough starter keeps dying: you're probably feeding it straight from the fridge..."
{"title": "The #1 Reason Your Sourdough Starter Keeps Dying", "description": "Common sourdough starter mistake, fixed in under a minute", "tags": ["educational", "baking", "sourdough", "cooking-tips", "food"]}

Transcript: "Would you rather have unlimited money but no friends, or unlimited friends but no money? Comment below..."
{"title": "Unlimited Money, No Friends... Or The Opposite?", "description": "A would-you-rather that's harder than it sounds", "tags": ["question", "would-you-rather", "opinion", "money", "relationships"]}

Output ONLY the requested JSON — no explanation, no reasoning, no extra commentary."""


class ClipMetadata(BaseModel):
    title: str = Field(description="30-60 char SEO title")
    description: str = Field(description="50-100 char SEO description, no hashtags")
    tags: List[str] = Field(default_factory=list, description="5-10 short lowercase tags")


class MetadataBatchEntry(ClipMetadata):
    clip_index: int = Field(description="0-based index matching the input clip order")


class MetadataBatchOutput(BaseModel):
    clips: List[MetadataBatchEntry] = Field(default_factory=list)


def truncate_at_word_boundary(text: str, max_len: int) -> str:
    """Truncate `text` to at most `max_len` characters, never mid-word."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated or text[:max_len]


def normalize_tags(tags: List[str]) -> List[str]:
    """Lowercase, hyphenate spaces, strip stray punctuation, dedupe, cap at MAX_TAGS."""
    seen = set()
    normalized: List[str] = []
    for tag in tags:
        cleaned = re.sub(r"[^a-z0-9-]", "", tag.strip().lower().replace(" ", "-"))
        cleaned = re.sub(r"-+", "-", cleaned).strip("-")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
        if len(normalized) >= MAX_TAGS:
            break
    return normalized


def _truncate_transcript(text: str, max_words: int = MAX_TRANSCRIPT_WORDS_PER_CLIP) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


class ClipContext(BaseModel):
    """Minimal per-clip input to metadata generation."""

    clip_id: str
    text: str
    hook_title: Optional[str] = None


def _build_batch_prompt(video_title: Optional[str], clips: List[ClipContext]) -> str:
    lines = []
    if video_title:
        lines.append(f"Video title: {video_title}")
    lines.append(f"Generate metadata for {len(clips)} clips. Respond with a JSON object matching this shape:")
    lines.append('{"clips": [{"clip_index": 0, "title": "...", "description": "...", "tags": ["..."]}]}')
    lines.append("")
    for idx, clip in enumerate(clips):
        excerpt = _truncate_transcript(clip.text)
        hook = f" Hook: {clip.hook_title}" if clip.hook_title else ""
        lines.append(f"Clip {idx}:{hook} Transcript: {excerpt}")
    return "\n".join(lines)


def _build_single_clip_prompt(video_title: Optional[str], clip: ClipContext) -> str:
    excerpt = _truncate_transcript(clip.text)
    hook = f"Hook: {clip.hook_title}\n" if clip.hook_title else ""
    title_line = f"Video title: {video_title}\n" if video_title else ""
    return (
        f"{title_line}{hook}Transcript: {excerpt}\n\n"
        'Respond with a JSON object: {"title": "...", "description": "...", "tags": ["..."]}'
    )


def _normalize_clip_metadata(meta: ClipMetadata) -> ClipMetadata:
    return ClipMetadata(
        title=truncate_at_word_boundary(meta.title.strip(), TITLE_MAX_CHARS),
        description=truncate_at_word_boundary(meta.description.strip(), DESCRIPTION_MAX_CHARS),
        tags=normalize_tags(meta.tags),
    )


async def generate_metadata_for_video(
    clips: List[ClipContext],
    *,
    video_title: Optional[str] = None,
    allow_gemini: bool = False,
) -> tuple[dict[str, ClipMetadata], str]:
    """Generate metadata for every clip in one LLM call.

    Returns (metadata_by_clip_id, provider). A clip missing from the
    returned dict means its entry couldn't be parsed/validated even after
    retry+fallback — the caller should leave that clip's metadata untouched
    (per-clip partial success, not all-or-nothing) rather than fail the batch.
    """
    if not clips:
        return {}, "unavailable"

    prompt = _build_batch_prompt(video_title, clips)
    result, provider = await run_with_llm_fallback(
        prompt=prompt,
        output_type=MetadataBatchOutput,
        system_prompt=METADATA_SYSTEM_PROMPT,
        allow_gemini=allow_gemini,
        # A batch response scales with clip count; 2000 tokens could truncate
        # mid-JSON for a video with many clips, especially with few-shot
        # examples in context. 4000 gives 7B-class models room to finish.
        max_output_tokens=4000,
    )
    if provider == "unavailable" or result is None:
        return {}, "unavailable"

    by_clip_id: dict[str, ClipMetadata] = {}
    for entry in result.clips:
        if not (0 <= entry.clip_index < len(clips)):
            continue
        clip_id = clips[entry.clip_index].clip_id
        by_clip_id[clip_id] = _normalize_clip_metadata(entry)
    return by_clip_id, provider


async def generate_metadata_for_single_clip(
    clip: ClipContext,
    *,
    video_title: Optional[str] = None,
    allow_gemini: bool = False,
    quality: Optional[str] = None,
) -> tuple[Optional[ClipMetadata], str]:
    """Generate metadata for exactly one clip — used by the per-clip
    "Regenerate" button so it doesn't re-run the full video's batch call.

    `quality` ("fast"/"balanced"/"high"/"gemini") lets that button pick a
    different model for just this call, overriding the global OLLAMA_MODEL
    setting and/or forcing the Gemini fallback outright."""
    prompt = _build_single_clip_prompt(video_title, clip)
    result, provider = await run_with_llm_fallback(
        prompt=prompt,
        output_type=ClipMetadata,
        system_prompt=METADATA_SYSTEM_PROMPT,
        allow_gemini=allow_gemini or quality == "gemini",
        quality=quality,
    )
    if provider == "unavailable" or result is None:
        return None, "unavailable"
    return _normalize_clip_metadata(result), provider
