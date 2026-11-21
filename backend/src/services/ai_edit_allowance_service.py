"""Durable, workspace-wide monthly allowances for AI editor requests."""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from ..config import get_config
from ..database import AsyncSessionLocal
from .billing_service import BillingService, grants_paid_access

AI_EDIT_MONTHLY_LIMITS = {"pro": 10, "scale": 100, "team": 300}


class AIEditLimitExceeded(Exception):
    def __init__(self, usage: dict[str, Any]):
        super().__init__("AI edit allowance reached")
        self.usage = usage


class AIEditAllowanceService:
    def __init__(self, db, config=None):
        self.db = db
        self.config = config or get_config()

    async def summary(
        self, user_id: str, *, team_id: Optional[str] = None
    ) -> dict[str, Any]:
        if not self.config.monetization_enabled:
            return {
                "plan": "self_host",
                "limit": None,
                "used": 0,
                "remaining": None,
                "resetsAt": None,
                "allowed": True,
            }

        billing = BillingService(self.db, self.config)
        row = (
            await billing._load_team_billing_row(team_id)
            if team_id
            else await billing._load_user_billing_row(user_id)
        )
        now = datetime.now(timezone.utc)
        start, end = billing._usage_window(
            row.get("billing_period_start"), row.get("billing_period_end"), now
        )
        # Billing's calendar fallback ends on the final microsecond. This ledger
        # uses half-open windows so a request at renewal counts only once.
        if not row.get("billing_period_start") or not row.get("billing_period_end"):
            end += timedelta(microseconds=1)
        paid = grants_paid_access(
            row["plan"], row["subscription_status"], row.get("subscription_provider")
        )
        if team_id and row["plan"] != "team":
            paid = False

        if not paid:
            limit = 0
        elif row["plan"] == "team":
            limit = int(
                getattr(
                    self.config,
                    "team_plan_ai_edit_limit",
                    AI_EDIT_MONTHLY_LIMITS["team"],
                )
            )
        else:
            limit = AI_EDIT_MONTHLY_LIMITS.get(row["plan"], 0)

        if team_id:
            used = int(
                (
                    await self.db.execute(
                        text("""
                            SELECT COUNT(*) FROM ai_edit_usage_events
                            WHERE team_id = :team_id AND created_at >= :start AND created_at < :end
                              AND refunded_at IS NULL
                        """),
                        {"team_id": team_id, "start": start, "end": end},
                    )
                ).scalar_one()
            )
        else:
            used = int(
                (
                    await self.db.execute(
                        text("""
                            SELECT COUNT(*) FROM ai_edit_usage_events
                            WHERE user_id = :user_id AND team_id IS NULL
                              AND created_at >= :start AND created_at < :end
                              AND refunded_at IS NULL
                        """),
                        {"user_id": user_id, "start": start, "end": end},
                    )
                ).scalar_one()
            )
        member_limit: int | None = None
        member_used: int | None = None
        member_remaining: int | None = None
        member_limit_reached = False
        if team_id and paid:
            _, member_limit = await billing._load_member_limits(team_id, user_id)
            if member_limit is not None:
                member_used = int(
                    (
                        await self.db.execute(
                            text("""
                                SELECT COUNT(*) FROM ai_edit_usage_events
                                WHERE team_id = :team_id AND user_id = :user_id
                                  AND created_at >= :start AND created_at < :end
                                  AND refunded_at IS NULL
                            """),
                            {
                                "team_id": team_id,
                                "user_id": user_id,
                                "start": start,
                                "end": end,
                            },
                        )
                    ).scalar_one()
                )
                member_remaining = max(0, member_limit - member_used)
                member_limit_reached = member_used >= member_limit

        result: dict[str, Any] = {
            "plan": row["plan"],
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "resetsAt": end.isoformat(),
            "allowed": paid and used < limit and not member_limit_reached,
        }
        if team_id:
            result["memberLimit"] = member_limit
            result["memberUsed"] = member_used
            result["memberRemaining"] = member_remaining
            result["memberLimitReached"] = member_limit_reached
        return result

    async def reserve(
        self, user_id: str, job_id: str, *, team_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Commit the reservation before queueing. Serialize across workspace."""
        if not self.config.monetization_enabled:
            return await self.summary(user_id, team_id=team_id)
        lock_key = f"team:{team_id}" if team_id else user_id
        try:
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 913465))"),
                {"lock_key": lock_key},
            )
            usage = await self.summary(user_id, team_id=team_id)
            if not usage["allowed"]:
                raise AIEditLimitExceeded(usage)
            await self.db.execute(
                text("""
                    INSERT INTO ai_edit_usage_events (job_id, user_id, team_id, created_at)
                    VALUES (:job_id, :user_id, :team_id, clock_timestamp())
                """),
                {"job_id": job_id, "user_id": user_id, "team_id": team_id},
            )
            await self.db.commit()
        except BaseException:
            await self.db.rollback()
            raise
        next_remaining = max(0, usage["remaining"] - 1)
        next_summary = {
            **usage,
            "used": usage["used"] + 1,
            "remaining": next_remaining,
            "allowed": next_remaining > 0,
        }
        if usage.get("memberLimit") is not None and usage.get("memberUsed") is not None:
            next_member_used = usage["memberUsed"] + 1
            next_member_remaining = max(0, usage["memberLimit"] - next_member_used)
            next_summary["memberUsed"] = next_member_used
            next_summary["memberRemaining"] = next_member_remaining
            next_summary["memberLimitReached"] = next_member_remaining <= 0
            next_summary["allowed"] = next_remaining > 0 and next_member_remaining > 0
        return next_summary


async def refund_ai_edit_usage(job_id: str) -> None:
    """Idempotent refunds also cover jobs whose Redis request has expired."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                UPDATE ai_edit_usage_events SET refunded_at = CURRENT_TIMESTAMP
                WHERE job_id = :job_id AND refunded_at IS NULL
            """),
            {"job_id": job_id},
        )
        await db.commit()
