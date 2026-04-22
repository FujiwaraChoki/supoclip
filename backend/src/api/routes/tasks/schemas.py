"""Pydantic request schemas for the ``/tasks/*`` and ``/tasks/{id}/clips/*`` endpoints.

These are a thin validation layer — they replace the hand-rolled
``payload = await request.json()`` parsing that previously lived inline in
each route, so FastAPI/Pydantic returns a structured 422 response on bad
input instead of the routes reaching the service layer with silently
coerced values.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClipTrimRequest(BaseModel):
    """Input for ``PATCH /tasks/{task_id}/clips/{clip_id}``."""

    start_offset: float = Field(0.0, ge=0, description="Seconds to trim from the start")
    end_offset: float = Field(0.0, ge=0, description="Seconds to trim from the end")


class ClipSplitRequest(BaseModel):
    """Input for ``POST /tasks/{task_id}/clips/{clip_id}/split``."""

    split_time: float = Field(..., gt=0, description="Seconds into the clip")


class ClipMergeRequest(BaseModel):
    """Input for ``POST /tasks/{task_id}/clips/merge``."""

    clip_ids: List[str] = Field(..., min_length=2)

    @field_validator("clip_ids")
    @classmethod
    def _reject_blank_ids(cls, v: List[str]) -> List[str]:
        cleaned = [item.strip() for item in v]
        if any(not item for item in cleaned):
            raise ValueError("clip_ids entries must be non-empty strings")
        return cleaned


class ClipCaptionsRequest(BaseModel):
    """Input for ``PATCH /tasks/{task_id}/clips/{clip_id}/captions``."""

    caption_text: str = Field("", max_length=8000)
    position: Literal["top", "center", "bottom"] = "bottom"
    highlight_words: List[str] = Field(default_factory=list)

    @field_validator("highlight_words")
    @classmethod
    def _clean_highlight_words(cls, v: List[str]) -> List[str]:
        return [str(item) for item in v if str(item).strip()]


class ClipRegenerateRequest(ClipTrimRequest):
    """Same shape as trim; kept distinct for route clarity."""


# ---------------------------------------------------------------------------
# Task lifecycle schemas
# ---------------------------------------------------------------------------


HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


class SourceInput(BaseModel):
    """Input source for a new task (YouTube URL or uploaded video reference)."""

    url: str = Field(..., min_length=1, max_length=2048)
    title: Optional[str] = Field(None, max_length=500)


class FontOptions(BaseModel):
    """Styling options for subtitles on a new task."""

    model_config = ConfigDict(extra="ignore")

    font_family: str = Field("TikTokSans-Regular", min_length=1, max_length=120)
    font_size: int = Field(24, ge=12, le=72)
    font_color: str = Field("#FFFFFF", pattern=HEX_COLOR_PATTERN)
    stroke_color: Optional[str] = Field(None, pattern=HEX_COLOR_PATTERN)
    stroke_width: Optional[int] = Field(None, ge=0, le=12)
    bold: bool = False
    italic: bool = False
    underline: bool = False


class CreateTaskRequest(BaseModel):
    """Input for ``POST /tasks/``."""

    model_config = ConfigDict(extra="ignore")

    source: SourceInput
    font_options: FontOptions = Field(default_factory=FontOptions)
    caption_template: str = Field("default", min_length=1, max_length=64)
    include_broll: bool = False
    processing_mode: Optional[Literal["fast", "balanced", "quality"]] = None
    output_format: Literal["vertical", "fit", "original", "capcut"] = "vertical"
    add_subtitles: bool = True
    avoid_original_subtitle: Literal["none", "bottom", "top"] = "none"


class TaskSettingsRequest(BaseModel):
    """Input for ``POST /tasks/{task_id}/settings``."""

    model_config = ConfigDict(extra="ignore")

    font_family: str = Field("TikTokSans-Regular", min_length=1, max_length=120)
    font_size: int = Field(24, ge=12, le=72)
    font_color: str = Field("#FFFFFF", pattern=HEX_COLOR_PATTERN)
    caption_template: str = Field("default", min_length=1, max_length=64)
    include_broll: bool = False
    apply_to_existing: bool = False
