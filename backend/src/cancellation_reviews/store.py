"""SQL for cancellation reviews (raw ``text()`` queries, like the repositories)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .folding import OpenReview, StoredEvent
from .intake import CancellationEvent
from .schema import has_column, has_table

REVIEW_COLUMNS = (
    "id, account_id, livemode, subscription_id, customer_id, user_id, kind, status, origin, exclusion_reason, "
    "snapshot, snapshot_at, reported_feedback, requested_at, ended_at, withdrawn_at, not_before, attempts, "
    "lease_expires_at, last_error, report_text, reported_at, created_at"
)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"), default=str)


async def insert_event(db: AsyncSession, account_id: str, event: CancellationEvent) -> bool:
    """Store a verified event once; False when Stripe redelivered one we already have."""
    result = await db.execute(
        text(
            """
            INSERT INTO stripe_cancellation_events
                (id, account_id, livemode, event_id, event_type, event_created, subscription_id, customer_id, snapshot)
            VALUES (:id, :account_id, :livemode, :event_id, :event_type, :created, :subscription_id, :customer_id,
                    CAST(:snapshot AS JSONB))
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": f"{account_id}:{'live' if event.livemode else 'test'}:{event.event_id}",
            "account_id": account_id,
            "livemode": event.livemode,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "created": event.created,
            "subscription_id": event.subscription_id,
            "customer_id": event.customer_id,
            "snapshot": _json(event.snapshot),
        },
    )
    await db.commit()
    return result.rowcount == 1


async def is_supoclip_customer(db: AsyncSession, account_id: str, customer_id: str) -> bool:
    """For an account shared with other products: only SupoClip customers are kept."""
    params = {"customer": customer_id, "account": account_id}
    if await has_column(db, "users", "stripe_account_id"):
        query = "SELECT 1 FROM users WHERE stripe_customer_id = :customer AND stripe_account_id = :account"
        if (await db.execute(text(query), params)).first():
            return True
    if await has_table(db, "stripe_account_migrations"):
        query = """
            SELECT 1 FROM stripe_account_migrations
            WHERE (destination_account = :account AND destination_customer = :customer)
               OR (source_account = :account AND source_customer = :customer)
            LIMIT 1
        """
        if (await db.execute(text(query), params)).first():
            return True
    return False


MAX_FOLD_ATTEMPTS = 5


async def unfolded_events(db: AsyncSession, limit: int = 200) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT id, account_id, livemode, event_type, event_created, subscription_id, customer_id, snapshot,
                   fold_attempts
            FROM stripe_cancellation_events
            WHERE folded_at IS NULL AND fold_attempts < :max_attempts
            ORDER BY event_created, received_at
            LIMIT :limit
            """
        ),
        {"limit": limit, "max_attempts": MAX_FOLD_ATTEMPTS},
    )
    return [dict(row._mapping) for row in rows]


def stored_event(row: dict[str, Any]) -> StoredEvent:
    return StoredEvent(event_type=row["event_type"], created=row["event_created"], snapshot=row["snapshot"])


async def lock_open_review(db: AsyncSession, subscription_id: str) -> OpenReview | None:
    row = (
        await db.execute(
            text(
                """
                SELECT id, status, kind, snapshot, snapshot_at, reported_feedback
                FROM cancellation_reviews
                WHERE subscription_id = :subscription_id AND status <> 'withdrawn'
                FOR UPDATE
                """
            ),
            {"subscription_id": subscription_id},
        )
    ).first()
    if row is None:
        return None
    return OpenReview(
        id=row.id, status=row.status, kind=row.kind, snapshot=row.snapshot or {},
        snapshot_at=row.snapshot_at, reported_feedback=row.reported_feedback,
    )


async def create_review(db: AsyncSession, *, account_id: str, livemode: bool, subscription_id: str,
                        customer_id: str | None, user_id: str | None = None, origin: str = "webhook",
                        **fields: Any) -> str:
    review_id = str(uuid.uuid4())
    await db.execute(
        text(
            """
            INSERT INTO cancellation_reviews
                (id, account_id, livemode, subscription_id, customer_id, user_id, kind, status, origin,
                 snapshot, snapshot_at, requested_at, ended_at, not_before)
            VALUES (:id, :account_id, :livemode, :subscription_id, :customer_id, :user_id, :kind, 'pending', :origin,
                    CAST(:snapshot AS JSONB), :snapshot_at, :requested_at, :ended_at, :not_before)
            """
        ),
        {
            "id": review_id, "account_id": account_id, "livemode": livemode, "subscription_id": subscription_id,
            "customer_id": customer_id, "user_id": user_id, "kind": fields["kind"], "origin": origin,
            "snapshot": _json(fields.get("snapshot") or {}), "snapshot_at": fields.get("snapshot_at"),
            "requested_at": fields.get("requested_at"), "ended_at": fields.get("ended_at"),
            "not_before": fields["not_before"],
        },
    )
    return review_id


_UPDATABLE = {
    "status", "requested_at", "account_id", "customer_id", "livemode", "snapshot", "snapshot_at", "reported_feedback", "ended_at", "withdrawn_at", "not_before", "user_id",
    "exclusion_reason", "attempts", "lease_expires_at", "last_error", "evidence", "report_text", "reported_at",
}
_JSON_COLUMNS = {"snapshot", "reported_feedback", "evidence"}


async def update_review(db: AsyncSession, review_id: str, **fields: Any) -> None:
    if not fields:
        return
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"unknown review fields: {sorted(unknown)}")
    assignments = ", ".join(
        f"{name} = CAST(:{name} AS JSONB)" if name in _JSON_COLUMNS else f"{name} = :{name}" for name in fields
    )
    params = {name: _json(value) if name in _JSON_COLUMNS else value for name, value in fields.items()}
    await db.execute(
        text(f"UPDATE cancellation_reviews SET {assignments}, updated_at = NOW() WHERE id = :review_id"),
        {**params, "review_id": review_id},
    )


async def mark_event_folded(db: AsyncSession, event_id: str, error: str | None = None) -> None:
    await db.execute(
        text(
            "UPDATE stripe_cancellation_events SET folded_at = CASE WHEN CAST(:error AS TEXT) IS NULL THEN NOW() END, "
            "fold_error = CAST(:error AS TEXT), fold_attempts = fold_attempts + 1 WHERE id = :id"
        ),
        {"id": event_id, "error": error},
    )


async def get_review(db: AsyncSession, review_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(text(f"SELECT {REVIEW_COLUMNS} FROM cancellation_reviews WHERE id = :id"), {"id": review_id})
    ).first()
    return dict(row._mapping) if row else None


async def claim_due_reviews(db: AsyncSession, now: datetime, lease: timedelta, limit: int) -> list[dict[str, Any]]:
    """Pending reviews past their settle window, plus ones whose worker died mid-investigation."""
    rows = await db.execute(
        text(
            f"""
            UPDATE cancellation_reviews SET status = 'processing', attempts = attempts + 1,
                lease_expires_at = :lease_until, updated_at = NOW()
            WHERE id IN (
                SELECT id FROM cancellation_reviews
                WHERE (status = 'pending' AND not_before <= :now)
                   OR (status = 'processing' AND lease_expires_at < :now)
                ORDER BY not_before
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            RETURNING {REVIEW_COLUMNS}
            """
        ),
        {"now": now, "lease_until": now + lease, "limit": limit},
    )
    claimed = [dict(row._mapping) for row in rows]
    await db.commit()
    return claimed


async def enqueue_delivery(db: AsyncSession, review_id: str, kind: str, message: str) -> str:
    delivery_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO cancellation_report_deliveries (id, review_id, kind, message) "
            "VALUES (:id, :review_id, :kind, :message)"
        ),
        {"id": delivery_id, "review_id": review_id, "kind": kind, "message": message},
    )
    return delivery_id


async def due_deliveries(db: AsyncSession, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT id, review_id, kind, message, attempts FROM cancellation_report_deliveries
            WHERE status = 'pending' AND next_attempt_at <= :now
            ORDER BY created_at LIMIT :limit
            """
        ),
        {"now": now, "limit": limit},
    )
    return [dict(row._mapping) for row in rows]


async def mark_delivery(db: AsyncSession, delivery_id: str, *, sent: bool, error: str | None = None,
                        retry_at: datetime | None = None) -> None:
    if sent:
        await db.execute(
            text(
                "UPDATE cancellation_report_deliveries SET status = 'sent', sent_at = NOW(), attempts = attempts + 1, "
                "last_error = NULL WHERE id = :id"
            ),
            {"id": delivery_id},
        )
    else:
        await db.execute(
            text(
                "UPDATE cancellation_report_deliveries SET attempts = attempts + 1, last_error = :error, "
                "next_attempt_at = :retry_at WHERE id = :id"
            ),
            {"id": delivery_id, "error": (error or "")[:500], "retry_at": retry_at},
        )
    await db.commit()


async def insert_note(db: AsyncSession, user_id: str, message: str) -> None:
    await db.execute(
        text("INSERT INTO cancellation_notes (id, user_id, message) VALUES (:id, :user_id, :message)"),
        {"id": str(uuid.uuid4()), "user_id": user_id, "message": message},
    )
    await db.commit()


async def notes_for_user(db: AsyncSession, user_id: str, since: datetime) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            "SELECT id, message, created_at, review_id FROM cancellation_notes "
            "WHERE user_id = :user_id AND created_at >= :since ORDER BY created_at"
        ),
        {"user_id": user_id, "since": since},
    )
    return [dict(row._mapping) for row in rows]


async def attach_notes(db: AsyncSession, note_ids: list[str], review_id: str) -> None:
    if note_ids:
        await db.execute(
            text("UPDATE cancellation_notes SET review_id = :review_id WHERE id = ANY(:ids) AND review_id IS NULL"),
            {"review_id": review_id, "ids": note_ids},
        )


async def late_notes(db: AsyncSession, window: timedelta, now: datetime) -> list[dict[str, Any]]:
    """Notes not yet used by a review, from users with a recently reported review."""
    rows = await db.execute(
        text(
            """
            SELECT n.id, n.message, n.created_at, r.id AS review_id, r.status AS review_status
            FROM cancellation_notes n
            JOIN LATERAL (
                SELECT id, status FROM cancellation_reviews
                WHERE user_id = n.user_id AND status IN ('reported', 'pending', 'processing')
                  AND created_at >= :since
                ORDER BY created_at DESC LIMIT 1
            ) r ON TRUE
            WHERE n.review_id IS NULL AND n.created_at >= :since
            ORDER BY n.created_at
            """
        ),
        {"since": now - window},
    )
    return [dict(row._mapping) for row in rows]
