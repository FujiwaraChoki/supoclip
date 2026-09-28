from __future__ import annotations

from datetime import datetime, timezone
import calendar
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config

PAID_PLAN_STATUSES = {"active", "trialing"}
PAID_PLAN_LIMIT_CONFIG = {
    "pro": "pro_plan_task_limit",
    "scale": "scale_plan_task_limit",
}


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
                        subscription_provider,
                        billing_period_start,
                        billing_period_end,
                        trial_ends_at,
                        subscription_cancel_at
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
                "subscription_provider": (
                    row.subscription_provider.lower()
                    if row.subscription_provider
                    else None
                ),
                "billing_period_start": row.billing_period_start,
                "billing_period_end": row.billing_period_end,
                "trial_ends_at": row.trial_ends_at,
                "cancel_at": row.subscription_cancel_at,
            }
        except Exception:
            now = datetime.now(timezone.utc)
            start, end = self._month_window(now)
            return {
                "plan": "free",
                "subscription_status": "inactive",
                "subscription_provider": None,
                "billing_period_start": start,
                "billing_period_end": end,
                "trial_ends_at": None,
                "cancel_at": None,
            }

    async def _count_tasks(
        self, user_id: str, period_start: datetime, period_end: datetime
    ) -> int:
        # Active jobs reserve an allowance slot. Release it when a job ends
        # unsuccessfully without any saved clips, including historical failures.
        # EXISTS keeps partially successful jobs counted once, regardless of how
        # many clips were delivered. Completed jobs retain their existing charge.
        result = await self.db.execute(
            text(
                """
                SELECT COUNT(*) AS total
                FROM tasks t
                WHERE t.user_id = :user_id
                  AND t.created_at >= :period_start
                  AND t.created_at <= :period_end
                  AND (
                      COALESCE(t.status, '') NOT IN ('error', 'failed', 'cancelled', 'canceled')
                      OR EXISTS (
                          SELECT 1 FROM generated_clips c WHERE c.task_id = t.id
                      )
                  )
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
                "max_youtube_duration_seconds": self.config.max_video_duration,
                "max_upload_duration_seconds": self.config.max_video_duration,
                "subscription_status": "inactive",
                "subscription_provider": None,
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
        is_paid = plan in PAID_PLAN_LIMIT_CONFIG and status in PAID_PLAN_STATUSES

        if not is_paid:
            return {
                "monetization_enabled": True,
                "plan": plan,
                "max_youtube_duration_seconds": self.config.max_youtube_video_duration_for_plan(
                    plan, status
                ),
                "max_upload_duration_seconds": self.config.max_video_duration,
                "subscription_status": status,
                "subscription_provider": row.get("subscription_provider"),
                "period_start": start,
                "period_end": end,
                "trial_ends_at": row.get("trial_ends_at"),
                "cancel_at": row.get("cancel_at"),
                "usage_count": usage_count,
                "usage_limit": 0,
                "remaining": 0,
                "can_create_task": False,
                "upgrade_required": True,
                "reason": "Choose a paid plan to process videos.",
            }

        usage_limit = int(
            getattr(self.config, PAID_PLAN_LIMIT_CONFIG[plan])
        )
        unlimited = usage_limit <= 0
        can_create = unlimited or usage_count < usage_limit
        remaining = None if unlimited else max(usage_limit - usage_count, 0)
        reason = None if can_create else "Plan usage limit reached"

        return {
            "monetization_enabled": True,
            "plan": plan,
            "max_youtube_duration_seconds": self.config.max_youtube_video_duration_for_plan(
                plan, status
            ),
            "max_upload_duration_seconds": self.config.max_video_duration,
            "subscription_status": status,
            "subscription_provider": row.get("subscription_provider"),
            "period_start": start,
            "period_end": end,
            "trial_ends_at": row.get("trial_ends_at"),
            "cancel_at": row.get("cancel_at"),
            "usage_count": usage_count,
            "usage_limit": None if unlimited else usage_limit,
            "remaining": remaining,
            "can_create_task": can_create,
            "upgrade_required": not can_create,
            "reason": reason,
        }

    async def assert_can_create_task(self, user_id: str) -> dict[str, Any]:
        summary = await self.get_usage_summary(user_id)
        if summary.get("can_create_task"):
            return summary
        raise BillingLimitExceeded(summary)

    async def assert_can_resume_task(
        self, user_id: str, task_id: str
    ) -> dict[str, Any]:
        """Require a slot for a refunded failure, without charging partial output twice."""
        summary = await self.get_usage_summary(user_id)
        if summary.get("can_create_task"):
            return summary

        if (
            summary.get("plan") in PAID_PLAN_LIMIT_CONFIG
            and summary.get("subscription_status") in PAID_PLAN_STATUSES
        ):
            # A partially delivered task already occupies a current-period slot.
            # It can resume even at the cap, but unrelated/historical output
            # must not allow an uncounted task to bypass the cap.
            result = await self.db.execute(
                text(
                    """
                    SELECT 1
                    FROM tasks t
                    WHERE t.id = :task_id
                      AND t.user_id = :user_id
                      AND t.created_at >= :period_start
                      AND t.created_at <= :period_end
                      AND EXISTS (
                          SELECT 1 FROM generated_clips c WHERE c.task_id = t.id
                      )
                    """
                ),
                {
                    "task_id": task_id,
                    "user_id": user_id,
                    "period_start": summary["period_start"],
                    "period_end": summary["period_end"],
                },
            )
            if result.fetchone() is not None:
                return summary
        raise BillingLimitExceeded(summary)
