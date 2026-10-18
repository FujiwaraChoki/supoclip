from datetime import datetime, timezone

import pytest

from src.services.billing_service import BillingLimitExceeded, BillingService, reduced_media_limits


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self.rows.pop(0))


def _billing_row(
    plan: str,
    status: str = "active",
    provider: str | None = None,
    cancel_at: datetime | None = None,
    legacy_hd: bool = False,
):
    return type(
        "BillingRow",
        (),
        {
            "plan": plan,
            "subscription_status": status,
            "subscription_provider": provider,
            "billing_period_start": datetime.now(timezone.utc),
            "billing_period_end": datetime.now(timezone.utc),
            "trial_ends_at": None,
            "subscription_cancel_at": cancel_at,
            "legacy_hd": legacy_hd,
        },
    )()


def _usage_row(total: int, minutes: int = 0):
    return type("UsageRow", (), {"total": total, "minutes": minutes})()


@pytest.mark.asyncio
async def test_billing_summary_requires_paid_subscription():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("free", "inactive"), _usage_row(2)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True

    summary = await service.get_usage_summary("user-1")

    assert summary["can_create_task"] is False
    assert summary["upgrade_required"] is True
    assert summary["usage_limit"] == 0
    assert summary["remaining"] == 0
    assert summary["reason"] == "Choose a paid plan to process videos."


@pytest.mark.asyncio
async def test_assert_can_create_task_raises_when_minutes_used_up():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("pro"), _usage_row(4, 600)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True
    service.config.pro_plan_minute_limit = 600

    with pytest.raises(BillingLimitExceeded):
        await service.assert_can_create_task("user-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("plan,limit", [("pro", 600), ("scale", 1500)])
async def test_paid_plans_meter_source_minutes(plan, limit):
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row(plan), _usage_row(3, limit - 1)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True
    service.config.pro_plan_minute_limit = 600
    service.config.scale_plan_minute_limit = 1500

    summary = await service.get_usage_summary("user-1")

    assert summary["plan"] == plan
    assert summary["usage_count"] == 3
    assert summary["usage_limit"] is None
    assert summary["usage_minutes"] == limit - 1
    assert summary["minute_limit"] == limit
    assert summary["remaining_minutes"] == 1
    assert summary["can_create_task"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan,legacy_hd,short_side,can_schedule",
    [
        ("pro", False, 720, False),
        ("pro", True, 1080, False),
        ("scale", False, 1080, True),
        ("scale", True, 1080, True),
    ],
)
async def test_plan_entitlements(plan, legacy_hd, short_side, can_schedule):
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row(plan, legacy_hd=legacy_hd), _usage_row(0, 0)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True

    summary = await service.get_usage_summary("user-1")

    assert summary["output_short_side"] == short_side
    assert summary["legacy_hd"] is (plan == "pro" and legacy_hd)
    assert summary["can_schedule_posts"] is can_schedule


@pytest.mark.asyncio
async def test_lapsed_legacy_subscriber_loses_full_hd():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("pro", "canceled", legacy_hd=True), _usage_row(0, 0)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True

    summary = await service.get_usage_summary("user-1")

    assert summary["output_short_side"] == 720
    assert summary["legacy_hd"] is False


@pytest.mark.parametrize(
    "now,expected_start,expected_end",
    [
        # Monthly anniversaries of a Jan 31 annual start clamp to short months.
        (datetime(2026, 2, 10, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc), datetime(2026, 2, 28, tzinfo=timezone.utc)),
        (datetime(2026, 3, 30, tzinfo=timezone.utc), datetime(2026, 2, 28, tzinfo=timezone.utc), datetime(2026, 3, 31, tzinfo=timezone.utc)),
        (datetime(2026, 3, 31, tzinfo=timezone.utc), datetime(2026, 3, 31, tzinfo=timezone.utc), datetime(2026, 4, 30, tzinfo=timezone.utc)),
        (datetime(2027, 1, 20, tzinfo=timezone.utc), datetime(2026, 12, 31, tzinfo=timezone.utc), datetime(2027, 1, 31, tzinfo=timezone.utc)),
    ],
)
def test_annual_period_resets_allowance_monthly(now, expected_start, expected_end):
    period_start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    period_end = datetime(2027, 1, 31, tzinfo=timezone.utc)

    assert BillingService._usage_window(period_start, period_end, now) == (expected_start, expected_end)


def test_monthly_period_is_used_as_is():
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 12, tzinfo=timezone.utc)

    assert BillingService._usage_window(start, end, datetime(2026, 10, 20, tzinfo=timezone.utc)) == (start, end)


@pytest.mark.asyncio
async def test_inactive_paid_plan_requires_upgrade():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("scale", "past_due"), _usage_row(1)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True

    summary = await service.get_usage_summary("user-1")

    assert summary["plan"] == "scale"
    assert summary["can_create_task"] is False
    assert summary["upgrade_required"] is True


@pytest.mark.asyncio
async def test_billing_summary_exposes_scheduled_cancellation():
    cancel_at = datetime(2026, 10, 21, tzinfo=timezone.utc)
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("pro", cancel_at=cancel_at), _usage_row(1)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True

    summary = await service.get_usage_summary("user-1")

    assert summary["plan"] == "pro"
    assert summary["can_create_task"] is True
    assert summary["cancel_at"] == cancel_at


@pytest.mark.asyncio
@pytest.mark.parametrize("plan,status,expected", [("pro", "active", 5400), ("scale", "trialing", 10800), ("scale", "inactive", 5400)])
async def test_billing_summary_exposes_enforced_duration_limit(plan, status, expected):
    service = BillingService(_FakeSession([_billing_row(plan, status), _usage_row(0)]))
    service.config.monetization_enabled = True
    service.config.max_video_duration = 5400
    service.config.pro_youtube_max_video_duration = 5400
    service.config.scale_youtube_max_video_duration = 10800
    summary = await service.get_usage_summary("user-1")
    assert summary["max_youtube_duration_seconds"] == expected
    assert summary["max_upload_duration_seconds"] == 5400


@pytest.mark.parametrize(
    "summary,expected",
    [
        ({"output_short_side": 720}, ("720", 720)),
        ({"output_short_side": 1080}, (None, None)),
        ({"output_short_side": None}, (None, None)),  # self-hosted
        ({}, (None, None)),
    ],
)
def test_reduced_media_limits(summary, expected):
    assert reduced_media_limits(summary) == expected


def test_reduced_quality_youtube_sources_get_their_own_cache_entry():
    import hashlib

    from src.services.task_service import TaskService
    from src.ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION

    url = "https://youtu.be/abcdefghijk"
    full = TaskService._build_cache_key(url, "youtube", "fast")
    # Full-quality keys are unchanged, so existing cache entries stay valid.
    assert full == hashlib.sha256(
        f"youtube|fast|{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url}".encode()
    ).hexdigest()
    assert TaskService._build_cache_key(url, "youtube", "fast", "720") != full
    # Uploads are the same file whatever the plan.
    assert TaskService._build_cache_key("upload://a.mp4", "video_url", "fast", "720") == (
        TaskService._build_cache_key("upload://a.mp4", "video_url", "fast")
    )
