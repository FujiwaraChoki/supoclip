"""
Ranking repository - handles ranking_inputs rows (the N source videos a
ranking project holds), distinct from generated_clips because clipping's
clip rows assume a timestamp-bounded cut of one source; a ranking input is
a whole standalone file with its own place in an order/rank.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class RankingRepository:
    """Repository for ranking-input database operations."""

    @staticmethod
    async def add_input(
        db: AsyncSession,
        task_id: str,
        file_path: str,
        original_filename: str,
        duration_seconds: Optional[float],
        order_index: int,
        thumbnail_path: Optional[str] = None,
        folder_clip_id: Optional[str] = None,
        rank_text: Optional[str] = None,
        framing: str = "blur_fill",
    ) -> str:
        """Attach one input video to a ranking task and return its ID."""
        input_id = str(uuid4())
        await db.execute(
            text("""
                INSERT INTO ranking_inputs (
                    id, task_id, file_path, original_filename, duration_seconds,
                    order_index, thumbnail_path, folder_clip_id, rank_text, framing, created_at
                )
                VALUES (
                    :input_id, :task_id, :file_path, :original_filename, :duration_seconds,
                    :order_index, :thumbnail_path, :folder_clip_id, :rank_text, :framing, NOW()
                )
            """),
            {
                "input_id": input_id,
                "task_id": task_id,
                "file_path": file_path,
                "original_filename": original_filename,
                "duration_seconds": duration_seconds,
                "order_index": order_index,
                "thumbnail_path": thumbnail_path,
                "folder_clip_id": folder_clip_id,
                "rank_text": rank_text,
                "framing": framing,
            },
        )
        await db.commit()
        return input_id

    @staticmethod
    async def list_inputs(db: AsyncSession, task_id: str) -> List[Dict[str, Any]]:
        """Ordered inputs for a ranking task — manual `rank_position` wins over
        the drag-order `order_index` when set, per the spec's "auto-number
        from order, OR manual rank assignment" requirement.

        `order_index` is 0-based and `rank_position` is a 1-based user-facing
        number, so a plain `COALESCE(rank_position, order_index)` compares
        the two on mismatched scales and can put an un-ranked item ahead of a
        manually-ranked one it shouldn't be. Instead, every input with an
        explicit `rank_position` sorts first (by that value), and only the
        remaining un-ranked inputs fall back to drag order — a manual rank
        always wins outright rather than competing numerically.
        """
        result = await db.execute(
            text("""
                SELECT id, task_id, file_path, original_filename, duration_seconds,
                       order_index, rank_position, thumbnail_path, folder_clip_id,
                       rank_text, framing, created_at
                FROM ranking_inputs
                WHERE task_id = :task_id
                ORDER BY (rank_position IS NULL) ASC, rank_position ASC, order_index ASC
            """),
            {"task_id": task_id},
        )
        return [
            {
                "id": row.id,
                "task_id": row.task_id,
                "file_path": row.file_path,
                "original_filename": row.original_filename,
                "duration_seconds": row.duration_seconds,
                "order_index": row.order_index,
                "rank_position": row.rank_position,
                "thumbnail_path": row.thumbnail_path,
                "folder_clip_id": row.folder_clip_id,
                "rank_text": row.rank_text,
                "framing": row.framing,
                "created_at": row.created_at,
            }
            for row in result.fetchall()
        ]

    @staticmethod
    async def update_rank_text(
        db: AsyncSession, task_id: str, input_id: str, rank_text: Optional[str]
    ) -> None:
        await db.execute(
            text(
                "UPDATE ranking_inputs SET rank_text = :rank_text "
                "WHERE id = :input_id AND task_id = :task_id"
            ),
            {"rank_text": rank_text, "input_id": input_id, "task_id": task_id},
        )
        await db.commit()

    @staticmethod
    async def update_framing(
        db: AsyncSession, task_id: str, input_id: str, framing: str
    ) -> None:
        await db.execute(
            text(
                "UPDATE ranking_inputs SET framing = :framing "
                "WHERE id = :input_id AND task_id = :task_id"
            ),
            {"framing": framing, "input_id": input_id, "task_id": task_id},
        )
        await db.commit()

    @staticmethod
    async def update_order(
        db: AsyncSession, task_id: str, ordered_input_ids: List[str]
    ) -> None:
        """Rewrite `order_index` to match a new drag-and-drop order. Does not
        touch `rank_position` — a manual rank override stays put until the
        caller explicitly clears/changes it."""
        for index, input_id in enumerate(ordered_input_ids):
            await db.execute(
                text(
                    "UPDATE ranking_inputs SET order_index = :order_index "
                    "WHERE id = :input_id AND task_id = :task_id"
                ),
                {"order_index": index, "input_id": input_id, "task_id": task_id},
            )
        await db.commit()

    @staticmethod
    async def update_rank_position(
        db: AsyncSession, task_id: str, input_id: str, rank_position: Optional[int]
    ) -> None:
        """Set (or clear, with None) a manual rank override for one input."""
        await db.execute(
            text(
                "UPDATE ranking_inputs SET rank_position = :rank_position "
                "WHERE id = :input_id AND task_id = :task_id"
            ),
            {"rank_position": rank_position, "input_id": input_id, "task_id": task_id},
        )
        await db.commit()

    @staticmethod
    async def remove_input(db: AsyncSession, task_id: str, input_id: str) -> None:
        await db.execute(
            text("DELETE FROM ranking_inputs WHERE id = :input_id AND task_id = :task_id"),
            {"input_id": input_id, "task_id": task_id},
        )
        await db.commit()

    @staticmethod
    async def duplicate_inputs(db: AsyncSession, source_task_id: str, target_task_id: str) -> int:
        """Copy every input row from one task to another, preserving
        order_index/rank_position/thumbnail_path — used by project
        duplication (POST /ranking/tasks/{id}/duplicate). `file_path` points
        at an already-uploaded `upload://` reference, so this is a cheap
        row copy, not a re-upload."""
        inputs = await RankingRepository.list_inputs(db, source_task_id)
        for index, item in enumerate(inputs):
            new_id = str(uuid4())
            await db.execute(
                text("""
                    INSERT INTO ranking_inputs (
                        id, task_id, file_path, original_filename, duration_seconds,
                        order_index, rank_position, thumbnail_path, folder_clip_id,
                        rank_text, framing, created_at
                    )
                    VALUES (
                        :id, :task_id, :file_path, :original_filename, :duration_seconds,
                        :order_index, :rank_position, :thumbnail_path, :folder_clip_id,
                        :rank_text, :framing, NOW()
                    )
                """),
                {
                    "id": new_id,
                    "task_id": target_task_id,
                    "file_path": item["file_path"],
                    "original_filename": item["original_filename"],
                    "duration_seconds": item["duration_seconds"],
                    "order_index": index,
                    "rank_position": item["rank_position"],
                    "thumbnail_path": item["thumbnail_path"],
                    "folder_clip_id": item["folder_clip_id"],
                    "rank_text": item["rank_text"],
                    "framing": item["framing"],
                },
            )
        await db.commit()
        return len(inputs)
