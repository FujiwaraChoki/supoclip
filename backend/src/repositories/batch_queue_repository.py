"""Repository for the batch processing queue (batch_queues/batch_queue_items)."""

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _queue_row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "template_id": row.template_id,
        "status": row.status,
        "auto_export_to_source": row.auto_export_to_source,
        "delay_between_items_seconds": row.delay_between_items_seconds,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _item_row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "batch_queue_id": row.batch_queue_id,
        "item_order": row.item_order,
        "source_filename": row.source_filename,
        "source_path": row.source_path,
        "task_id": row.task_id,
        "status": row.status,
        "progress_percent": row.progress_percent,
        "current_stage": row.current_stage,
        "error_message": row.error_message,
        "retry_count": row.retry_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class BatchQueueRepository:
    @staticmethod
    async def create_queue(
        db: AsyncSession,
        queue_id: str,
        user_id: str,
        *,
        template_id: Optional[str],
        auto_export_to_source: bool,
        delay_between_items_seconds: int,
    ) -> Dict[str, Any]:
        await db.execute(
            text(
                """
                INSERT INTO batch_queues
                    (id, user_id, template_id, auto_export_to_source, delay_between_items_seconds)
                VALUES (:id, :user_id, :template_id, :auto_export, :delay)
                """
            ),
            {
                "id": queue_id,
                "user_id": user_id,
                "template_id": template_id,
                "auto_export": auto_export_to_source,
                "delay": delay_between_items_seconds,
            },
        )
        await db.commit()
        return await BatchQueueRepository.get_queue(db, queue_id)

    @staticmethod
    async def create_items(
        db: AsyncSession, queue_id: str, items: List[Dict[str, Any]]
    ) -> None:
        for idx, item in enumerate(items):
            await db.execute(
                text(
                    """
                    INSERT INTO batch_queue_items
                        (id, batch_queue_id, item_order, source_filename, source_path)
                    VALUES (:id, :batch_queue_id, :item_order, :source_filename, :source_path)
                    """
                ),
                {
                    "id": item["id"],
                    "batch_queue_id": queue_id,
                    "item_order": idx,
                    "source_filename": item["source_filename"],
                    "source_path": item.get("source_path"),
                },
            )
        await db.commit()

    @staticmethod
    async def get_queue(db: AsyncSession, queue_id: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text("SELECT * FROM batch_queues WHERE id = :id"), {"id": queue_id}
        )
        row = result.fetchone()
        return _queue_row_to_dict(row) if row else None

    @staticmethod
    async def get_items(db: AsyncSession, queue_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                "SELECT * FROM batch_queue_items WHERE batch_queue_id = :queue_id ORDER BY item_order ASC"
            ),
            {"queue_id": queue_id},
        )
        return [_item_row_to_dict(row) for row in result.fetchall()]

    @staticmethod
    async def list_queues_for_user(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                "SELECT * FROM batch_queues WHERE user_id = :user_id ORDER BY created_at DESC"
            ),
            {"user_id": user_id},
        )
        return [_queue_row_to_dict(row) for row in result.fetchall()]

    @staticmethod
    async def get_incomplete_queues_for_user(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT * FROM batch_queues
                WHERE user_id = :user_id AND status IN ('queued', 'running', 'paused')
                ORDER BY created_at DESC
                """
            ),
            {"user_id": user_id},
        )
        return [_queue_row_to_dict(row) for row in result.fetchall()]

    @staticmethod
    async def update_queue_status(
        db: AsyncSession,
        queue_id: str,
        status: str,
        *,
        started_at: bool = False,
        completed_at: bool = False,
    ) -> None:
        sets = ["status = :status", "updated_at = NOW()"]
        if started_at:
            sets.append("started_at = COALESCE(started_at, NOW())")
        if completed_at:
            sets.append("completed_at = NOW()")
        await db.execute(
            text(f"UPDATE batch_queues SET {', '.join(sets)} WHERE id = :id"),
            {"id": queue_id, "status": status},
        )
        await db.commit()

    @staticmethod
    async def update_item_status(
        db: AsyncSession,
        item_id: str,
        *,
        status: Optional[str] = None,
        progress_percent: Optional[int] = None,
        current_stage: Optional[str] = None,
        error_message: Optional[str] = None,
        task_id: Optional[str] = None,
        increment_retry: bool = False,
    ) -> None:
        sets: List[str] = ["updated_at = NOW()"]
        params: Dict[str, Any] = {"id": item_id}
        if status is not None:
            sets.append("status = :status")
            params["status"] = status
        if progress_percent is not None:
            sets.append("progress_percent = :progress_percent")
            params["progress_percent"] = progress_percent
        if current_stage is not None:
            sets.append("current_stage = :current_stage")
            params["current_stage"] = current_stage
        if error_message is not None:
            sets.append("error_message = :error_message")
            params["error_message"] = error_message
        if task_id is not None:
            sets.append("task_id = :task_id")
            params["task_id"] = task_id
        if increment_retry:
            sets.append("retry_count = retry_count + 1")
        await db.execute(
            text(f"UPDATE batch_queue_items SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        await db.commit()

    @staticmethod
    async def mark_items_skipped_if_error(db: AsyncSession, queue_id: str) -> None:
        """Used by "resume, skipping failed" — converts any errored item to
        skipped so a resume walks past it instead of retrying automatically."""
        await db.execute(
            text(
                """
                UPDATE batch_queue_items
                SET status = 'skipped', updated_at = NOW()
                WHERE batch_queue_id = :queue_id AND status = 'error'
                """
            ),
            {"queue_id": queue_id},
        )
        await db.commit()
