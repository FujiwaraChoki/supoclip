"""Database access for queued clip preset exports."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .media_repository import queue_delete

LIVE_STATUSES = ("queued", "running", "succeeded")
ACTIVE_STATUSES = ("queued", "running")


def _row(result) -> Optional[Dict[str, Any]]:
    mapped = result.mappings().first()
    return dict(mapped) if mapped is not None else None


class ClipExportRepository:
    @staticmethod
    async def lock_options(db: AsyncSession, clip_id: str, options_hash: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 902110))"),
            {"key": f"{clip_id}:{options_hash}"},
        )

    @staticmethod
    async def get(db: AsyncSession, export_id: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text("SELECT * FROM clip_exports WHERE id = :id"),
            {"id": export_id},
        )
        return _row(result)

    @staticmethod
    async def get_for_task_clip(
        db: AsyncSession, export_id: str, task_id: str, clip_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT * FROM clip_exports
                WHERE id = :id AND task_id = :task_id AND clip_id = :clip_id
                """
            ),
            {"id": export_id, "task_id": task_id, "clip_id": clip_id},
        )
        return _row(result)

    @staticmethod
    async def get_live(
        db: AsyncSession, clip_id: str, options_hash: str, *, for_update: bool = False
    ) -> Optional[Dict[str, Any]]:
        lock = " FOR UPDATE" if for_update else ""
        result = await db.execute(
            text(
                f"""
                SELECT * FROM clip_exports
                WHERE clip_id = :clip_id AND options_hash = :options_hash
                  AND status IN ('queued', 'running', 'succeeded')
                ORDER BY created_at DESC
                LIMIT 1{lock}
                """
            ),
            {"clip_id": clip_id, "options_hash": options_hash},
        )
        return _row(result)

    @staticmethod
    async def insert(
        db: AsyncSession,
        *,
        export_id: str,
        task_id: str,
        clip_id: str,
        user_id: str,
        preset: str,
        options_hash: str,
        source_file_path: str,
        download_filename: str,
        ttl_seconds: int,
    ) -> Dict[str, Any]:
        result = await db.execute(
            text(
                """
                INSERT INTO clip_exports (
                    id, task_id, clip_id, user_id, preset, options_hash, source_file_path,
                    status, progress, download_filename, created_at, updated_at, expires_at
                ) VALUES (
                    :id, :task_id, :clip_id, :user_id, :preset, :options_hash, :source_file_path,
                    'queued', 0, :download_filename, NOW(), NOW(),
                    NOW() + make_interval(secs => :ttl_seconds)
                )
                RETURNING *
                """
            ),
            {
                "id": export_id,
                "task_id": task_id,
                "clip_id": clip_id,
                "user_id": user_id,
                "preset": preset,
                "options_hash": options_hash,
                "source_file_path": source_file_path,
                "download_filename": download_filename,
                "ttl_seconds": ttl_seconds,
            },
        )
        row = _row(result)
        if row is None:
            raise RuntimeError("Failed to create clip export")
        return row

    @staticmethod
    async def list_queued(
        db: AsyncSession,
        *,
        export_id: Optional[str] = None,
        clip_id: Optional[str] = None,
        options_hash: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        clauses = ["status = 'queued'"]
        params: Dict[str, Any] = {}
        if export_id is not None:
            clauses.append("id = :id")
            params["id"] = export_id
        if clip_id is not None:
            clauses.append("clip_id = :clip_id")
            params["clip_id"] = clip_id
        if options_hash is not None:
            clauses.append("options_hash = :options_hash")
            params["options_hash"] = options_hash
        result = await db.execute(
            text(
                f"""
                SELECT id, created_at FROM clip_exports
                WHERE {' AND '.join(clauses)}
                """
            ),
            params,
        )
        return [dict(row) for row in result.mappings()]

    @staticmethod
    async def fail_stale_running(
        db: AsyncSession,
        *,
        message: str,
        stale_seconds: int,
        export_id: Optional[str] = None,
        clip_id: Optional[str] = None,
        options_hash: Optional[str] = None,
    ) -> int:
        """Fail a running encode whose worker stopped heartbeating.

        Queued rows are not included. They sit on the shared worker queue
        behind generation jobs and are not touched until a worker starts them.
        """
        clauses = [
            "status = 'running'",
            "updated_at < NOW() - make_interval(secs => :stale_seconds)",
        ]
        params: Dict[str, Any] = {"message": message[:500], "stale_seconds": stale_seconds}
        if export_id is not None:
            clauses.append("id = :id")
            params["id"] = export_id
        if clip_id is not None:
            clauses.append("clip_id = :clip_id")
            params["clip_id"] = clip_id
        if options_hash is not None:
            clauses.append("options_hash = :options_hash")
            params["options_hash"] = options_hash
        result = await db.execute(
            text(
                f"""
                UPDATE clip_exports
                SET status = 'failed', error_message = :message, updated_at = NOW()
                WHERE {' AND '.join(clauses)}
                """
            ),
            params,
        )
        return result.rowcount or 0

    @staticmethod
    async def mark_running(db: AsyncSession, export_id: str) -> bool:
        result = await db.execute(
            text(
                """
                UPDATE clip_exports
                SET status = 'running',
                    progress = GREATEST(progress, 1),
                    updated_at = NOW()
                WHERE id = :id AND status IN ('queued', 'running')
                RETURNING id
                """
            ),
            {"id": export_id},
        )
        return result.first() is not None

    @staticmethod
    async def update_progress(db: AsyncSession, export_id: str, progress: int) -> None:
        await db.execute(
            text(
                """
                UPDATE clip_exports
                SET progress = :progress, updated_at = NOW()
                WHERE id = :id AND status = 'running' AND :progress > progress AND :progress < 100
                """
            ),
            {"id": export_id, "progress": progress},
        )

    @staticmethod
    async def touch(db: AsyncSession, export_id: str) -> None:
        await db.execute(
            text(
                """
                UPDATE clip_exports SET updated_at = NOW()
                WHERE id = :id AND status = 'running'
                """
            ),
            {"id": export_id},
        )

    @staticmethod
    async def mark_succeeded(db: AsyncSession, export_id: str, file_path: str) -> bool:
        result = await db.execute(
            text(
                """
                UPDATE clip_exports
                SET status = 'succeeded', progress = 100, file_path = :file_path,
                    error_message = NULL, updated_at = NOW()
                WHERE id = :id AND status = 'running'
                RETURNING id
                """
            ),
            {"id": export_id, "file_path": file_path},
        )
        return result.first() is not None

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        export_id: str,
        message: str,
        *,
        statuses: tuple[str, ...] = ACTIVE_STATUSES,
    ) -> bool:
        allowed = {"queued", "running", "succeeded", "failed"}
        if not statuses or not set(statuses) <= allowed:
            raise ValueError("Invalid clip export status filter")
        status_sql = ", ".join(f"'{status}'" for status in statuses)
        result = await db.execute(
            text(
                f"""
                UPDATE clip_exports
                SET status = 'failed', error_message = :message, updated_at = NOW()
                WHERE id = :id AND status IN ({status_sql})
                RETURNING id
                """
            ),
            {"id": export_id, "message": message[:500]},
        )
        return result.first() is not None

    @staticmethod
    async def delete(db: AsyncSession, export_id: str) -> None:
        await db.execute(
            text("DELETE FROM clip_exports WHERE id = :id"),
            {"id": export_id},
        )

    @staticmethod
    async def lock_expired(db: AsyncSession, *, limit: int) -> list[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT id, file_path FROM clip_exports
                WHERE status = 'succeeded' AND expires_at <= NOW()
                ORDER BY expires_at
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        return [dict(row) for row in result.mappings()]

    @staticmethod
    async def purge_failures(db: AsyncSession, *, days: int) -> int:
        result = await db.execute(
            text(
                """
                DELETE FROM clip_exports
                WHERE status = 'failed'
                  AND updated_at < NOW() - make_interval(days => :days)
                """
            ),
            {"days": days},
        )
        return result.rowcount or 0

    @staticmethod
    async def live_ids(db: AsyncSession) -> set[str]:
        result = await db.execute(
            text(
                """
                SELECT id FROM clip_exports
                WHERE status IN ('queued', 'running', 'succeeded')
                """
            )
        )
        return {row.id for row in result}

    @staticmethod
    async def queue_file_deletes(
        db: AsyncSession, *, clip_id: Optional[str] = None, task_id: Optional[str] = None
    ) -> None:
        if clip_id is not None:
            result = await db.execute(
                text(
                    """
                    SELECT file_path FROM clip_exports
                    WHERE clip_id = :clip_id AND file_path IS NOT NULL
                    """
                ),
                {"clip_id": clip_id},
            )
        elif task_id is not None:
            result = await db.execute(
                text(
                    """
                    SELECT file_path FROM clip_exports
                    WHERE task_id = :task_id AND file_path IS NOT NULL
                    """
                ),
                {"task_id": task_id},
            )
        else:
            return
        for row in result:
            if row.file_path:
                await queue_delete(db, row.file_path)
