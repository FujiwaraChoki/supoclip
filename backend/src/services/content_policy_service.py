"""Orchestrates content-policy scanning: regex engine + optional Ollama
borderline-phrase check, writing results onto generated_clips."""

import logging
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from ..content_policy import ollama_borderline_check, scan_text
from ..repositories.clip_repository import ClipRepository
from ..repositories.content_policy_repository import ContentPolicyRepository

logger = logging.getLogger(__name__)


class ContentPolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def scan_clip(
        self,
        user_id: str,
        clip_id: str,
        clip_text: str,
        *,
        project_settings: Dict[str, Any],
        borderline_phrases: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        """Scan one clip's caption text and persist the resulting flags."""
        if not project_settings.get("enabled", True):
            await ClipRepository.update_clip_content_policy_flags(self.db, clip_id, [])
            return []

        word_lists = await ContentPolicyRepository.get_word_lists_for_user(self.db, user_id)
        flags = scan_text(
            clip_text,
            word_lists,
            project_settings.get("sensitivity", "medium"),
            project_settings.get("categories_enabled", {}),
        )

        for phrase in borderline_phrases or []:
            idx = clip_text.lower().find(phrase.lower())
            if idx == -1:
                continue
            flags.append(
                {
                    "word": clip_text[idx : idx + len(phrase)],
                    "category": "borderline",
                    "start": idx,
                    "end": idx + len(phrase),
                    "severity": "borderline",
                    "source": "llm",
                }
            )
        flags.sort(key=lambda f: f["start"])

        await ClipRepository.update_clip_content_policy_flags(self.db, clip_id, flags)
        return flags

    async def scan_video(
        self,
        user_id: str,
        task_id: str,
        clips: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Scan every clip of a video. Runs the (optional) Ollama borderline
        check once for the whole video, per spec, then applies its result
        alongside the regex scan per clip."""
        project_settings = await ContentPolicyRepository.get_project_settings(self.db, task_id)
        if not project_settings.get("enabled", True):
            results: Dict[str, List[Dict[str, Any]]] = {}
            for clip in clips:
                await ClipRepository.update_clip_content_policy_flags(self.db, clip["id"], [])
                results[clip["id"]] = []
            return results

        borderline_phrases: List[str] = []
        if project_settings.get("ollama_borderline_check_enabled", False):
            full_transcript = "\n".join(clip.get("text", "") for clip in clips)
            try:
                borderline_phrases = await ollama_borderline_check(full_transcript)
            except Exception as exc:
                logger.info("Ollama borderline check failed (%s); continuing with regex only", exc)
                borderline_phrases = []

        results = {}
        for clip in clips:
            flags = await self.scan_clip(
                user_id,
                clip["id"],
                clip.get("text", ""),
                project_settings=project_settings,
                borderline_phrases=borderline_phrases,
            )
            results[clip["id"]] = flags
        return results
