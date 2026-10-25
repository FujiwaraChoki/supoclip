"""Durable minute charges and failure refunds, independent of task deletion."""

import math
from uuid import uuid4

from sqlalchemy import text


def charged_duration(duration_seconds: float | None) -> int:
    """Persist whole seconds without rounding a started minute down."""
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
    ):
        raise ValueError("A verified positive source duration is required")
    return math.ceil(duration_seconds)


async def record_minute_usage(db, user_id: str, task_id: str, duration_seconds: float) -> None:
    """Insert once per task. Duplicate delivery must never undo a refund."""
    seconds = charged_duration(duration_seconds)
    await db.execute(
        text("""
            INSERT INTO minute_usage_events (
                id, user_id, task_id, minutes, source_duration_seconds, created_at
            ) VALUES (
                :id, :user_id, :task_id, :minutes, :seconds, CURRENT_TIMESTAMP
            )
            ON CONFLICT (task_id) DO NOTHING
        """),
        {
            "id": str(uuid4()), "user_id": user_id, "task_id": task_id,
            "minutes": (seconds + 59) // 60, "seconds": seconds,
        },
    )


async def refund_failed_minute_usage(db, task_id: str) -> None:
    """Refund only terminal failures without saved output, never task deletion.

    The caller holds the task row lock from the status UPDATE until commit, so
    clip saves (which also lock the task) cannot race the output check.
    """
    await db.execute(
        text("""
            UPDATE minute_usage_events SET refunded_at = CURRENT_TIMESTAMP
            WHERE task_id = :task_id AND refunded_at IS NULL
              AND EXISTS (
                  SELECT 1 FROM tasks t
                  WHERE t.id = :task_id AND t.user_id = minute_usage_events.user_id
                    AND t.status IN ('error', 'failed', 'cancelled', 'canceled')
                    AND NOT EXISTS (SELECT 1 FROM generated_clips c WHERE c.task_id = t.id)
              )
        """),
        {"task_id": task_id},
    )


async def reactivate_minute_usage(db, user_id: str, task_id: str) -> None:
    """Re-charge one refunded row after a locked allowance check.

    The new charge belongs to the retry's current billing period, even if the
    original attempt was refunded in an earlier period.
    """
    await db.execute(
        text("""
            UPDATE minute_usage_events
            SET refunded_at = NULL, created_at = CURRENT_TIMESTAMP
            WHERE task_id = :task_id AND user_id = :user_id AND refunded_at IS NOT NULL
        """),
        {"task_id": task_id, "user_id": user_id},
    )
