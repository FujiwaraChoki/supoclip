"""
Task repository - handles all database operations for tasks.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, Dict, Any, List
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


class TaskRepository:
    """Repository for task-related database operations."""

    @staticmethod
    async def create_task(
        db: AsyncSession,
        user_id: str,
        source_id: Optional[str],
        status: str = "processing",
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        include_broll: bool = False,
        processing_mode: str = "fast",
        task_type: str = "clipping",
    ) -> str:
        """Create a new task and return its ID.

        `source_id` is nullable — a ranking task (`task_type="ranking"`) has
        no single source; its N input videos live in `ranking_inputs` instead.
        """
        task_id = str(uuid4())
        try:
            result = await db.execute(
                text("""
                    INSERT INTO tasks (
                        id, user_id, source_id, status, font_family, font_size, font_color,
                        caption_template, include_broll, processing_mode, task_type,
                        created_at, updated_at
                    )
                    VALUES (
                        :task_id, :user_id, :source_id, :status, :font_family, :font_size, :font_color,
                        :caption_template, :include_broll, :processing_mode, :task_type,
                        NOW(), NOW()
                    )
                    RETURNING id
                """),
                {
                    "task_id": task_id,
                    "user_id": user_id,
                    "source_id": source_id,
                    "status": status,
                    "font_family": font_family,
                    "font_size": font_size,
                    "font_color": font_color,
                    "caption_template": caption_template,
                    "include_broll": include_broll,
                    "processing_mode": processing_mode,
                    "task_type": task_type,
                },
            )
        except Exception:
            await db.rollback()
            result = await db.execute(
                text("""
                    INSERT INTO tasks (
                        id, user_id, source_id, status, font_family, font_size, font_color,
                        task_type, created_at, updated_at
                    )
                    VALUES (
                        :task_id, :user_id, :source_id, :status, :font_family, :font_size, :font_color,
                        :task_type, NOW(), NOW()
                    )
                    RETURNING id
                """),
                {
                    "task_id": task_id,
                    "user_id": user_id,
                    "source_id": source_id,
                    "status": status,
                    "font_family": font_family,
                    "font_size": font_size,
                    "font_color": font_color,
                    "task_type": task_type,
                },
            )
        await db.commit()
        task_id = result.scalar()
        if not task_id:
            raise RuntimeError("Failed to create task: no ID returned")
        logger.info(f"Created task {task_id} for user {user_id}")
        return str(task_id)

    @staticmethod
    async def get_task_by_id(
        db: AsyncSession, task_id: str, include_deleted: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Get task by ID with source information.

        `include_deleted` defaults to True here because this single-task
        lookup backs ownership checks, restore, and purge flows that must be
        able to see a soft-deleted task. Listing queries below always exclude
        soft-deleted tasks.
        """
        deleted_clause = "" if include_deleted else "AND t.deleted_at IS NULL"
        try:
            result = await db.execute(
                text(f"""
                    SELECT t.*, s.title as source_title, s.type as source_type, s.url as source_url
                    FROM tasks t
                    LEFT JOIN sources s ON t.source_id = s.id
                    WHERE t.id = :task_id {deleted_clause}
                """),
                {"task_id": task_id},
            )
        except Exception:
            await db.rollback()
            result = await db.execute(
                text(f"""
                    SELECT t.*, s.title as source_title, s.type as source_type
                    FROM tasks t
                    LEFT JOIN sources s ON t.source_id = s.id
                    WHERE t.id = :task_id {deleted_clause}
                """),
                {"task_id": task_id},
            )
        row = result.fetchone()

        if not row:
            return None

        return {
            "id": row.id,
            "user_id": row.user_id,
            "source_id": row.source_id,
            "source_title": row.source_title,
            "source_type": row.source_type,
            "status": row.status,
            "task_type": getattr(row, "task_type", "clipping"),
            "ranking_settings": getattr(row, "ranking_settings", None),
            "progress": getattr(row, "progress", None),
            "progress_message": getattr(row, "progress_message", None),
            "generated_clips_ids": row.generated_clips_ids,
            "font_family": row.font_family,
            "font_size": row.font_size,
            "font_color": row.font_color,
            "caption_template": getattr(row, "caption_template", "default"),
            "include_broll": getattr(row, "include_broll", False),
            "processing_mode": getattr(row, "processing_mode", "fast"),
            "cache_hit": getattr(row, "cache_hit", False),
            "error_code": getattr(row, "error_code", None),
            "stage_timings_json": getattr(row, "stage_timings_json", None),
            "started_at": getattr(row, "started_at", None),
            "completed_at": getattr(row, "completed_at", None),
            "completion_notification_sent_at": getattr(
                row, "completion_notification_sent_at", None
            ),
            "source_url": getattr(row, "source_url", None),
            "deleted_at": getattr(row, "deleted_at", None),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    async def update_task_runtime_metadata(
        db: AsyncSession,
        task_id: str,
        cache_hit: Optional[bool] = None,
        error_code: Optional[str] = None,
        stage_timings_json: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        params: Dict[str, Any] = {"task_id": task_id}
        set_parts = []

        if cache_hit is not None:
            set_parts.append("cache_hit = :cache_hit")
            params["cache_hit"] = cache_hit

        if error_code is not None:
            set_parts.append("error_code = :error_code")
            params["error_code"] = error_code

        if stage_timings_json is not None:
            set_parts.append("stage_timings_json = :stage_timings_json")
            params["stage_timings_json"] = stage_timings_json

        if started_at is not None:
            set_parts.append("started_at = :started_at")
            params["started_at"] = started_at

        if completed_at is not None:
            set_parts.append("completed_at = :completed_at")
            params["completed_at"] = completed_at

        if not set_parts:
            return

        set_parts.append("updated_at = NOW()")
        query = f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = :task_id"
        await db.execute(text(query), params)
        await db.commit()

    @staticmethod
    async def get_performance_metrics(db: AsyncSession) -> Dict[str, Any]:
        result = await db.execute(
            text(
                """
                SELECT
                    processing_mode,
                    COUNT(*) AS total_tasks,
                    AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) AS avg_seconds,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))) AS p50_seconds,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))) AS p95_seconds,
                    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits
                FROM tasks
                WHERE started_at IS NOT NULL AND completed_at IS NOT NULL
                GROUP BY processing_mode
                ORDER BY processing_mode
                """
            )
        )

        rows = result.fetchall()
        metrics = []
        for row in rows:
            total = int(row.total_tasks or 0)
            cache_hits = int(row.cache_hits or 0)
            metrics.append(
                {
                    "processing_mode": row.processing_mode,
                    "total_tasks": total,
                    "avg_seconds": float(row.avg_seconds or 0),
                    "p50_seconds": float(row.p50_seconds or 0),
                    "p95_seconds": float(row.p95_seconds or 0),
                    "cache_hit_rate": (cache_hits / total) if total else 0,
                }
            )

        return {"modes": metrics}

    @staticmethod
    async def update_task_settings(
        db: AsyncSession,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
    ) -> None:
        """Update task styling settings."""
        try:
            await db.execute(
                text(
                    """
                    UPDATE tasks
                    SET font_family = :font_family,
                        font_size = :font_size,
                        font_color = :font_color,
                        caption_template = :caption_template,
                        include_broll = :include_broll,
                        updated_at = NOW()
                    WHERE id = :task_id
                    """
                ),
                {
                    "task_id": task_id,
                    "font_family": font_family,
                    "font_size": font_size,
                    "font_color": font_color,
                    "caption_template": caption_template,
                    "include_broll": include_broll,
                },
            )
        except Exception:
            await db.rollback()
            await db.execute(
                text(
                    """
                    UPDATE tasks
                    SET font_family = :font_family,
                        font_size = :font_size,
                        font_color = :font_color,
                        updated_at = NOW()
                    WHERE id = :task_id
                    """
                ),
                {
                    "task_id": task_id,
                    "font_family": font_family,
                    "font_size": font_size,
                    "font_color": font_color,
                },
            )
        await db.commit()

    @staticmethod
    async def update_task_font_size(db: AsyncSession, task_id: str, font_size: int) -> None:
        """Persist just the caption font size, without touching other task columns.

        Used by the editor's inline caption size control, which must not clobber
        font_family/font_color/caption_template the way the full `/settings`
        replace-style update would if called with a partial payload.
        """
        await db.execute(
            text(
                """
                UPDATE tasks
                SET font_size = :font_size,
                    updated_at = NOW()
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id, "font_size": font_size},
        )
        await db.commit()

    @staticmethod
    async def update_ranking_settings(
        db: AsyncSession, task_id: str, ranking_settings: Dict[str, Any]
    ) -> None:
        """Persist a ranking task's template/overlay/export settings (JSON-encoded
        TEXT, same convention as generated_clips.reactions)."""
        await db.execute(
            text(
                """
                UPDATE tasks
                SET ranking_settings = :ranking_settings,
                    updated_at = NOW()
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id, "ranking_settings": json.dumps(ranking_settings)},
        )
        await db.commit()

    @staticmethod
    async def update_task_status(
        db: AsyncSession,
        task_id: str,
        status: str,
        progress: Optional[int] = None,
        progress_message: Optional[str] = None,
    ) -> None:
        """Update task status and optional progress."""
        params = {
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "progress_message": progress_message,
        }

        # Build dynamic query based on what's provided
        set_parts = ["status = :status"]

        if progress is not None:
            set_parts.append("progress = :progress")

        if progress_message is not None:
            set_parts.append("progress_message = :progress_message")

        set_parts.append("updated_at = NOW()")

        query = f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = :task_id"

        await db.execute(text(query), params)
        await db.commit()
        logger.info(
            f"Updated task {task_id} status to {status}"
            + (f" (progress: {progress}%)" if progress else "")
        )

    @staticmethod
    async def update_task_clips(
        db: AsyncSession, task_id: str, clip_ids: List[str]
    ) -> None:
        """Update task with generated clip IDs."""
        await db.execute(
            text(
                "UPDATE tasks SET generated_clips_ids = :clip_ids, updated_at = NOW() WHERE id = :task_id"
            ),
            {"clip_ids": clip_ids, "task_id": task_id},
        )
        await db.commit()
        logger.info(f"Updated task {task_id} with {len(clip_ids)} clips")

    @staticmethod
    async def get_user_tasks(
        db: AsyncSession, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get all (non-deleted) tasks for a user."""
        result = await db.execute(
            text("""
                SELECT t.*, s.title as source_title, s.type as source_type, s.url as source_url,
                       (SELECT COUNT(*) FROM generated_clips WHERE task_id = t.id) as clips_count
                FROM tasks t
                LEFT JOIN sources s ON t.source_id = s.id
                WHERE t.user_id = :user_id AND t.deleted_at IS NULL
                ORDER BY t.created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": limit},
        )

        tasks = []
        for row in result.fetchall():
            tasks.append(
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "source_id": row.source_id,
                    "source_title": row.source_title,
                    "source_type": row.source_type,
                    "source_url": getattr(row, "source_url", None),
                    "status": row.status,
                    "task_type": getattr(row, "task_type", "clipping"),
                    "progress": getattr(row, "progress", 0),
                    "progress_message": getattr(row, "progress_message", None),
                    "processing_mode": getattr(row, "processing_mode", "fast"),
                    "completion_notification_sent_at": getattr(
                        row, "completion_notification_sent_at", None
                    ),
                    "clips_count": row.clips_count,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )

        return tasks

    @staticmethod
    async def get_status_counts(db: AsyncSession, user_id: str) -> Dict[str, int]:
        """Count of each non-deleted task status for a user — the cheap
        aggregate the home screen's status strip needs (queue depth, active
        jobs) without pulling full task rows."""
        result = await db.execute(
            text("""
                SELECT status, COUNT(*) as count
                FROM tasks
                WHERE user_id = :user_id AND deleted_at IS NULL
                GROUP BY status
            """),
            {"user_id": user_id},
        )
        return {row.status: row.count for row in result.fetchall()}

    @staticmethod
    async def list_deleted_tasks(
        db: AsyncSession, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get soft-deleted (trashed) tasks for a user, most recently deleted first."""
        result = await db.execute(
            text("""
                SELECT t.*, s.title as source_title, s.type as source_type, s.url as source_url,
                       (SELECT COUNT(*) FROM generated_clips WHERE task_id = t.id) as clips_count
                FROM tasks t
                LEFT JOIN sources s ON t.source_id = s.id
                WHERE t.user_id = :user_id AND t.deleted_at IS NOT NULL
                ORDER BY t.deleted_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": limit},
        )

        tasks = []
        for row in result.fetchall():
            tasks.append(
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "source_id": row.source_id,
                    "source_title": row.source_title,
                    "source_type": row.source_type,
                    "source_url": getattr(row, "source_url", None),
                    "status": row.status,
                    "task_type": getattr(row, "task_type", "clipping"),
                    "processing_mode": getattr(row, "processing_mode", "fast"),
                    "clips_count": row.clips_count,
                    "deleted_at": getattr(row, "deleted_at", None),
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )

        return tasks

    @staticmethod
    async def enable_sharing(
        db: AsyncSession, task_id: str, share_token: str
    ) -> Optional[str]:
        """Enable public sharing and return the token.

        The token is stable while sharing stays enabled (repeat calls keep the
        same URL), but rotates when re-enabling after a revoke so previously
        leaked links stay dead.
        """
        result = await db.execute(
            text(
                """
                UPDATE tasks
                SET share_token = CASE
                        WHEN share_enabled THEN COALESCE(share_token, :share_token)
                        ELSE :share_token
                    END,
                    share_enabled = TRUE,
                    updated_at = NOW()
                WHERE id = :task_id
                RETURNING share_token
                """
            ),
            {"task_id": task_id, "share_token": share_token},
        )
        await db.commit()
        row = result.fetchone()
        return str(row.share_token) if row else None

    @staticmethod
    async def disable_sharing(db: AsyncSession, task_id: str) -> bool:
        """Disable an existing share URL without changing private task access."""
        result = await db.execute(
            text(
                """
                UPDATE tasks
                SET share_enabled = FALSE,
                    updated_at = NOW()
                WHERE id = :task_id
                RETURNING id
                """
            ),
            {"task_id": task_id},
        )
        await db.commit()
        return result.fetchone() is not None

    @staticmethod
    async def get_shared_task_id(
        db: AsyncSession, share_token: str
    ) -> Optional[str]:
        """Resolve an enabled opaque share token to its task ID."""
        result = await db.execute(
            text(
                """
                SELECT id
                FROM tasks
                WHERE share_token = :share_token
                  AND share_enabled = TRUE
                  AND status = 'completed'
                  AND deleted_at IS NULL
                LIMIT 1
                """
            ),
            {"share_token": share_token},
        )
        row = result.fetchone()
        return str(row.id) if row else None

    @staticmethod
    async def user_exists(db: AsyncSession, user_id: str) -> bool:
        """Check if a user exists in the database."""
        result = await db.execute(
            text("SELECT 1 FROM users WHERE id = :user_id"), {"user_id": user_id}
        )
        return result.fetchone() is not None

    @staticmethod
    async def delete_task(db: AsyncSession, task_id: str) -> None:
        """Soft-delete a task by ID (sets deleted_at; the row and its clips stay).

        Use `purge_task` for the actual hard delete, once a trashed task is
        permanently removed.
        """
        await db.execute(
            text(
                "UPDATE tasks SET deleted_at = NOW(), updated_at = NOW() "
                "WHERE id = :task_id AND deleted_at IS NULL"
            ),
            {"task_id": task_id},
        )
        await db.commit()
        logger.info(f"Soft-deleted task {task_id}")

    @staticmethod
    async def restore_task(db: AsyncSession, task_id: str) -> bool:
        """Restore a soft-deleted task. Returns True if a row was restored."""
        result = await db.execute(
            text(
                "UPDATE tasks SET deleted_at = NULL, updated_at = NOW() "
                "WHERE id = :task_id AND deleted_at IS NOT NULL "
                "RETURNING id"
            ),
            {"task_id": task_id},
        )
        await db.commit()
        restored = result.fetchone() is not None
        if restored:
            logger.info(f"Restored task {task_id} from trash")
        return restored

    @staticmethod
    async def purge_task(db: AsyncSession, task_id: str) -> None:
        """Permanently delete a task row (hard delete). Only call this from the
        purge path, once any on-disk clip files have already been cleaned up."""
        await db.execute(
            text("DELETE FROM tasks WHERE id = :task_id"), {"task_id": task_id}
        )
        await db.commit()
        logger.info(f"Purged task {task_id}")

    @staticmethod
    async def get_task_notification_context(
        db: AsyncSession, task_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT
                    t.id,
                    u.notify_on_completion,
                    t.completion_notification_sent_at,
                    s.title AS source_title,
                    u.email AS user_email,
                    u.name AS user_name,
                    u.first_name AS user_first_name
                FROM tasks t
                JOIN users u ON u.id = t.user_id
                LEFT JOIN sources s ON s.id = t.source_id
                WHERE t.id = :task_id
                """
            ),
            {"task_id": task_id},
        )
        row = result.fetchone()
        if not row:
            return None

        return {
            "task_id": row.id,
            "notify_on_completion": getattr(row, "notify_on_completion", False),
            "completion_notification_sent_at": getattr(
                row, "completion_notification_sent_at", None
            ),
            "source_title": getattr(row, "source_title", None),
            "user_email": getattr(row, "user_email", None),
            "user_name": getattr(row, "user_name", None),
            "user_first_name": getattr(row, "user_first_name", None),
        }

    @staticmethod
    async def mark_completion_notification_sent(
        db: AsyncSession, task_id: str
    ) -> bool:
        result = await db.execute(
            text(
                """
                UPDATE tasks
                SET completion_notification_sent_at = NOW(),
                    updated_at = NOW()
                WHERE id = :task_id
                  AND completion_notification_sent_at IS NULL
                RETURNING completion_notification_sent_at
                """
            ),
            {"task_id": task_id},
        )
        await db.commit()
        return result.fetchone() is not None
