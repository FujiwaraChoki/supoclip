"""Agent conversations, transcripts, turns, background jobs and memories in PostgreSQL.

Every function opens its own short session: an agent turn runs for many
seconds and must never hold a pooled connection while it waits on the model.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from ..database import AsyncSessionLocal

ACTIVE_RUN_STATUSES = ("queued", "running")
OPEN_RUN_STATUSES = ("queued", "running", "awaiting_approval")
MAX_MEMORIES = 40


def new_id() -> str:
    return str(uuid.uuid4())


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# ----------------------------------------------------------------------
# Conversations
# ----------------------------------------------------------------------
def _scope(team_id: Optional[str]) -> str:
    return "team_id = :team_id" if team_id else "team_id IS NULL"


async def create_conversation(user_id: str, team_id: Optional[str], title: Optional[str]) -> dict:
    conversation_id = new_id()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO agent_conversations (id, user_id, team_id, title) "
                "VALUES (:id, :user_id, :team_id, :title)"
            ),
            {"id": conversation_id, "user_id": user_id, "team_id": team_id, "title": (title or None)},
        )
        await db.commit()
    return {"id": conversation_id, "title": title, "team_id": team_id}


async def get_conversation(conversation_id: str, user_id: str) -> Optional[dict]:
    """A conversation the person started. Conversations are private, also in a team."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT id, user_id, team_id, title, created_at, updated_at "
                    "FROM agent_conversations WHERE id = :id AND user_id = :user_id"
                ),
                {"id": conversation_id, "user_id": user_id},
            )
        ).fetchone()
    if not row:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "team_id": row.team_id,
        "title": row.title,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def list_conversations(user_id: str, team_id: Optional[str], limit: int = 30) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT c.id, c.title, c.created_at, c.updated_at,
                           (SELECT r.status FROM agent_runs r WHERE r.conversation_id = c.id
                            ORDER BY r.created_at DESC LIMIT 1) AS last_status
                    FROM agent_conversations c
                    WHERE c.user_id = :user_id AND c.{_scope(team_id)}
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"user_id": user_id, "team_id": team_id, "limit": max(1, min(limit, 100))},
            )
        ).fetchall()
    return [
        {
            "id": row.id,
            "title": row.title,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "status": row.last_status,
        }
        for row in rows
    ]


async def rename_conversation(conversation_id: str, user_id: str, title: str) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                "UPDATE agent_conversations SET title = :title WHERE id = :id AND user_id = :user_id RETURNING id"
            ),
            {"id": conversation_id, "user_id": user_id, "title": title[:200]},
        )
        await db.commit()
        return result.fetchone() is not None


async def set_title_if_missing(conversation_id: str, title: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE agent_conversations SET title = :title WHERE id = :id AND title IS NULL"),
            {"id": conversation_id, "title": title[:200]},
        )
        await db.commit()


async def delete_conversation(conversation_id: str, user_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("DELETE FROM agent_conversations WHERE id = :id AND user_id = :user_id RETURNING id"),
            {"id": conversation_id, "user_id": user_id},
        )
        await db.commit()
        return result.fetchone() is not None


async def touch_conversation(conversation_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE agent_conversations SET updated_at = NOW() WHERE id = :id"),
            {"id": conversation_id},
        )
        await db.commit()


# ----------------------------------------------------------------------
# Transcript
# ----------------------------------------------------------------------
async def append_message(
    conversation_id: str,
    role: str,
    content: list[dict],
    *,
    run_id: Optional[str] = None,
    display: Optional[dict] = None,
) -> int:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO agent_messages (conversation_id, run_id, role, content, display)
                    VALUES (:conversation_id, :run_id, :role, CAST(:content AS JSONB), CAST(:display AS JSONB))
                    RETURNING id
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "role": role,
                    "content": json.dumps(content),
                    "display": json.dumps(display) if display is not None else None,
                },
            )
        ).fetchone()
        await db.execute(
            text("UPDATE agent_conversations SET updated_at = NOW() WHERE id = :id"),
            {"id": conversation_id},
        )
        await db.commit()
    return int(row.id)


async def list_messages(conversation_id: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, run_id, role, content, display, created_at FROM agent_messages "
                    "WHERE conversation_id = :id ORDER BY id"
                ),
                {"id": conversation_id},
            )
        ).fetchall()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "role": row.role,
            "content": _json(row.content),
            "display": _json(row.display),
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


# ----------------------------------------------------------------------
# Runs (one agent turn each)
# ----------------------------------------------------------------------
def _run(row) -> dict:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "user_id": row.user_id,
        "status": row.status,
        "trigger": row.trigger,
        "pending": _json(row.pending),
        "error": row.error,
        "cancel_requested": bool(row.cancel_requested),
        "usage": _json(row.usage),
        "heartbeat_at": _iso(row.heartbeat_at),
        "created_at": _iso(row.created_at),
    }


_RUN_COLUMNS = (
    "id, conversation_id, user_id, status, trigger, pending, error, cancel_requested, usage, heartbeat_at, created_at"
)


async def create_run(conversation_id: str, user_id: str, trigger: str = "user") -> Optional[dict]:
    """A new turn, or None when this conversation already has one open.

    The partial unique check is done in one statement so two quick sends can't
    start two turns that would interleave in the transcript.
    """
    run_id = new_id()
    async with AsyncSessionLocal() as db:
        # NOT EXISTS alone doesn't see a concurrent uncommitted insert: serialize per conversation.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:conversation_id, 72611))"),
            {"conversation_id": conversation_id},
        )
        row = (
            await db.execute(
                text(
                    f"""
                    INSERT INTO agent_runs (id, conversation_id, user_id, status, trigger, heartbeat_at)
                    SELECT CAST(:id AS VARCHAR), CAST(:conversation_id AS VARCHAR), CAST(:user_id AS VARCHAR),
                           'queued', CAST(:trigger AS VARCHAR), NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM agent_runs
                        WHERE conversation_id = CAST(:conversation_id AS VARCHAR) AND status IN {OPEN_RUN_STATUSES}
                    )
                    RETURNING {_RUN_COLUMNS}
                    """
                ),
                {"id": run_id, "conversation_id": conversation_id, "user_id": user_id, "trigger": trigger},
            )
        ).fetchone()
        await db.commit()
    return _run(row) if row else None


async def get_run(run_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(text(f"SELECT {_RUN_COLUMNS} FROM agent_runs WHERE id = :id"), {"id": run_id})
        ).fetchone()
    return _run(row) if row else None


async def open_run(conversation_id: str) -> Optional[dict]:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    f"SELECT {_RUN_COLUMNS} FROM agent_runs WHERE conversation_id = :id "
                    f"AND status IN {OPEN_RUN_STATUSES} ORDER BY created_at DESC LIMIT 1"
                ),
                {"id": conversation_id},
            )
        ).fetchone()
    return _run(row) if row else None


async def update_run(run_id: str, **fields: Any) -> None:
    allowed = {"status", "pending", "error", "usage", "cancel_requested"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Cannot update agent run fields: {sorted(unknown)}")
    assignments = ["updated_at = NOW()", "heartbeat_at = NOW()"]
    params: dict[str, Any] = {"id": run_id}
    for key, value in fields.items():
        if key in {"pending", "usage"}:
            assignments.append(f"{key} = CAST(:{key} AS JSONB)")
            params[key] = json.dumps(value) if value is not None else None
        else:
            assignments.append(f"{key} = :{key}")
            params[key] = value
    async with AsyncSessionLocal() as db:
        await db.execute(text(f"UPDATE agent_runs SET {', '.join(assignments)} WHERE id = :id"), params)
        await db.commit()


async def claim_approval(run_id: str) -> Optional[dict]:
    """Move a turn waiting for approval back to running; None if someone else did first."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    f"""
                    UPDATE agent_runs SET status = 'running', heartbeat_at = NOW(), updated_at = NOW()
                    WHERE id = :id AND status = 'awaiting_approval'
                    RETURNING {_RUN_COLUMNS}
                    """
                ),
                {"id": run_id},
            )
        ).fetchone()
        await db.commit()
    return _run(row) if row else None


async def heartbeat(run_id: str) -> bool:
    """Keep the turn alive; returns whether a stop was requested."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "UPDATE agent_runs SET heartbeat_at = NOW() WHERE id = :id RETURNING cancel_requested"
                ),
                {"id": run_id},
            )
        ).fetchone()
        await db.commit()
    return bool(row and row.cancel_requested)


async def stale_runs(older_than_seconds: int) -> list[dict]:
    """Turns whose process stopped beating (a restart or a crash)."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    f"""
                    UPDATE agent_runs SET status = 'interrupted', updated_at = NOW(),
                        error = 'The agent was interrupted. Send your message again.'
                    WHERE status IN ('queued', 'running')
                      AND heartbeat_at < NOW() - make_interval(secs => :age)
                    RETURNING {_RUN_COLUMNS}
                    """
                ),
                {"age": older_than_seconds},
            )
        ).fetchall()
        await db.commit()
    return [_run(row) for row in rows]


async def turns_today(user_id: str) -> int:
    """User-started turns in the last 24 hours (follow-ups and approvals are free)."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT COUNT(*) AS n FROM agent_runs WHERE user_id = :user_id AND trigger = 'user' "
                    "AND created_at > NOW() - INTERVAL '24 hours'"
                ),
                {"user_id": user_id},
            )
        ).fetchone()
    return int(row.n if row else 0)


# ----------------------------------------------------------------------
# Background jobs
# ----------------------------------------------------------------------
def _job(row) -> dict:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "user_id": row.user_id,
        "team_id": row.team_id,
        "kind": row.kind,
        "status": row.status,
        "step": row.step,
        "task_id": row.task_id,
        "clip_id": row.clip_id,
        "payload": _json(row.payload) or {},
        "result": _json(row.result),
        "error": row.error,
        "attempts": row.attempts,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


_JOB_COLUMNS = (
    "id, conversation_id, user_id, team_id, kind, status, step, task_id, clip_id, payload, result, error, "
    "attempts, created_at, updated_at"
)


async def create_job(
    *,
    conversation_id: str,
    user_id: str,
    team_id: Optional[str],
    kind: str,
    task_id: Optional[str],
    clip_id: Optional[str],
    payload: dict,
    lifetime: timedelta = timedelta(days=1),
) -> dict:
    job_id = new_id()
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    f"""
                    INSERT INTO agent_jobs (id, conversation_id, user_id, team_id, kind, task_id, clip_id, payload,
                                            expires_at)
                    VALUES (:id, :conversation_id, :user_id, :team_id, :kind, :task_id, :clip_id,
                            CAST(:payload AS JSONB), NOW() + make_interval(secs => :lifetime))
                    RETURNING {_JOB_COLUMNS}
                    """
                ),
                {
                    "id": job_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "team_id": team_id,
                    "kind": kind,
                    "task_id": task_id,
                    "clip_id": clip_id,
                    "payload": json.dumps(payload),
                    "lifetime": int(lifetime.total_seconds()),
                },
            )
        ).fetchone()
        await db.commit()
    return _job(row)


async def claim_due_jobs(limit: int = 10, lease_seconds: int = 60) -> list[dict]:
    """Jobs due for a step, leased to this process so no other steps them meanwhile."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    f"""
                    UPDATE agent_jobs SET claimed_until = NOW() + make_interval(secs => :lease),
                        status = CASE WHEN status = 'pending' THEN 'running' ELSE status END
                    WHERE id IN (
                        SELECT id FROM agent_jobs
                        WHERE status IN ('pending', 'running') AND next_check_at <= NOW()
                          AND (claimed_until IS NULL OR claimed_until < NOW())
                        ORDER BY next_check_at
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING {_JOB_COLUMNS}, expires_at
                    """
                ),
                {"limit": limit, "lease": lease_seconds},
            )
        ).fetchall()
        await db.commit()
    jobs = []
    for row in rows:
        job = _job(row)
        expires = row.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        job["expired"] = bool(expires and expires < datetime.now(timezone.utc))
        jobs.append(job)
    return jobs


async def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    step: Optional[str] = None,
    payload: Optional[dict] = None,
    result: Optional[dict] = None,
    error: Optional[str] = None,
    delay_seconds: Optional[float] = None,
    bump_attempts: bool = False,
) -> None:
    assignments = ["updated_at = NOW()", "claimed_until = NULL"]
    params: dict[str, Any] = {"id": job_id}
    if status is not None:
        assignments.append("status = :status")
        params["status"] = status
    if step is not None:
        assignments.append("step = :step")
        params["step"] = step
    if payload is not None:
        assignments.append("payload = CAST(:payload AS JSONB)")
        params["payload"] = json.dumps(payload)
    if result is not None:
        assignments.append("result = CAST(:result AS JSONB)")
        params["result"] = json.dumps(result)
    if error is not None:
        assignments.append("error = :error")
        params["error"] = error[:1000]
    if delay_seconds is not None:
        assignments.append("next_check_at = NOW() + make_interval(secs => :delay)")
        params["delay"] = float(delay_seconds)
    if bump_attempts:
        assignments.append("attempts = attempts + 1")
    async with AsyncSessionLocal() as db:
        await db.execute(text(f"UPDATE agent_jobs SET {', '.join(assignments)} WHERE id = :id"), params)
        await db.commit()


async def list_jobs(conversation_id: str, job_ids: Optional[list[str]] = None, limit: int = 30) -> list[dict]:
    params: dict[str, Any] = {"conversation_id": conversation_id, "limit": limit}
    clause = ""
    if job_ids:
        clause = "AND id = ANY(:ids)"
        params["ids"] = job_ids
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    f"SELECT {_JOB_COLUMNS} FROM agent_jobs WHERE conversation_id = :conversation_id {clause} "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                params,
            )
        ).fetchall()
    return [_job(row) for row in rows]


async def cancel_job(job_id: str, user_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "UPDATE agent_jobs SET status = 'cancelled', updated_at = NOW() "
                    "WHERE id = :id AND user_id = :user_id AND status IN ('pending', 'running') RETURNING id"
                ),
                {"id": job_id, "user_id": user_id},
            )
        ).fetchone()
        await db.commit()
    return row is not None


# ----------------------------------------------------------------------
# Memories
# ----------------------------------------------------------------------
async def list_memories(user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, note, created_at FROM agent_memories WHERE user_id = :user_id ORDER BY created_at"
                ),
                {"user_id": user_id},
            )
        ).fetchall()
    return [{"id": row.id, "note": row.note, "created_at": _iso(row.created_at)} for row in rows]


async def add_memory(user_id: str, note: str) -> Optional[dict]:
    async with AsyncSessionLocal() as db:
        count = (
            await db.execute(
                text("SELECT COUNT(*) AS n FROM agent_memories WHERE user_id = :user_id"), {"user_id": user_id}
            )
        ).fetchone()
        if count and count.n >= MAX_MEMORIES:
            return None
        memory_id = new_id()
        await db.execute(
            text("INSERT INTO agent_memories (id, user_id, note) VALUES (:id, :user_id, :note)"),
            {"id": memory_id, "user_id": user_id, "note": note[:400]},
        )
        await db.commit()
    return {"id": memory_id, "note": note[:400]}


async def delete_memory(user_id: str, memory_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text("DELETE FROM agent_memories WHERE id = :id AND user_id = :user_id RETURNING id"),
                {"id": memory_id, "user_id": user_id},
            )
        ).fetchone()
        await db.commit()
    return row is not None
