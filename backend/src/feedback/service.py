"""Storing submissions and reading them back for the admin inbox.

``submit_feedback`` is the one way a submission is created (``POST /feedback``
from the web app and the iOS app using signed backend headers). The row, its cancellation note and
one outbox delivery per matching enabled webhook are written in one transaction,
so a webhook that is down never loses or fails a submission.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import CATEGORIES, STATUSES

RATE_LIMIT_COUNT = 10
RATE_LIMIT_WINDOW_MINUTES = 10
MAX_NOTE_LENGTH = 4000
PAGE_SIZE = 50


class FeedbackUserGone(Exception):
    pass


class FeedbackRateLimited(Exception):
    pass


@dataclass(frozen=True)
class SubmissionInput:
    category: str
    message: str
    user_id: str | None = None
    page_url: str | None = None
    platform: str = "web"
    user_agent: str | None = None
    app_version: str | None = None
    locale: str | None = None


def _clip(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


async def submit_feedback(db: AsyncSession, data: SubmissionInput) -> dict[str, Any]:
    if data.category not in CATEGORIES:
        raise ValueError(f"Unknown category {data.category!r}")
    message = data.message.strip()
    if not message:
        raise ValueError("Empty message")

    email = None
    if data.user_id:
        # Serialize count + insert, and hold the user row until commit so deletion
        # cannot race the email snapshot. All callers lock before reading the count.
        user = (await db.execute(
            text("SELECT email FROM users WHERE id = :user_id FOR NO KEY UPDATE"),
            {"user_id": data.user_id},
        )).mappings().first()
        if user is None:
            raise FeedbackUserGone()
        email = user["email"]
        recent = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM feedback_submissions WHERE user_id = :user_id "
                    "AND created_at > NOW() - make_interval(mins => :minutes)"
                ),
                {"user_id": data.user_id, "minutes": RATE_LIMIT_WINDOW_MINUTES},
            )
        ).scalar()
        if recent >= RATE_LIMIT_COUNT:
            raise FeedbackRateLimited()

    submission_id = str(uuid.uuid4())
    await db.execute(
        text(
            """
            INSERT INTO feedback_submissions
                (id, user_id, email, category, message, page_url, platform, user_agent, app_version, locale)
            VALUES (:id, :user_id, :email, :category, :message, :page_url, :platform, :user_agent, :app_version, :locale)
            """
        ),
        {
            "id": submission_id,
            # Authenticated submissions retain their owner until account deletion.
            "user_id": data.user_id if email is not None else None,
            "email": email,
            "category": data.category,
            "message": message,
            "page_url": _clip(data.page_url, 2000),
            "platform": data.platform if data.platform in ("web", "ios") else "web",
            "user_agent": _clip(data.user_agent, 500),
            "app_version": _clip(data.app_version, 50),
            "locale": _clip(data.locale, 20),
        },
    )
    if data.category == "cancellation" and data.user_id and email is not None:
        # Read by the cancellation review of this customer (docs/cancellation-reviews.md).
        await db.execute(
            text("INSERT INTO cancellation_notes (id, user_id, message) VALUES (:id, :user_id, :message)"),
            {"id": submission_id, "user_id": data.user_id, "message": message},
        )
    queued = await db.execute(
        text(
            """
            INSERT INTO feedback_webhook_deliveries (id, submission_id, webhook_id)
            SELECT uuid_generate_v4()::text, :submission_id, w.id
            FROM feedback_webhooks w
            WHERE w.enabled AND (:category = ANY(w.categories) OR w.kind = 'hermes')
            ORDER BY w.id FOR SHARE
            """
        ),
        {"submission_id": submission_id, "category": data.category},
    )
    await db.commit()
    return {"id": submission_id, "deliveries": queued.rowcount or 0}


def _filters(
    category: str | None, status: str | None, query: str | None,
    since: datetime | None, until: datetime | None,
) -> tuple[str, dict[str, Any]]:
    clauses, params = [], {}
    if category in CATEGORIES:
        clauses.append("s.category = :category")
        params["category"] = category
    if status in STATUSES:
        clauses.append("s.status = :status")
        params["status"] = status
    if since:
        clauses.append("s.created_at >= :since")
        params["since"] = since
    if until:
        clauses.append("s.created_at < :until")
        params["until"] = until
    if query:
        query = query.strip()
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(
            "(s.user_id = :exact OR s.id = :exact OR LOWER(s.email) LIKE :pattern "
            "OR LOWER(u.email) LIKE :pattern OR LOWER(u.name) LIKE :pattern)"
        )
        params["exact"] = query
        params["pattern"] = f"%{escaped.lower()}%"
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


async def list_submissions(
    db: AsyncSession, *, category: str | None = None, status: str | None = None, query: str | None = None,
    since: datetime | None = None, until: datetime | None = None, page: int = 1, page_size: int = PAGE_SIZE,
) -> dict[str, Any]:
    where, params = _filters(category, status, query, since, until)
    base = f"FROM feedback_submissions s LEFT JOIN users u ON u.id = s.user_id {where}"
    total = (await db.execute(text(f"SELECT COUNT(*) {base}"), params)).scalar()
    rows = (
        await db.execute(
            text(
                f"""
                SELECT s.id, s.user_id, s.email, u.name AS user_name, s.category, LEFT(s.message, 280) AS message,
                       s.status, s.platform, s.created_at,
                       (SELECT COUNT(*) FROM feedback_notes n WHERE n.submission_id = s.id) AS note_count,
                       (SELECT COUNT(*) FROM feedback_webhook_deliveries d
                         WHERE d.submission_id = s.id AND d.status = 'failed') AS failed_deliveries
                {base}
                ORDER BY s.created_at DESC, s.id
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": (max(page, 1) - 1) * page_size},
        )
    ).mappings().all()
    counts = (
        await db.execute(text("SELECT status, COUNT(*) AS n FROM feedback_submissions GROUP BY status"))
    ).mappings().all()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": max(page, 1),
        "page_size": page_size,
        "status_counts": {**{name: 0 for name in STATUSES}, **{row["status"]: row["n"] for row in counts}},
    }


async def get_submission(db: AsyncSession, submission_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                """
                SELECT s.*, u.name AS user_name, u.email AS user_current_email, u.plan AS user_plan,
                       u."createdAt" AS user_created_at, r.name AS resolved_by_name
                FROM feedback_submissions s
                LEFT JOIN users u ON u.id = s.user_id
                LEFT JOIN users r ON r.id = s.resolved_by
                WHERE s.id = :id
                """
            ),
            {"id": submission_id},
        )
    ).mappings().first()
    if row is None:
        return None
    notes = (
        await db.execute(
            text(
                """
                SELECT n.id, n.body, n.created_at, n.author_id, u.name AS author_name
                FROM feedback_notes n LEFT JOIN users u ON u.id = n.author_id
                WHERE n.submission_id = :id ORDER BY n.created_at, n.id
                """
            ),
            {"id": submission_id},
        )
    ).mappings().all()
    deliveries = (
        await db.execute(
            text(
                """
                SELECT d.id, d.webhook_id, w.name AS webhook_name, w.kind AS webhook_kind, d.status, d.attempts,
                       d.next_attempt_at, d.last_status_code, d.last_error, d.delivered_at, d.created_at
                FROM feedback_webhook_deliveries d JOIN feedback_webhooks w ON w.id = d.webhook_id
                WHERE d.submission_id = :id ORDER BY d.created_at, w.name
                """
            ),
            {"id": submission_id},
        )
    ).mappings().all()
    return {**dict(row), "notes": [dict(n) for n in notes], "deliveries": [dict(d) for d in deliveries]}


async def set_status(db: AsyncSession, submission_id: str, status: str, admin_id: str) -> bool:
    if status not in STATUSES:
        raise ValueError(f"Unknown status {status!r}")
    result = await db.execute(
        text(
            """
            UPDATE feedback_submissions
            SET status = :status,
                resolved_at = CASE WHEN :resolved
                                   THEN COALESCE(CASE WHEN status = 'resolved' THEN resolved_at END, NOW()) END,
                resolved_by = CASE WHEN :resolved
                                   THEN COALESCE(CASE WHEN status = 'resolved' THEN resolved_by END, :admin_id) END,
                updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": submission_id, "status": status, "resolved": status == "resolved", "admin_id": admin_id},
    )
    await db.commit()
    return bool(result.rowcount)


async def add_note(db: AsyncSession, submission_id: str, body: str, admin_id: str) -> dict[str, Any] | None:
    body = body.strip()[:MAX_NOTE_LENGTH]
    if not body:
        raise ValueError("Empty note")
    exists = (
        await db.execute(text("SELECT 1 FROM feedback_submissions WHERE id = :id"), {"id": submission_id})
    ).scalar()
    if not exists:
        return None
    note_id = str(uuid.uuid4())
    row = (
        await db.execute(
            text(
                """
                INSERT INTO feedback_notes (id, submission_id, author_id, body)
                VALUES (:id, :submission_id, :author_id, :body)
                RETURNING id, body, created_at, author_id
                """
            ),
            {"id": note_id, "submission_id": submission_id, "author_id": admin_id, "body": body},
        )
    ).mappings().one()
    await db.execute(
        text("UPDATE feedback_submissions SET updated_at = NOW() WHERE id = :id"), {"id": submission_id}
    )
    await db.commit()
    return dict(row)
