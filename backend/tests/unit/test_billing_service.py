from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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


@pytest.mark.asyncio
async def test_billing_summary_requires_paid_subscription():
    row = type(
        "BillingRow",
        (),
        {
            "plan": "free",
            "subscription_status": "inactive",
            "billing_period_start": datetime.now(timezone.utc),
            "billing_period_end": datetime.now(timezone.utc),
            "trial_ends_at": None,
        },
    )()
    count_row = type("CountRow", (), {"total": 2})()
    service = BillingService(_FakeSession([row, count_row]))  # type: ignore[arg-type]
    service.config.self_host = False
    service.config.monetization_enabled = True

    summary = await service.get_usage_summary("user-1")

    assert summary["can_create_task"] is False
    assert summary["upgrade_required"] is True


@pytest.mark.asyncio
async def test_assert_can_create_task_raises_when_limit_exceeded():
    row = type(
        "BillingRow",
        (),
        {
            "plan": "pro",
            "subscription_status": "active",
            "billing_period_start": datetime.now(timezone.utc),
            "billing_period_end": datetime.now(timezone.utc),
            "trial_ends_at": None,
        },
    )()
    count_row = type("CountRow", (), {"total": 3})()
    service = BillingService(_FakeSession([row, count_row]))  # type: ignore[arg-type]
    service.config.self_host = False
    service.config.monetization_enabled = True
    service.config.pro_plan_task_limit = 2

    with pytest.raises(BillingLimitExceeded):
      await service.assert_can_create_task("user-1")
