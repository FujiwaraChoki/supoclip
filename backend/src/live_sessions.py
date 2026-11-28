"""Continuous live clipping: a live session adds a part task while the stream lasts.

``POST /tasks/`` with ``source.live.follow`` makes the first part (the newest
``window_seconds``) and a ``live_sessions`` row. The cron worker runs
``advance_live_sessions`` every minute. For each due session it checks the
stream (metadata only) and, once ``LIVE_FOLLOW_INTERVAL_SECONDS`` of new stream
exist, creates the next part through the same code as ``POST /tasks/``: same
options, billed for the new minutes only, with an idempotency key per part so a
retried cron run never creates a part twice. After the stream ends, one last
part covers the rest of the recording.

Nothing records a stream on our side, and nothing long-running holds a worker:
each part is an ordinary task (clips, editor, publishing, refunds all apply).
A session stops when the person stops it, the stream ends, the account runs out
of minutes or loses the plan, ``LIVE_FOLLOW_MAX_HOURS`` pass, or the stream
can't be reached for a while.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_config
from .live_source import LiveSourceError, LiveStream, resolve_live_stream, resolve_recording_length
from .utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

SESSIONS_PER_RUN = 20
CLAIM_MINUTES = 10
RETRY_SECONDS = 300
MAX_CONSECUTIVE_FAILURES = 6
# A part shorter than this after the stream ends isn't worth a task.
MIN_FINAL_PART_SECONDS = 120.0
# How long after the stream ends we wait for its finished recording.
FINAL_PART_WAIT = timedelta(hours=2)
# Options from the first request that every part reuses.
PART_OPTION_KEYS = (
    "font_options", "caption_template", "include_broll", "processing_mode", "output_format",
    "add_subtitles", "clip_length", "cut_long_pauses", "pause_threshold_ms", "remove_filler_words",
    "filtered_words", "edit_style", "instructions", "workflow", "workflow_options",
)
ACTIVE = "active"
PUBLIC_COLUMNS = (
    "id, user_id, team_id, platform, channel_url, channel, title, recording_id, recording_url, status, "
    "stop_reason, covered_until_seconds, parts_created, last_error, next_check_at, stream_started_at, "
    "last_checked_at, ended_at, created_at"
)


@dataclass(frozen=True)
class LivePart:
    """A part the cron is adding to a session. Never read from a request body."""

    session_id: str
    part_number: int
    stream: LiveStream
    from_seconds: float


def part_options(data: dict) -> dict:
    return {key: data[key] for key in PART_OPTION_KEYS if key in data}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(seconds: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(seconds, timezone.utc) if seconds else None


# ------------------------------------------------------------------ access

async def live_clipping_allowed(db: AsyncSession, user_id: str, team_id: Optional[str]) -> tuple[bool, dict]:
    """``(allowed, billing_summary)``: Creator/Team (paid), self-host, or an admin."""
    from .admin_auth import user_is_admin
    from .services.billing_service import BillingService

    service = BillingService(db)
    summary = (
        await service.get_usage_summary(user_id, team_id=team_id) if team_id
        else await service.get_usage_summary(user_id)
    )
    if summary.get("can_clip_live"):
        return True, summary
    return await user_is_admin(db, user_id), summary


# -------------------------------------------------------------- repository

async def find_active_session(
    db: AsyncSession, user_id: str, team_id: Optional[str], platform: str, recording_id: str,
):
    return (await db.execute(
        text(
            f"SELECT {PUBLIC_COLUMNS} FROM live_sessions WHERE user_id = :user_id "
            "AND COALESCE(team_id, '') = COALESCE(:team_id, '') AND platform = :platform "
            "AND recording_id = :recording_id AND status = 'active' LIMIT 1"
        ),
        {"user_id": user_id, "team_id": team_id, "platform": platform, "recording_id": recording_id},
    )).fetchone()


async def start_session(
    db: AsyncSession, *, user_id: str, team_id: Optional[str], stream: LiveStream,
    covered_until: float, options: dict, first_task_id: str, first_start: float,
) -> str:
    """Store a session and its first part. Returns the session id (an existing one if racing)."""
    import json

    session_id = str(uuid.uuid4())
    interval = get_config().live_follow_interval_seconds
    try:
        await db.execute(
            text(
                """
                INSERT INTO live_sessions (
                    id, user_id, team_id, platform, channel_url, channel, title, recording_id,
                    recording_url, options, covered_until_seconds, parts_created, next_check_at,
                    stream_started_at, last_checked_at
                ) VALUES (
                    :id, :user_id, :team_id, :platform, :channel_url, :channel, :title, :recording_id,
                    :recording_url, CAST(:options AS JSONB), :covered, 1, :next_check_at,
                    :started_at, NOW()
                )
                """
            ),
            {
                "id": session_id, "user_id": user_id, "team_id": team_id, "platform": stream.platform,
                "channel_url": stream.channel_url, "channel": (stream.channel or "")[:200] or None,
                "title": stream.title, "recording_id": stream.recording_id,
                "recording_url": stream.recording_url, "options": json.dumps(options),
                "covered": float(covered_until), "next_check_at": _now() + timedelta(seconds=interval),
                "started_at": _stamp(stream.started_at),
            },
        )
        await _insert_part(db, session_id, 1, first_task_id, first_start, covered_until)
        await db.commit()
    except Exception:
        await db.rollback()
        existing = await find_active_session(db, user_id, team_id, stream.platform, stream.recording_id)
        if existing is None:
            raise
        return str(existing.id)
    return session_id


async def _insert_part(db, session_id: str, number: int, task_id: Optional[str], start: float, end: float) -> None:
    await db.execute(
        text(
            "INSERT INTO live_session_parts (session_id, part_number, task_id, start_seconds, end_seconds) "
            "VALUES (:session_id, :number, :task_id, :start, :end) ON CONFLICT (session_id, part_number) DO NOTHING"
        ),
        {"session_id": session_id, "number": number, "task_id": task_id, "start": float(start), "end": float(end)},
    )


async def load_session(db: AsyncSession, session_id: str, user_id: str, team_id: Optional[str]):
    return (await db.execute(
        text(
            f"SELECT {PUBLIC_COLUMNS} FROM live_sessions WHERE id = :id AND user_id = :user_id "
            "AND COALESCE(team_id, '') = COALESCE(:team_id, '')"
        ),
        {"id": session_id, "user_id": user_id, "team_id": team_id},
    )).fetchone()


async def session_for_task(db: AsyncSession, task_id: str):
    return (await db.execute(
        text(
            f"SELECT s.{PUBLIC_COLUMNS.replace(', ', ', s.')}, p.part_number FROM live_session_parts p "
            "JOIN live_sessions s ON s.id = p.session_id WHERE p.task_id = :task_id LIMIT 1"
        ),
        {"task_id": task_id},
    )).fetchone()


async def list_sessions(db: AsyncSession, user_id: str, team_id: Optional[str], limit: int = 20) -> list:
    return list((await db.execute(
        text(
            f"SELECT {PUBLIC_COLUMNS} FROM live_sessions WHERE user_id = :user_id "
            "AND COALESCE(team_id, '') = COALESCE(:team_id, '') ORDER BY created_at DESC LIMIT :limit"
        ),
        {"user_id": user_id, "team_id": team_id, "limit": limit},
    )).fetchall())


async def session_parts(db: AsyncSession, session_id: str) -> list[dict]:
    rows = (await db.execute(
        text(
            """
            SELECT p.part_number, p.task_id, p.start_seconds, p.end_seconds, p.created_at,
                   t.status AS task_status,
                   (SELECT COUNT(*) FROM generated_clips c WHERE c.task_id = p.task_id) AS clips_count
            FROM live_session_parts p LEFT JOIN tasks t ON t.id = p.task_id
            WHERE p.session_id = :session_id ORDER BY p.part_number
            """
        ),
        {"session_id": session_id},
    )).fetchall()
    return [
        {
            "part_number": row.part_number,
            "task_id": row.task_id,
            "start_seconds": float(row.start_seconds),
            "end_seconds": float(row.end_seconds),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "task_status": row.task_status,
            "clips_count": int(row.clips_count or 0),
        }
        for row in rows
    ]


def session_public(row, parts: Optional[list[dict]] = None) -> dict:
    def iso(value):
        return value.isoformat() if value else None

    payload = {
        "id": str(row.id),
        "platform": row.platform,
        "channel": row.channel,
        "channel_url": row.channel_url,
        "title": row.title,
        "status": row.status,
        "stop_reason": row.stop_reason,
        "covered_until_seconds": float(row.covered_until_seconds or 0),
        "parts_created": int(row.parts_created or 0),
        "next_check_at": iso(row.next_check_at) if row.status == ACTIVE else None,
        "stream_started_at": iso(row.stream_started_at),
        "last_checked_at": iso(row.last_checked_at),
        "ended_at": iso(row.ended_at),
        "created_at": iso(row.created_at),
        "interval_seconds": get_config().live_follow_interval_seconds,
    }
    if parts is not None:
        payload["parts"] = parts
    return payload


async def stop_session(db: AsyncSession, session_id: str, reason: str, *, status: str = "stopped",
                       error: Optional[str] = None) -> None:
    await db.execute(
        text(
            "UPDATE live_sessions SET status = :status, stop_reason = :reason, last_error = COALESCE(:error, last_error), "
            "ended_at = COALESCE(ended_at, NOW()), claimed_until = NULL, updated_at = NOW() "
            "WHERE id = :id AND status = 'active'"
        ),
        {"id": session_id, "status": status, "reason": reason, "error": error},
    )
    await db.commit()


# -------------------------------------------------------------------- cron

class _InternalRequest:
    """What ``_create_task`` reads from a request, for a part the cron adds.

    The idempotency key makes a retried run return the part it already made.
    """

    def __init__(self, body: dict, idempotency_key: str):
        from .workers.job_queue import JobQueue

        self._body = body
        self.headers = {"idempotency-key": idempotency_key, "user-agent": "supoclip-live-session"}
        self.app = SimpleNamespace(state=SimpleNamespace(queue_adapter=JobQueue))

    async def json(self) -> dict:
        return self._body


class _NoAttempt:
    def __getattr__(self, _name):
        def ignore(*_args, **_kwargs):
            return None
        return ignore


async def _claim_due(db: AsyncSession) -> list:
    rows = (await db.execute(
        text(
            f"""
            UPDATE live_sessions SET claimed_until = NOW() + INTERVAL '{CLAIM_MINUTES} minutes'
            WHERE id IN (
                SELECT id FROM live_sessions
                WHERE status = 'active' AND next_check_at <= NOW()
                  AND (claimed_until IS NULL OR claimed_until < NOW())
                ORDER BY next_check_at LIMIT {SESSIONS_PER_RUN}
                FOR UPDATE SKIP LOCKED
            )
            RETURNING {PUBLIC_COLUMNS}, options, consecutive_failures
            """
        )
    )).fetchall()
    await db.commit()
    return list(rows)


async def _reschedule(db: AsyncSession, session_id: str, seconds: float, **fields: Any) -> None:
    sets = ["next_check_at = NOW() + make_interval(secs => :seconds)", "claimed_until = NULL",
            "last_checked_at = NOW()", "updated_at = NOW()"]
    params: dict[str, Any] = {"id": session_id, "seconds": max(60.0, float(seconds))}
    for key, value in fields.items():
        sets.append(f"{key} = :{key}")
        params[key] = value
    await db.execute(text(f"UPDATE live_sessions SET {', '.join(sets)} WHERE id = :id"), params)
    await db.commit()


async def _create_part(db: AsyncSession, session, part: LivePart) -> dict:
    """Create the part's task through ``POST /tasks/``'s own code."""
    from .api.routes.tasks import _create_task
    from .auth_headers import AuthContext

    title = session.title or f"{session.channel or session.platform.title()} live"
    options = session.options
    if isinstance(options, str):
        import json

        options = json.loads(options or "{}")
    body = {**(options or {}), "source": {"url": session.recording_url, "title": f"{title} · Part {part.part_number}"}}
    request = _InternalRequest(body, f"live-{session.id}-{part.part_number}")
    return await _create_task(
        request, db, _NoAttempt(),
        auth_ctx=AuthContext(user_id=str(session.user_id), team_id=session.team_id),
        live_part=part,
    )


async def advance_session(db: AsyncSession, session) -> str:
    """One step for one claimed session. Returns what happened (for the cron's summary)."""
    from fastapi import HTTPException

    config = get_config()
    interval = float(config.live_follow_interval_seconds)
    covered = float(session.covered_until_seconds or 0)
    from . import feature_flags

    if not await feature_flags.is_enabled(db, feature_flags.LIVE_CLIPPING, platform="web", user_id=str(session.user_id)):
        await stop_session(db, str(session.id), "disabled")
        return "stopped_disabled"
    allowed, _summary = await live_clipping_allowed(db, str(session.user_id), session.team_id)
    if not allowed:
        await stop_session(db, str(session.id), "plan")
        return "stopped_plan"
    if covered >= config.live_follow_max_hours * 3600:
        await stop_session(db, str(session.id), "max_hours")
        return "stopped_max_hours"

    ended = False
    stream: Optional[LiveStream] = None
    try:
        stream = await run_in_thread(resolve_live_stream, session.channel_url)
        if stream.recording_id != session.recording_id:
            # The channel started a new broadcast: this session's recording is over.
            ended, stream = True, None
    except LiveSourceError as exc:
        if exc.code != "STREAM_NOT_LIVE":
            return await _note_failure(db, session, exc.message)
        ended = True

    if ended:
        return await _finish_session(db, session, covered)

    new_seconds = stream.edge_seconds - covered
    if new_seconds < interval:
        await _reschedule(db, str(session.id), interval - new_seconds, consecutive_failures=0)
        return "waiting"
    number = int(session.parts_created or 0) + 1
    try:
        response = await _create_part(db, session, LivePart(str(session.id), number, stream, covered))
    except HTTPException as exc:
        return await _part_refused(db, session, exc)
    await _record_part(db, session, number, response, covered)
    return "part_created"


async def _record_part(db: AsyncSession, session, number: int, response: dict, covered: float) -> None:
    live = response.get("live") or {}
    end = float(live.get("end_seconds") or covered)
    await _insert_part(db, str(session.id), number, response.get("task_id"), float(live.get("start_seconds") or covered), end)
    await db.execute(
        text(
            "UPDATE live_sessions SET parts_created = GREATEST(parts_created, :number), "
            "covered_until_seconds = GREATEST(covered_until_seconds, :end), consecutive_failures = 0, "
            "last_error = NULL, next_check_at = NOW() + make_interval(secs => :interval), claimed_until = NULL, "
            "last_checked_at = NOW(), updated_at = NOW() WHERE id = :id"
        ),
        {"id": str(session.id), "number": number, "end": end,
         "interval": float(get_config().live_follow_interval_seconds)},
    )
    await db.commit()


async def _finish_session(db: AsyncSession, session, covered: float) -> str:
    """The stream ended: one last part for the rest of the recording, then done."""
    from fastapi import HTTPException

    try:
        length = await run_in_thread(resolve_recording_length, session.platform, session.recording_url, session.recording_id)
    except Exception as exc:  # noqa: BLE001 - the recording may still be processing
        logger.info("live_session_final_length_unavailable session=%s error=%s", session.id, exc)
        length = None
    if length is None:
        if session.ended_at and _now() - session.ended_at > FINAL_PART_WAIT:
            await stop_session(db, str(session.id), "ended", status="ended")
            return "ended"
        await db.execute(
            text("UPDATE live_sessions SET ended_at = COALESCE(ended_at, NOW()) WHERE id = :id"),
            {"id": str(session.id)},
        )
        await _reschedule(db, str(session.id), RETRY_SECONDS)
        return "waiting_for_recording"
    if length - covered >= MIN_FINAL_PART_SECONDS:
        stream = LiveStream(
            platform=session.platform, channel_url=session.channel_url, channel=session.channel or "",
            recording_url=session.recording_url, recording_id=session.recording_id,
            recorded_seconds=length, title=session.title, final=True,
        )
        number = int(session.parts_created or 0) + 1
        try:
            response = await _create_part(db, session, LivePart(str(session.id), number, stream, covered))
            await _record_part(db, session, number, response, covered)
        except HTTPException as exc:
            await _part_refused(db, session, exc, final=True)
            return "ended"
    await stop_session(db, str(session.id), "ended", status="ended")
    return "ended"


async def _part_refused(db: AsyncSession, session, exc, *, final: bool = False) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    code = detail.get("code")
    message = str(detail.get("message") or detail)[:500]
    if exc.status_code == 402 or code == "SUBSCRIPTION_REQUIRED":
        await stop_session(db, str(session.id), "minutes", error=message)
        return "stopped_minutes"
    if code == "LIVE_TOO_SHORT" and not final:
        await _reschedule(db, str(session.id), 120)
        return "waiting"
    if exc.status_code in (409, 503) or (exc.status_code >= 500):
        return await _note_failure(db, session, message)
    await stop_session(db, str(session.id), "error", status="failed", error=message)
    return "failed"


async def _note_failure(db: AsyncSession, session, message: str) -> str:
    failures = int(getattr(session, "consecutive_failures", 0) or 0) + 1
    if failures >= MAX_CONSECUTIVE_FAILURES:
        await stop_session(db, str(session.id), "errors", status="failed", error=message[:500])
        return "failed"
    await _reschedule(db, str(session.id), RETRY_SECONDS, consecutive_failures=failures, last_error=message[:500])
    return "retry"


async def advance_live_sessions(session_maker=None) -> dict:
    """Cron entry: step every due session once."""
    from .database import AsyncSessionLocal

    maker = session_maker or AsyncSessionLocal
    async with maker() as db:
        due = await _claim_due(db)
    outcomes: dict[str, int] = {}
    for session in due:
        async with maker() as db:
            try:
                outcome = await advance_session(db, session)
            except Exception as exc:  # noqa: BLE001 - one stream must not stop the others
                logger.exception("Live session %s step failed", session.id)
                await db.rollback()
                outcome = await _note_failure(db, session, f"{type(exc).__name__}")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {"due": len(due), **outcomes}
