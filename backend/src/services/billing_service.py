from __future__ import annotations

from datetime import datetime, timezone
import calendar
import math
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config

PAID_PLAN_STATUSES = {"active", "trialing"}
PAID_PLAN_MINUTE_LIMIT_CONFIG = {
    "pro": "pro_plan_minute_limit",
    "scale": "scale_plan_minute_limit",
}
# Short side, in pixels, of the clips each plan receives. Pro subscribers from
# before the 720p change keep 1080p while they stay subscribed (legacy_hd).
FULL_HD_SHORT_SIDE = 1080
HD_SHORT_SIDE = 720
SCHEDULING_PLANS = {"scale"}
# Periods longer than this are annual; their allowance still resets monthly.
MAX_MONTHLY_PERIOD_DAYS = 32


def source_minutes(duration_seconds: float | None) -> int:
    """Minutes charged for a source video: every started minute counts."""
    if not duration_seconds or duration_seconds <= 0:
        return 0
    return math.ceil(duration_seconds / 60)


def _add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def reduced_media_limits(summary: dict[str, Any]) -> tuple[str | None, int | None]:
    """(YouTube download quality, clip short-side cap) for plans below 1080p.

    Returns (None, None) for 1080p plans and self-hosting, which keep the
    default download quality and whatever the renderer produces.
    """
    short_side = summary.get("output_short_side")
    if short_side and short_side < FULL_HD_SHORT_SIDE:
        return str(short_side), short_side
    return None, None


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

    @classmethod
    def _usage_window(
        cls, period_start: datetime | None, period_end: datetime | None, now: datetime
    ) -> tuple[datetime, datetime]:
        """The month whose usage counts against the allowance right now.

        Monthly subscriptions use their billing period. Annual ones use the
        current month of the year, anchored on the subscription's start day.
        """
        if not period_start or not period_end:
            return cls._month_window(now)
        if (period_end - period_start).days <= MAX_MONTHLY_PERIOD_DAYS:
            return period_start, period_end

        months = (now.year - period_start.year) * 12 + now.month - period_start.month
        start = _add_months(period_start, months)
        if start > now:
            months -= 1
            start = _add_months(period_start, months)
        end = min(_add_months(period_start, months + 1), period_end)
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
                        subscription_cancel_at,
                        legacy_hd
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
                "legacy_hd": bool(getattr(row, "legacy_hd", False)),
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
                "legacy_hd": False,
            }

    async def _count_usage(
        self, user_id: str, period_start: datetime, period_end: datetime
    ) -> tuple[int, int]:
        """Return (tasks, source minutes) charged in the window.

        Active jobs reserve their minutes. A job that ends unsuccessfully without
        any saved clips releases them, including historical failures. EXISTS
        keeps partially successful jobs counted once, regardless of how many
        clips were delivered. Jobs without a recorded duration (uploads still
        downloading, or tasks from before minute metering) count as 0 minutes.
        """
        result = await self.db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    -- Whole-second integers: (s + 59) / 60 rounds up portably.
                    COALESCE(
                        SUM((COALESCE(t.source_duration_seconds, 0) + 59) / 60), 0
                    ) AS minutes
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
        if not row:
            return 0, 0
        total = int(row.total) if row.total is not None else 0
        minutes = getattr(row, "minutes", None)
        return total, int(minutes) if minutes is not None else 0

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
                "usage_minutes": 0,
                "minute_limit": None,
                "remaining_minutes": None,
                # Self-hosted output is never downscaled.
                "output_short_side": None,
                "legacy_hd": False,
                "can_schedule_posts": True,
                "can_create_task": True,
                "upgrade_required": False,
                "reason": None,
            }

        row = await self._load_user_billing_row(user_id)
        now = datetime.now(timezone.utc)
        start, end = self._usage_window(
            row.get("billing_period_start"), row.get("billing_period_end"), now
        )
        usage_count, usage_minutes = await self._count_usage(user_id, start, end)

        plan = row["plan"]
        status = row["subscription_status"]
        is_paid = plan in PAID_PLAN_MINUTE_LIMIT_CONFIG and status in PAID_PLAN_STATUSES

        common = {
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
            "usage_minutes": usage_minutes,
        }

        if not is_paid:
            return {
                **common,
                "usage_limit": 0,
                "remaining": 0,
                "minute_limit": 0,
                "remaining_minutes": 0,
                "output_short_side": HD_SHORT_SIDE,
                "legacy_hd": False,
                "can_schedule_posts": False,
                "can_create_task": False,
                "upgrade_required": True,
                "reason": "Choose a paid plan to process videos.",
            }

        minute_limit = int(getattr(self.config, PAID_PLAN_MINUTE_LIMIT_CONFIG[plan]))
        unlimited = minute_limit <= 0
        remaining_minutes = None if unlimited else max(minute_limit - usage_minutes, 0)
        can_create = unlimited or remaining_minutes > 0
        billing_start = row.get("billing_period_start")
        billing_end = row.get("billing_period_end")
        billing_interval = (
            "year"
            if billing_start
            and billing_end
            and (billing_end - billing_start).days > MAX_MONTHLY_PERIOD_DAYS
            else "month"
        )
        legacy_hd = bool(plan == "pro" and row.get("legacy_hd", False))
        full_hd = plan != "pro" or legacy_hd

        return {
            **common,
            "billing_interval": billing_interval,
            # Allowances are measured in minutes; there is no per-task cap.
            "usage_limit": None,
            "remaining": None,
            "minute_limit": None if unlimited else minute_limit,
            "remaining_minutes": remaining_minutes,
            "output_short_side": FULL_HD_SHORT_SIDE if full_hd else HD_SHORT_SIDE,
            "legacy_hd": legacy_hd,
            "can_schedule_posts": plan in SCHEDULING_PLANS,
            "can_create_task": can_create,
            "upgrade_required": not can_create,
            "reason": None if can_create else "You've used this month's video minutes.",
        }

    async def assert_can_create_task(
        self, user_id: str, duration_seconds: float | None = None
    ) -> dict[str, Any]:
        """Require an active plan with room left, and for a known source length,
        enough minutes to cover the whole video."""
        summary = await self.get_usage_summary(user_id)
        if not summary.get("can_create_task"):
            raise BillingLimitExceeded(summary)

        needed = source_minutes(duration_seconds)
        remaining = summary.get("remaining_minutes")
        if remaining is not None and needed > remaining:
            raise BillingLimitExceeded(
                {
                    **summary,
                    "upgrade_required": True,
                    "required_minutes": needed,
                    "reason": (
                        f"This video is {needed} minutes long and you have "
                        f"{remaining} minutes left this month."
                    ),
                }
            )
        return summary

    async def assert_can_resume_task(
        self, user_id: str, task_id: str
    ) -> dict[str, Any]:
        """Require a slot for a refunded failure, without charging partial output twice."""
        summary = await self.get_usage_summary(user_id)
        if summary.get("can_create_task"):
            return summary

        if (
            summary.get("plan") in PAID_PLAN_MINUTE_LIMIT_CONFIG
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
