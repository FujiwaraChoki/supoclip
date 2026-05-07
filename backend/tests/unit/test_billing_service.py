from datetime import datetime, timezone

import pytest

from src.services.billing_service import BillingLimitExceeded, BillingService


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


def _billing_row(plan: str, status: str = "active"):
    return type(
        "BillingRow",
        (),
        {
            "plan": plan,
            "subscription_status": status,
            "billing_period_start": datetime.now(timezone.utc),
            "billing_period_end": datetime.now(timezone.utc),
            "trial_ends_at": None,
        },
    )()


def _count_row(total: int):
    return type("CountRow", (), {"total": total})()


@pytest.mark.asyncio
async def test_billing_summary_requires_paid_subscription():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("free", "inactive"), _count_row(2)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True
    service.config.free_plan_task_limit = 10

    summary = await service.get_usage_summary("user-1")

    assert summary["can_create_task"] is False
    assert summary["upgrade_required"] is True
    assert summary["usage_limit"] == 0
    assert summary["remaining"] == 0
    assert summary["reason"] == "Choose a paid plan to process videos."


@pytest.mark.asyncio
async def test_assert_can_create_task_raises_when_limit_exceeded():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("pro"), _count_row(50)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True
    service.config.pro_plan_task_limit = 50

    with pytest.raises(BillingLimitExceeded):
        await service.assert_can_create_task("user-1")


@pytest.mark.asyncio
async def test_pro_plan_uses_50_generation_limit():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("pro"), _count_row(49)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True
    service.config.pro_plan_task_limit = 50

    summary = await service.get_usage_summary("user-1")

    assert summary["plan"] == "pro"
    assert summary["usage_limit"] == 50
    assert summary["remaining"] == 1
    assert summary["can_create_task"] is True


@pytest.mark.asyncio
async def test_scale_plan_uses_300_generation_limit():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("scale"), _count_row(299)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True
    service.config.scale_plan_task_limit = 300

    summary = await service.get_usage_summary("user-1")

    assert summary["plan"] == "scale"
    assert summary["usage_limit"] == 300
    assert summary["remaining"] == 1
    assert summary["can_create_task"] is True


@pytest.mark.asyncio
async def test_inactive_paid_plan_requires_upgrade():
    service = BillingService(  # type: ignore[arg-type]
        _FakeSession([_billing_row("scale", "past_due"), _count_row(1)])
    )
    service.config.self_host = False
    service.config.monetization_enabled = True

    summary = await service.get_usage_summary("user-1")

    assert summary["plan"] == "scale"
    assert summary["can_create_task"] is False
    assert summary["upgrade_required"] is True
