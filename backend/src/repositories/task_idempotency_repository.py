"""Durable Idempotency-Key records for task creation.

A key names one create request for one user. Within the retention window a
repeat of that request returns the task it made instead of creating (and
charging) another; the same key with a different request is a conflict.
The record is written in the task's own transaction, so a key exists exactly
when its task does. Rows have no foreign key to ``tasks``: like the minute
ledger, deleting the task must not free the key for a late retry to charge
again.
"""

from __future__ import annotations

from sqlalchemy import text

# How long a key keeps answering with its task. Clients retry a lost
# response within minutes; a day leaves room for a device that was offline.
TASK_IDEMPOTENCY_RETENTION_HOURS = 24

_LIVE = f"created_at > NOW() - INTERVAL '{TASK_IDEMPOTENCY_RETENTION_HOURS} hours'"


class TaskIdempotencyConflict(ValueError):
    """The key was already used for a different create request."""


class TaskIdempotencyReplay(Exception):
    """A concurrent request with the same key created the task first."""


async def find_task_for_key(db, user_id: str, key: str, request_hash: str):
    """The task an earlier request with ``key`` created, or None.

    Raises ``TaskIdempotencyConflict`` when the key was used for a different
    request. ``job_id`` is None when the task has since been deleted.
    """
    row = (await db.execute(text(f"""
        SELECT k.task_id AS id, k.request_hash,
               t.source_metadata->>'processing_job_id' AS job_id
        FROM task_idempotency_keys k
        LEFT JOIN tasks t ON t.id = k.task_id
        WHERE k.user_id = :user AND k.idempotency_key = :key AND k.{_LIVE}
    """), {"user": user_id, "key": key})).first()
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise TaskIdempotencyConflict(
            "This Idempotency-Key was already used for a different request."
        )
    return row


async def claim_key(db, user_id: str, key: str, request_hash: str, task_id: str) -> bool:
    """Bind ``key`` to ``task_id`` inside the caller's open transaction.

    Returns False when a live record already holds the key. A concurrent
    insert of the same key waits on the primary key until the other
    transaction ends, so exactly one of them gets True. An expired record is
    replaced in place.
    """
    # Keys past retention can no longer answer; drop this user's.
    await db.execute(text(f"""
        DELETE FROM task_idempotency_keys
        WHERE user_id = :user AND idempotency_key <> :key AND NOT ({_LIVE})
    """), {"user": user_id, "key": key})
    claimed = (await db.execute(text(f"""
        INSERT INTO task_idempotency_keys (user_id, idempotency_key, request_hash, task_id, created_at)
        VALUES (:user, :key, :hash, :task, NOW())
        ON CONFLICT (user_id, idempotency_key) DO UPDATE
            SET request_hash = EXCLUDED.request_hash,
                task_id = EXCLUDED.task_id,
                created_at = EXCLUDED.created_at
            WHERE NOT (task_idempotency_keys.{_LIVE})
        RETURNING task_id
    """), {"user": user_id, "key": key, "hash": request_hash, "task": task_id})).first()
    return claimed is not None
