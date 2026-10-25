from __future__ import annotations

from datetime import datetime, timezone
import calendar
import math
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config
from ..repositories.minute_usage_repository import charged_duration, record_minute_usage, reactivate_minute_usage
from ..repositories.edit_transaction import TaskCancelled

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


def grants_paid_access(
    plan: str | None, status: str | None, provider: str | None = None
) -> bool:
    """Whether this row may spend its plan allowance.

    Stripe leaves a subscription `past_due` while it retries the invoice.
    Access continues for that window so a failed charge is not a downgrade.
    `unpaid` means retries are finished. App Store `past_due` is set only
    after the grace period and does not keep access.
    """
    normalized_plan = (plan or "").lower()
    normalized_status = (status or "").lower()
    if normalized_plan not in PAID_PLAN_MINUTE_LIMIT_CONFIG:
        return False
    if normalized_status in PAID_PLAN_STATUSES:
        return True
    return normalized_status == "past_due" and (provider or "").lower() == "stripe"


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
        """Return durable task charges and minutes in the allowance window."""
        result = await self.db.execute(
            text(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(minutes), 0) AS minutes
                FROM minute_usage_events
                WHERE user_id = :user_id
                  AND created_at >= :period_start
                  AND created_at <= :period_end
                  AND refunded_at IS NULL
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
                "max_upload_bytes": self.config.max_video_upload_bytes,
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
        provider = row.get("subscription_provider")
        is_paid = grants_paid_access(plan, status, provider)

        common = {
            "monetization_enabled": True,
            "plan": plan,
            "max_youtube_duration_seconds": self.config.max_youtube_video_duration_for_plan(
                plan, status, provider
            ),
            "max_upload_duration_seconds": self.config.max_video_duration,
            "max_upload_bytes": self.config.max_video_upload_bytes,
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
        self, user_id: str, task_id: str, *, require_active_plan: bool = True
    ) -> dict[str, Any]:
        """Reuse an active charge or re-charge a refunded retry after checking room.

        The caller holds the user's allowance lock through this check and commit.
        Workers may run an already-paid queued job after its plan lapses. Explicit
        resumes and all new/reinstated charges still require paid access.
        """
        summary = await self.get_usage_summary(user_id)
        if not self.config.monetization_enabled:
            return summary
        paid_access = grants_paid_access(
            summary.get("plan"), summary.get("subscription_status"),
            summary.get("subscription_provider"),
        )
        result = await self.db.execute(
            text("""
                SELECT refunded_at, source_duration_seconds FROM minute_usage_events
                WHERE task_id = :task_id AND user_id = :user_id
            """),
            {"task_id": task_id, "user_id": user_id},
        )
        event = result.fetchone()
        if event is not None and event.refunded_at is None:
            if require_active_plan and not paid_access:
                raise BillingLimitExceeded(summary)
            return summary
        if not paid_access:
            raise BillingLimitExceeded(summary)

        result = await self.db.execute(
            text("""
                SELECT t.source_duration_seconds, s.url AS source_url, t.source_metadata
                FROM tasks t LEFT JOIN sources s ON s.id = t.source_id
                WHERE t.id = :task_id AND t.user_id = :user_id
            """),
            {"task_id": task_id, "user_id": user_id},
        )
        task = result.fetchone()
        if task is None:
            raise ValueError("Task not found")
        try:
            seconds = charged_duration(
                event.source_duration_seconds if event is not None else task.source_duration_seconds
            )
        except ValueError:
            from .video_service import VideoService

            url = task.source_url or (task.source_metadata or {}).get("url")
            if not url:
                raise ValueError("Task source URL is missing")
            seconds = await VideoService().measure_source_duration(url, user_id)

        # Refunded rows do not contribute usage, so the entire retry must fit.
        await self.assert_can_create_task(user_id, seconds)
        try:
            result = await self.db.execute(
                text("""
                    UPDATE tasks SET source_duration_seconds = :seconds
                    WHERE id = :task_id AND user_id = :user_id
                      AND (:explicit_resume OR status IN ('queued', 'processing'))
                """),
                {
                    "seconds": seconds, "task_id": task_id, "user_id": user_id,
                    "explicit_resume": require_active_plan,
                },
            )
            if result.rowcount == 0:
                # Cancellation may win while a legacy worker measures duration.
                # Do not re-charge a task that has already stopped.
                raise TaskCancelled()
            if event is not None:
                await reactivate_minute_usage(self.db, user_id, task_id)
            else:
                await record_minute_usage(self.db, user_id, task_id, seconds)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return summary
