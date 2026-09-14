"""Orchestrates per-clip metadata generation: one LLM call per video for the
video-level regenerate action, a lighter per-clip call for the single-clip
regenerate button, and the manual-edit path — all respecting the
"never overwrite a user's manual edit" rule on passive/automatic paths."""

import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..metadata_generation import (
    ClipContext,
    generate_metadata_for_single_clip,
    generate_metadata_for_video,
)
from ..repositories.clip_repository import ClipRepository

logger = logging.getLogger(__name__)


class MetadataService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def regenerate_project_metadata(
        self,
        task_id: str,
        clips: List[Dict[str, Any]],
        *,
        video_title: Optional[str] = None,
        allow_gemini: bool = False,
    ) -> Dict[str, Any]:
        """Video-level "Regenerate all metadata" — one LLM call, overwrites
        every clip's metadata outright (explicit user action, so overwriting
        even previously-user-edited fields is correct here)."""
        if not clips:
            return {"generated": 0, "provider": "unavailable"}

        contexts = [
            ClipContext(clip_id=c["id"], text=c.get("text", ""), hook_title=c.get("hook_title"))
            for c in clips
        ]
        start = time.perf_counter()
        results, provider = await generate_metadata_for_video(
            contexts, video_title=video_title, allow_gemini=allow_gemini
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        text_by_clip_id = {c["id"]: c.get("text", "") for c in clips}
        generated = 0
        for clip_id, meta in results.items():
            await ClipRepository.update_clip_metadata(
                self.db,
                clip_id,
                title=meta.title,
                description=meta.description,
                tags=meta.tags,
                provider=provider,
                generation_ms=elapsed_ms,
                source_text=text_by_clip_id.get(clip_id, ""),
            )
            generated += 1

        logger.info(
            "Metadata generation for task %s: %d/%d clips via %s",
            task_id,
            generated,
            len(clips),
            provider,
        )
        return {"generated": generated, "total": len(clips), "provider": provider}

    async def regenerate_clip_metadata(
        self,
        clip: Dict[str, Any],
        *,
        video_title: Optional[str] = None,
        allow_gemini: bool = False,
        quality: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Per-clip "Regenerate" button — a lighter single-clip call rather
        than re-running the whole video's batch. `quality` optionally
        overrides the model for just this call ("fast"/"balanced"/"high"
        pick an Ollama model size, "gemini" forces the Gemini fallback
        outright) without touching the global OLLAMA_MODEL setting."""
        context = ClipContext(
            clip_id=clip["id"], text=clip.get("text", ""), hook_title=clip.get("hook_title")
        )
        start = time.perf_counter()
        meta, provider = await generate_metadata_for_single_clip(
            context, video_title=video_title, allow_gemini=allow_gemini, quality=quality
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if meta is None:
            return None

        await ClipRepository.update_clip_metadata(
            self.db,
            clip["id"],
            title=meta.title,
            description=meta.description,
            tags=meta.tags,
            provider=provider,
            generation_ms=elapsed_ms,
            source_text=clip.get("text", ""),
        )
        return {
            "title": meta.title,
            "description": meta.description,
            "tags": meta.tags,
            "provider": provider,
            "generation_ms": elapsed_ms,
        }

    async def maybe_regenerate_on_transcript_change(
        self,
        clips: List[Dict[str, Any]],
        *,
        video_title: Optional[str] = None,
        allow_gemini: bool = False,
    ) -> None:
        """Passive path (transcript change / re-cut): regenerates metadata
        but skips any field the user has manually edited, refilling only the
        untouched ones. Used by the automatic post-processing pipeline, never
        by the explicit "Regenerate" button (which always overwrites)."""
        if not clips:
            return

        contexts = [
            ClipContext(clip_id=c["id"], text=c.get("text", ""), hook_title=c.get("hook_title"))
            for c in clips
        ]
        results, provider = await generate_metadata_for_video(
            contexts, video_title=video_title, allow_gemini=allow_gemini
        )
        if provider == "unavailable":
            return

        clips_by_id = {c["id"]: c for c in clips}
        for clip_id, meta in results.items():
            existing = clips_by_id.get(clip_id, {})
            title = None if existing.get("metadata_title_user_edited") else meta.title
            description = None if existing.get("metadata_description_user_edited") else meta.description
            tags = None if existing.get("metadata_tags_user_edited") else meta.tags
            if title is None and description is None and tags is None:
                continue
            await ClipRepository.update_clip_metadata_fields(
                self.db, clip_id, title=title, description=description, tags=tags
            )

    async def update_clip_metadata_fields(
        self,
        clip_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Manual user edit — delegates straight to the repository, which
        marks the corresponding *_user_edited flag(s) TRUE."""
        from ..metadata_generation import normalize_tags

        await ClipRepository.update_clip_metadata_fields(
            self.db,
            clip_id,
            title=title,
            description=description,
            tags=normalize_tags(tags) if tags is not None else None,
        )
