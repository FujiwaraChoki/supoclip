"""Durable, account-wide monthly allowances for AI editor requests."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from ..config import get_config
from ..database import AsyncSessionLocal
from .billing_service import BillingService, grants_paid_access

AI_EDIT_MONTHLY_LIMITS = {"pro": 10, "scale": 100}


class AIEditLimitExceeded(Exception):
    def __init__(self, usage: dict[str, Any]):
        super().__init__("AI edit allowance reached")
        self.usage = usage


class AIEditAllowanceService:
    def __init__(self, db, config=None):
        self.db = db
        self.config = config or get_config()

    async def summary(self, user_id: str) -> dict[str, Any]:
        if not self.config.monetization_enabled:
            return {"plan": "self_host", "limit": None, "used": 0,
                    "remaining": None, "resetsAt": None, "allowed": True}

        billing = BillingService(self.db, self.config)
        row = await billing._load_user_billing_row(user_id)
        now = datetime.now(timezone.utc)
        start, end = billing._usage_window(
            row.get("billing_period_start"), row.get("billing_period_end"), now
        )
        # Billing's calendar fallback ends on the final microsecond. This ledger
        # uses half-open windows so a request at renewal counts only once.
        if not row.get("billing_period_start") or not row.get("billing_period_end"):
            end += timedelta(microseconds=1)
        paid = grants_paid_access(row["plan"], row["subscription_status"], row.get("subscription_provider"))
        limit = AI_EDIT_MONTHLY_LIMITS.get(row["plan"], 0) if paid else 0
        used = int((await self.db.execute(text("""
            SELECT COUNT(*) FROM ai_edit_usage_events
            WHERE user_id = :user_id AND created_at >= :start AND created_at < :end
              AND refunded_at IS NULL
        """), {"user_id": user_id, "start": start, "end": end})).scalar_one())
        return {"plan": row["plan"], "limit": limit, "used": used,
                "remaining": max(0, limit - used), "resetsAt": end.isoformat(),
                "allowed": paid and used < limit}

    async def reserve(self, user_id: str, job_id: str) -> dict[str, Any]:
        """Commit the reservation before queueing. Serialize all of an owner's clips."""
        if not self.config.monetization_enabled:
            return await self.summary(user_id)
        try:
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 913465))"),
                {"user_id": user_id},
            )
            usage = await self.summary(user_id)
            if not usage["allowed"]:
                raise AIEditLimitExceeded(usage)
            await self.db.execute(text("""
                INSERT INTO ai_edit_usage_events (job_id, user_id, created_at)
                VALUES (:job_id, :user_id, clock_timestamp())
            """), {"job_id": job_id, "user_id": user_id})
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise
        return {**usage, "used": usage["used"] + 1,
                "remaining": usage["remaining"] - 1, "allowed": usage["remaining"] > 1}


async def refund_ai_edit_usage(job_id: str) -> None:
    """Idempotent refunds also cover jobs whose Redis request has expired."""
    async with AsyncSessionLocal() as db:
        await db.execute(text("""
            UPDATE ai_edit_usage_events SET refunded_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id AND refunded_at IS NULL
        """), {"job_id": job_id})
        await db.commit()
