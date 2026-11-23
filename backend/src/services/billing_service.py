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
    "team": "team_plan_minute_limit",
}
# Short side, in pixels, of the clips each plan receives. Pro subscribers from
# before the 720p change keep 1080p while they stay subscribed (legacy_hd).
FULL_HD_SHORT_SIDE = 1080
HD_SHORT_SIDE = 720
SCHEDULING_PLANS = {"scale", "team"}
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

    async def _load_team_billing_row(self, team_id: str) -> dict[str, Any]:
        result = await self.db.execute(
            text(
                """
                SELECT
                    name,
                    plan,
                    subscription_status,
                    subscription_provider,
                    seat_limit,
                    billing_period_start,
                    billing_period_end,
                    subscription_cancel_at
                FROM teams
                WHERE id = :team_id
                """
            ),
            {"team_id": team_id},
        )
        row = result.fetchone()
        if not row:
            raise ValueError("Team not found")

        return {
            "name": row.name,
            "plan": (row.plan or "free").lower(),
            "subscription_status": (row.subscription_status or "inactive").lower(),
            "subscription_provider": (
                row.subscription_provider.lower()
                if row.subscription_provider
                else None
            ),
            "seat_limit": max(int(getattr(row, "seat_limit", None) or self.config.team_included_seats), 1),
            "billing_period_start": row.billing_period_start,
            "billing_period_end": row.billing_period_end,
            "trial_ends_at": None,
            "cancel_at": row.subscription_cancel_at,
            "legacy_hd": False,
        }

    async def _count_usage(
        self,
        user_id: str,
        period_start: datetime,
        period_end: datetime,
        *,
        team_id: str | None = None,
    ) -> tuple[int, int]:
        """Return durable task charges and minutes in the allowance window."""
        if team_id:
            result = await self.db.execute(
                text(
                    """
                    SELECT COUNT(*) AS total, COALESCE(SUM(minutes), 0) AS minutes
                    FROM minute_usage_events
                    WHERE team_id = :team_id
                      AND created_at >= :period_start
                      AND created_at <= :period_end
                      AND refunded_at IS NULL
                    """
                ),
                {
                    "team_id": team_id,
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )
        else:
            result = await self.db.execute(
                text(
                    """
                    SELECT COUNT(*) AS total, COALESCE(SUM(minutes), 0) AS minutes
                    FROM minute_usage_events
                    WHERE user_id = :user_id
                      AND team_id IS NULL
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

    async def _load_member_limits(
        self, team_id: str, user_id: str
    ) -> tuple[int | None, int | None]:
        """Return (monthly_minute_limit, monthly_ai_edit_limit) for a team member.

        Owners are never subject to per-member caps.
        """
        result = await self.db.execute(
            text(
                """
                SELECT role, monthly_minute_limit, monthly_ai_edit_limit
                FROM team_members
                WHERE team_id = :team_id AND user_id = :user_id
                """
            ),
            {"team_id": team_id, "user_id": user_id},
        )
        row = result.fetchone()
        if not row or (getattr(row, "role", None) or "").lower() == "owner":
            return None, None
        min_lim = getattr(row, "monthly_minute_limit", None)
        ai_lim = getattr(row, "monthly_ai_edit_limit", None)
        return (
            int(min_lim) if min_lim is not None else None,
            int(ai_lim) if ai_lim is not None else None,
        )

    async def _count_member_usage(
        self,
        team_id: str,
        user_id: str,
        period_start: datetime,
        period_end: datetime,
    ) -> tuple[int, int]:
        """Return durable task charges and minutes for one member inside a team."""
        result = await self.db.execute(
            text(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(minutes), 0) AS minutes
                FROM minute_usage_events
                WHERE team_id = :team_id
                  AND user_id = :user_id
                  AND created_at >= :period_start
                  AND created_at <= :period_end
                  AND refunded_at IS NULL
                """
            ),
            {
                "team_id": team_id,
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

    async def get_team_member_usage_map(
        self, team_id: str, *, only_user_id: str | None = None
    ) -> dict[str, dict[str, int]]:
        """Return per-member current-period minutes and AI edits for a team."""
        try:
            row = await self._load_team_billing_row(team_id)
        except Exception:
            return {}
        now = datetime.now(timezone.utc)
        start, end = self._usage_window(
            row.get("billing_period_start"), row.get("billing_period_end"), now
        )
        user_filter = " AND user_id = :only_user_id" if only_user_id else ""
        params: dict[str, Any] = {
            "team_id": team_id,
            "period_start": start,
            "period_end": end,
        }
        if only_user_id:
            params["only_user_id"] = only_user_id

        usage_map: dict[str, dict[str, int]] = {}
        min_rows = (
            await self.db.execute(
                text(
                    f"""
                    SELECT user_id, COALESCE(SUM(minutes), 0) AS minutes
                    FROM minute_usage_events
                    WHERE team_id = :team_id
                      AND created_at >= :period_start
                      AND created_at <= :period_end
                      AND refunded_at IS NULL
                      {user_filter}
                    GROUP BY user_id
                    """
                ),
                params,
            )
        ).fetchall()
        for r in min_rows:
            uid = str(r.user_id)
            usage_map[uid] = {
                "usage_minutes": int(r.minutes or 0),
                "ai_edits_used": 0,
            }

        ai_rows = (
            await self.db.execute(
                text(
                    f"""
                    SELECT user_id, COUNT(*) AS total
                    FROM ai_edit_usage_events
                    WHERE team_id = :team_id
                      AND created_at >= :period_start
                      AND created_at <= :period_end
                      AND refunded_at IS NULL
                      {user_filter}
                    GROUP BY user_id
                    """
                ),
                params,
            )
        ).fetchall()
        for r in ai_rows:
            uid = str(r.user_id)
            entry = usage_map.setdefault(uid, {"usage_minutes": 0, "ai_edits_used": 0})
            entry["ai_edits_used"] = int(r.total or 0)

        return usage_map

    async def get_usage_summary(
        self, user_id: str, *, team_id: str | None = None
    ) -> dict[str, Any]:
        if not self.config.monetization_enabled:
            summary: dict[str, Any] = {
                "monetization_enabled": False,
                "workspace_type": "team" if team_id else "personal",
                "team_id": team_id,
                "plan": "self_host",
                # Self-hosted instances are not limited by video length.
                "max_youtube_duration_seconds": None,
                "max_upload_duration_seconds": None,
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
            if team_id:
                try:
                    team_row = await self._load_team_billing_row(team_id)
                    summary["team_name"] = team_row.get("name")
                    summary["seat_limit"] = team_row.get("seat_limit")
                    summary["included_seats"] = self.config.team_included_seats
                except Exception:
                    pass
            return summary

        row = (
            await self._load_team_billing_row(team_id)
            if team_id
            else await self._load_user_billing_row(user_id)
        )
        now = datetime.now(timezone.utc)
        start, end = self._usage_window(
            row.get("billing_period_start"), row.get("billing_period_end"), now
        )
        usage_count, usage_minutes = await self._count_usage(
            user_id, start, end, team_id=team_id
        )

        plan = row["plan"]
        status = row["subscription_status"]
        provider = row.get("subscription_provider")
        is_paid = grants_paid_access(plan, status, provider)
        if team_id and plan != "team":
            is_paid = False

        max_duration = self.config.max_youtube_video_duration_for_plan(plan, status, provider)
        common: dict[str, Any] = {
            "monetization_enabled": True,
            "workspace_type": "team" if team_id else "personal",
            "team_id": team_id,
            "plan": plan,
            "max_youtube_duration_seconds": max_duration,
            # One per-video cap for links and uploads alike.
            "max_upload_duration_seconds": max_duration,
            "max_upload_bytes": self.config.max_upload_bytes_for_plan(plan, status, provider),
            "subscription_status": status,
            "subscription_provider": row.get("subscription_provider"),
            "period_start": start,
            "period_end": end,
            "trial_ends_at": row.get("trial_ends_at"),
            "cancel_at": row.get("cancel_at"),
            "usage_count": usage_count,
            "usage_minutes": usage_minutes,
        }
        if team_id:
            common["team_name"] = row.get("name")
            common["seat_limit"] = row.get("seat_limit")
            common["included_seats"] = self.config.team_included_seats

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
                "reason": (
                    "Start a Team subscription to process videos in this team workspace."
                    if team_id
                    else "Choose a paid plan to process videos."
                ),
            }

        if plan == "team":
            minute_limit = self.config.team_minute_limit_for_seats(
                row.get("seat_limit")
            )
        else:
            minute_limit = int(getattr(self.config, PAID_PLAN_MINUTE_LIMIT_CONFIG[plan]))

        unlimited = minute_limit <= 0
        remaining_minutes = None if unlimited else max(minute_limit - usage_minutes, 0)
        team_exhausted = not unlimited and remaining_minutes <= 0

        member_minute_limit: int | None = None
        member_usage_minutes: int | None = None
        member_remaining_minutes: int | None = None
        member_limit_reached = False
        if team_id:
            member_minute_limit, _ = await self._load_member_limits(team_id, user_id)
            if member_minute_limit is not None:
                _, member_usage_minutes = await self._count_member_usage(
                    team_id, user_id, start, end
                )
                member_remaining_minutes = max(
                    member_minute_limit - member_usage_minutes, 0
                )
                member_limit_reached = member_remaining_minutes <= 0

        can_create = (not team_exhausted) and (not member_limit_reached)
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

        exhausted_reason = (
            "Your team has used this month's pooled video minutes. Add seats to increase the monthly allowance."
            if team_id
            else "You've used this month's video minutes."
        )
        member_exhausted_reason = (
            "Video generation is currently paused for your seat by a team admin."
            if member_minute_limit == 0
            else (
                f"You've reached your monthly team limit of {member_minute_limit} minutes "
                f"({member_usage_minutes} min used). Ask a team admin to raise your limit."
            )
        )
        reason: str | None = None
        if team_exhausted:
            reason = exhausted_reason
        elif member_limit_reached:
            reason = member_exhausted_reason

        result_summary: dict[str, Any] = {
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
            "upgrade_required": team_exhausted,
            "reason": reason,
        }
        if team_id:
            result_summary["member_minute_limit"] = member_minute_limit
            result_summary["member_usage_minutes"] = member_usage_minutes
            result_summary["member_remaining_minutes"] = member_remaining_minutes
            result_summary["member_limit_reached"] = member_limit_reached
        return result_summary

    async def assert_can_create_task(
        self,
        user_id: str,
        duration_seconds: float | None = None,
        *,
        team_id: str | None = None,
    ) -> dict[str, Any]:
        """Require an active plan with room left, and for a known source length,
        enough minutes to cover the whole video."""
        summary = (
            await self.get_usage_summary(user_id, team_id=team_id)
            if team_id
            else await self.get_usage_summary(user_id)
        )
        if not summary.get("can_create_task"):
            raise BillingLimitExceeded(summary)

        needed = source_minutes(duration_seconds)
        remaining = summary.get("remaining_minutes")
        if remaining is not None and needed > remaining:
            reason = (
                f"This video is {needed} minutes long and your team has "
                f"{remaining} pooled minutes left this month."
                if team_id
                else f"This video is {needed} minutes long and you have "
                f"{remaining} minutes left this month."
            )
            raise BillingLimitExceeded(
                {
                    **summary,
                    "upgrade_required": True,
                    "required_minutes": needed,
                    "reason": reason,
                }
            )
        member_remaining = summary.get("member_remaining_minutes")
        member_limit = summary.get("member_minute_limit")
        if team_id and member_remaining is not None and needed > member_remaining:
            reason = (
                f"This video is {needed} minutes long and you have "
                f"{member_remaining} minutes left in your monthly team limit "
                f"({member_limit} min). Ask a team admin to raise your limit."
            )
            raise BillingLimitExceeded(
                {
                    **summary,
                    "upgrade_required": False,
                    "member_limit_reached": True,
                    "required_minutes": needed,
                    "reason": reason,
                }
            )
        return summary

    async def assert_can_resume_task(
        self,
        user_id: str,
        task_id: str,
        *,
        require_active_plan: bool = True,
        team_id: str | None = None,
    ) -> dict[str, Any]:
        """Reuse an active charge or re-charge a refunded retry after checking room.

        The caller holds the allowance lock through this check and commit.
        Workers may run an already-paid queued job after its plan lapses. Explicit
        resumes and all new/reinstated charges still require paid access.
        """
        summary = (
            await self.get_usage_summary(user_id, team_id=team_id)
            if team_id
            else await self.get_usage_summary(user_id)
        )
        if not self.config.monetization_enabled:
            return summary
        paid_access = grants_paid_access(
            summary.get("plan"),
            summary.get("subscription_status"),
            summary.get("subscription_provider"),
        )
        if team_id and summary.get("plan") != "team":
            paid_access = False

        if team_id:
            result = await self.db.execute(
                text("""
                    SELECT refunded_at, source_duration_seconds FROM minute_usage_events
                    WHERE task_id = :task_id AND team_id = :team_id
                """),
                {"task_id": task_id, "team_id": team_id},
            )
        else:
            result = await self.db.execute(
                text("""
                    SELECT refunded_at, source_duration_seconds FROM minute_usage_events
                    WHERE task_id = :task_id AND user_id = :user_id AND team_id IS NULL
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

        if team_id:
            result = await self.db.execute(
                text("""
                    SELECT t.source_duration_seconds, s.url AS source_url, t.source_metadata
                    FROM tasks t LEFT JOIN sources s ON s.id = t.source_id
                    WHERE t.id = :task_id AND t.team_id = :team_id
                """),
                {"task_id": task_id, "team_id": team_id},
            )
        else:
            result = await self.db.execute(
                text("""
                    SELECT t.source_duration_seconds, s.url AS source_url, t.source_metadata
                    FROM tasks t LEFT JOIN sources s ON s.id = t.source_id
                    WHERE t.id = :task_id AND t.user_id = :user_id AND t.team_id IS NULL
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
        if team_id:
            await self.assert_can_create_task(user_id, seconds, team_id=team_id)
        else:
            await self.assert_can_create_task(user_id, seconds)
        try:
            if team_id:
                result = await self.db.execute(
                    text("""
                        UPDATE tasks SET source_duration_seconds = :seconds
                        WHERE id = :task_id AND team_id = :team_id
                          AND (:explicit_resume OR status IN ('queued', 'processing'))
                    """),
                    {
                        "seconds": seconds,
                        "task_id": task_id,
                        "team_id": team_id,
                        "explicit_resume": require_active_plan,
                    },
                )
            else:
                result = await self.db.execute(
                    text("""
                        UPDATE tasks SET source_duration_seconds = :seconds
                        WHERE id = :task_id AND user_id = :user_id AND team_id IS NULL
                          AND (:explicit_resume OR status IN ('queued', 'processing'))
                    """),
                    {
                        "seconds": seconds,
                        "task_id": task_id,
                        "user_id": user_id,
                        "explicit_resume": require_active_plan,
                    },
                )
            if result.rowcount == 0:
                # Cancellation may win while a legacy worker measures duration.
                # Do not re-charge a task that has already stopped.
                raise TaskCancelled()
            if event is not None:
                await reactivate_minute_usage(self.db, user_id, task_id, team_id=team_id)
            else:
                await record_minute_usage(self.db, user_id, task_id, seconds, team_id=team_id)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return summary
