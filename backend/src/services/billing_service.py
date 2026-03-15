from __future__ import annotations

from datetime import datetime, timezone
import calendar
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config


class BillingLimitExceeded(Exception):
    def __init__(self, summary: dict[str, Any]):
        super().__init__("Billing limit reached")
        self.summary = summary


class BillingService:
    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.config = config or get_config()

    @staticmethod
    def _month_window(now: datetime) -> tuple[datetime, datetime]:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        _, day_count = calendar.monthrange(start.year, start.month)
        end = start.replace(
            day=day_count, hour=23, minute=59, second=59, microsecond=999999
        )
        return start, end

    async def _load_user_billing_row(self, user_id: str) -> dict[str, Any]:
        try:
            result = await self.db.execute(
                text(
                    """
                    SELECT
                        plan,
                        subscription_status,
                        billing_period_start,
                        billing_period_end,
                        trial_ends_at
                    FROM users
                    WHERE id = :user_id
                    """
                ),
                {"user_id": user_id},
            )
            row = result.fetchone()
            if not row:
                raise ValueError("User not found")

            return {
                "plan": (row.plan or "free").lower(),
                "subscription_status": (row.subscription_status or "inactive").lower(),
                "billing_period_start": row.billing_period_start,
                "billing_period_end": row.billing_period_end,
                "trial_ends_at": row.trial_ends_at,
            }
        except Exception:
            now = datetime.now(timezone.utc)
            start, end = self._month_window(now)
            return {
                "plan": "free",
                "subscription_status": "inactive",
                "billing_period_start": start,
                "billing_period_end": end,
                "trial_ends_at": None,
            }

    async def _count_tasks(
        self, user_id: str, period_start: datetime, period_end: datetime
    ) -> int:
        result = await self.db.execute(
            text(
                """
                SELECT COUNT(*) AS total
                FROM tasks
                WHERE user_id = :user_id
                  AND created_at >= :period_start
                  AND created_at <= :period_end
                """
            ),
            {
                "user_id": user_id,
                "period_start": period_start,
                "period_end": period_end,
            },
        )
        row = result.fetchone()
        return int(row.total) if row and row.total is not None else 0

    async def get_usage_summary(self, user_id: str) -> dict[str, Any]:
        if not self.config.monetization_enabled:
            return {
                "monetization_enabled": False,
                "plan": "self_host",
                "subscription_status": "inactive",
                "period_start": None,
                "period_end": None,
                "usage_count": 0,
                "usage_limit": None,
                "remaining": None,
                "can_create_task": True,
                "upgrade_required": False,
                "reason": None,
            }

        row = await self._load_user_billing_row(user_id)
        now = datetime.now(timezone.utc)

        start = row.get("billing_period_start")
        end = row.get("billing_period_end")
        if not start or not end:
            start, end = self._month_window(now)

        usage_count = await self._count_tasks(user_id, start, end)

        plan = row["plan"]
        status = row["subscription_status"]
        is_paid = plan == "pro" and status in {"active", "trialing"}

        # Hosted mode requires an active/trialing paid subscription.
        if not is_paid:
            return {
                "monetization_enabled": True,
                "plan": plan,
                "subscription_status": status,
                "period_start": start,
                "period_end": end,
                "trial_ends_at": row.get("trial_ends_at"),
                "usage_count": usage_count,
                "usage_limit": None,
                "remaining": None,
                "can_create_task": False,
                "upgrade_required": True,
                "reason": "Active subscription required",
            }

        usage_limit = self.config.pro_plan_task_limit
        unlimited = usage_limit <= 0
        can_create = unlimited or usage_count < usage_limit
        remaining = None if unlimited else max(usage_limit - usage_count, 0)
        reason = None if can_create else "Plan usage limit reached"

        return {
            "monetization_enabled": True,
            "plan": plan,
            "subscription_status": status,
            "period_start": start,
            "period_end": end,
            "trial_ends_at": row.get("trial_ends_at"),
            "usage_count": usage_count,
            "usage_limit": None if unlimited else usage_limit,
            "remaining": remaining,
            "can_create_task": can_create,
            "upgrade_required": not can_create,
            "reason": reason,
        }

    async def assert_can_create_task(self, user_id: str) -> None:
        summary = await self.get_usage_summary(user_id)
        if summary.get("can_create_task"):
            return
        raise BillingLimitExceeded(summary)
