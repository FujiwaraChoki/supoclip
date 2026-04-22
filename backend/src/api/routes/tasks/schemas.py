"""Pydantic request schemas for the ``/tasks/{id}/clips/*`` endpoints.

These are a thin validation layer — they replace the hand-rolled
``payload = await request.json()`` parsing that previously lived inline in
each route, so FastAPI/Pydantic returns a structured 422 response on bad
input instead of the routes reaching the service layer with silently
coerced values.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field, field_validator


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
