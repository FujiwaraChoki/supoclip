"""Orchestrates the batch processing queue: sequential item processing,
rate limiting, and resource-slot reuse for transcription/render steps.

Batch orchestration itself uses no LLM (per spec) — content-policy/metadata
generation still happen per-item as part of the normal TaskService.process_task
pipeline, which already serializes its own LLM/render calls via
workers/resource_locks.py.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.batch_queue_repository import BatchQueueRepository
from ..repositories.template_repository import TemplateRepository
from .task_service import TaskService

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"queued", "running"}


class BatchCancelled(Exception):
    pass


class BatchQueueService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = BatchQueueRepository

    async def create_batch(
        self,
        user_id: str,
        items: List[Dict[str, str]],
        *,
        template_id: Optional[str] = None,
        auto_export_to_source: bool = False,
        delay_between_items_seconds: int = 3,
    ) -> Dict[str, Any]:
        """`items`: [{"source_filename": ..., "source_path": "upload://..."}]"""
        queue_id = str(uuid.uuid4())
        await self.repo.create_queue(
            self.db,
            queue_id,
            user_id,
            template_id=template_id,
            auto_export_to_source=auto_export_to_source,
            delay_between_items_seconds=delay_between_items_seconds,
        )
        items_with_ids = [{**item, "id": str(uuid.uuid4())} for item in items]
        await self.repo.create_items(self.db, queue_id, items_with_ids)
        return await self.repo.get_queue(self.db, queue_id)

    async def _resolve_template_settings(self, template_id: Optional[str], user_id: str) -> Dict[str, Any]:
        if not template_id:
            return {}
        template = await TemplateRepository.get_by_id(self.db, user_id, template_id)
        if not template:
            return {}
        # Import locally to avoid a circular import (templates.py imports from tasks.py).
        from ..api.routes.templates import migrate_template_settings

        return migrate_template_settings(template["settings"], template["schema_version"])

    async def run_batch(
        self,
        queue_id: str,
        *,
        should_pause_or_cancel: Optional[Any] = None,
    ) -> None:
        """The ARQ job body: walks items in order, never aborting the batch on
        a single item's failure (per spec). `should_pause_or_cancel`, if given,
        is an async callable returning "pause" | "cancel" | None, checked
        between items."""
        queue = await self.repo.get_queue(self.db, queue_id)
        if not queue:
            logger.warning("Batch queue %s not found", queue_id)
            return

        await self.repo.update_queue_status(self.db, queue_id, "running", started_at=True)
        template_settings = await self._resolve_template_settings(
            queue["template_id"], queue["user_id"]
        )
        items = await self.repo.get_items(self.db, queue_id)
        delay = queue["delay_between_items_seconds"]

        try:
            for idx, item in enumerate(items):
                if item["status"] in ("done", "skipped"):
                    continue

                if should_pause_or_cancel:
                    action = await should_pause_or_cancel()
                    if action == "cancel":
                        raise BatchCancelled()
                    if action == "pause":
                        await self.repo.update_queue_status(self.db, queue_id, "paused")
                        return

                await self._process_item(item, queue, template_settings)

                if idx < len(items) - 1 and delay > 0:
                    await asyncio.sleep(delay)

            await self.repo.update_queue_status(self.db, queue_id, "completed", completed_at=True)
        except BatchCancelled:
            await self.repo.update_queue_status(self.db, queue_id, "cancelled", completed_at=True)

    async def _process_item(
        self, item: Dict[str, Any], queue: Dict[str, Any], template_settings: Dict[str, Any]
    ) -> None:
        item_id = item["id"]
        try:
            await self.repo.update_item_status(
                self.db, item_id, status="processing", current_stage="creating", progress_percent=0
            )

            task_service = TaskService(self.db)
            # "transcription"/"render" slots cap real concurrency at 1 across
            # the whole app (not just this batch) — process_task already
            # acquires them internally via its own pipeline stages, same as
            # any single-video task; the batch adds no separate locking here,
            # it just guarantees items run one at a time within this job.
            task_id = await task_service.create_task_with_source(
                queue["user_id"],
                item["source_path"],
                title=item["source_filename"],
                font_family=template_settings.get("font_family"),
                font_size=template_settings.get("font_size"),
                font_color=template_settings.get("font_color"),
                caption_template=template_settings.get("caption_template") or "default",
                include_broll=bool(template_settings.get("include_broll", False)),
            )
            await self.repo.update_item_status(self.db, item_id, task_id=task_id, current_stage="processing")

            async def progress_callback(progress: int, message: str, status: str, stage: Optional[str] = None):
                await self.repo.update_item_status(
                    self.db, item_id, progress_percent=progress, current_stage=stage
                )

            # Real concurrency-1 guarantee for this batch comes from awaiting
            # each item fully before starting the next (this loop is
            # sequential by construction) — the "llm"/"gpu" resource slots
            # from ai.py/resource_locks.py additionally prevent a batch item's
            # LLM calls from overlapping its own render pass. A dedicated
            # "transcription" slot capping cross-task (not just cross-batch)
            # transcription concurrency is not implemented in this pass — it
            # would need a change inside VideoService.generate_transcript
            # itself, shared by both the batch and single-video paths.
            await task_service.process_task(
                task_id,
                item["source_path"],
                task_service.video_service.determine_source_type(item["source_path"]),
                user_id=queue["user_id"],
                font_family=template_settings.get("font_family"),
                font_size=template_settings.get("font_size"),
                font_color=template_settings.get("font_color"),
                caption_template=template_settings.get("caption_template") or "default",
                processing_mode="fast",
                output_format=template_settings.get("output_format") or "vertical",
                add_subtitles=template_settings.get("add_subtitles", True),
                progress_callback=progress_callback,
                hook_style=template_settings.get("hook_style"),
                social_overlay=template_settings.get("social_overlay"),
                target_duration_seconds=template_settings.get("target_duration_seconds"),
                max_clips=template_settings.get("max_clips"),
            )
            if queue.get("auto_export_to_source"):
                await self._auto_export_to_source(task_id, item)

            await self.repo.update_item_status(
                self.db, item_id, status="done", progress_percent=100, current_stage="complete"
            )
        except Exception as exc:
            logger.warning("Batch item %s failed: %s", item_id, exc)
            await self.repo.update_item_status(self.db, item_id, status="error", error_message=str(exc))
            # Never re-raise — a failed item stays in the queue for retry and
            # the batch continues to the next item, per spec.

    async def _auto_export_to_source(self, task_id: str, item: Dict[str, Any]) -> None:
        """Best-effort: export rendered clips into the source video's own
        directory. Never lets an export failure fail the batch item itself."""
        import os
        import shutil

        from ..repositories.clip_repository import ClipRepository

        source_path = item.get("source_path") or ""
        if not source_path or source_path.startswith("upload://"):
            # Uploaded files don't have a real filesystem directory to export
            # back into (they live in object/temp storage under a generated
            # key) — auto-export only makes sense for local-path sources.
            return
        source_dir = os.path.dirname(source_path)
        if not source_dir or not os.path.isdir(source_dir):
            return
        try:
            clips = await ClipRepository.get_clips_by_task(self.db, task_id)
            for clip in clips:
                file_path = clip.get("file_path")
                if file_path and os.path.exists(file_path):
                    shutil.copy2(file_path, os.path.join(source_dir, os.path.basename(file_path)))
        except Exception as exc:
            logger.warning("Auto-export-to-source failed for task %s: %s", task_id, exc)

    async def pause_batch(self, queue_id: str) -> None:
        await self.repo.update_queue_status(self.db, queue_id, "paused")

    async def resume_batch(self, queue_id: str, *, skip_failed: bool = False) -> None:
        if skip_failed:
            await self.repo.mark_items_skipped_if_error(self.db, queue_id)
        await self.repo.update_queue_status(self.db, queue_id, "queued")

    async def cancel_batch(self, queue_id: str) -> None:
        await self.repo.update_queue_status(self.db, queue_id, "cancelled", completed_at=True)

    async def retry_item(self, item_id: str) -> None:
        await self.repo.update_item_status(
            self.db, item_id, status="pending", error_message=None, increment_retry=True
        )
