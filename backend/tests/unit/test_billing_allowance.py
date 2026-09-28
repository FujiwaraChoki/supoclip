"""Exercise the actual allowance SQL over task and clip rows."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from src.config import Config
from src.services.billing_service import BillingLimitExceeded, BillingService


PERIOD_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 10, 1, tzinfo=timezone.utc)
CREATED_AT = PERIOD_START + timedelta(days=1)


class _SqlSession:
    """Run the production query on SQLite without async-driver dependencies."""

    def __init__(self, connection):
        self.connection = connection

    async def execute(self, statement, parameters):
        return self.connection.execute(statement, parameters)


@pytest.fixture
def allowance_db():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id TEXT, "
            "status TEXT, created_at TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE generated_clips (id TEXT PRIMARY KEY, task_id TEXT)"
        ))
        yield connection
    engine.dispose()


def _insert_task(connection, task_id="task-1", status="queued", user_id="user-1", created_at=CREATED_AT):
    connection.execute(
        text("INSERT INTO tasks VALUES (:id, :user_id, :status, :created_at)"),
        {"id": task_id, "user_id": user_id, "status": status, "created_at": created_at},
    )


def _insert_clip(connection, task_id, clip_id):
    connection.execute(
        text("INSERT INTO generated_clips VALUES (:id, :task_id)"),
        {"id": clip_id, "task_id": task_id},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "failed", "cancelled", "canceled"])
@pytest.mark.parametrize("clip_count", [0, 1, 3])
async def test_unsuccessful_jobs_only_count_when_they_delivered_clips(allowance_db, status, clip_count):
    _insert_task(allowance_db, status=status)
    for index in range(clip_count):
        _insert_clip(allowance_db, "task-1", f"clip-{index}")

    service = BillingService(_SqlSession(allowance_db))
    assert await service._count_tasks("user-1", PERIOD_START, PERIOD_END) == int(clip_count > 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "queued", "processing", "completed", "unknown", None])
async def test_active_completed_and_unknown_jobs_keep_their_slot(allowance_db, status):
    _insert_task(allowance_db, status=status)
    service = BillingService(_SqlSession(allowance_db))
    assert await service._count_tasks("user-1", PERIOD_START, PERIOD_END) == 1


@pytest.mark.asyncio
async def test_allowance_is_scoped_to_user_period_and_matching_output(allowance_db):
    _insert_task(allowance_db, "failed", status="error")
    _insert_task(allowance_db, "other-user", user_id="other")
    _insert_clip(allowance_db, "other-user", "other-clip")
    _insert_task(allowance_db, "before", created_at=PERIOD_START - timedelta(seconds=1))
    _insert_task(allowance_db, "after", created_at=PERIOD_END + timedelta(seconds=1))
    _insert_task(allowance_db, "start", created_at=PERIOD_START)
    _insert_task(allowance_db, "end", created_at=PERIOD_END)
    service = BillingService(_SqlSession(allowance_db))
    assert await service._count_tasks("user-1", PERIOD_START, PERIOD_END) == 2


@pytest.mark.asyncio
async def test_failure_releases_slot_and_resumed_job_reserves_it_again(allowance_db, monkeypatch):
    config = Config()
    config.monetization_enabled = True
    config.pro_plan_task_limit = 1
    service = BillingService(_SqlSession(allowance_db), config)

    async def billing_row(_user_id):
        return {
            "plan": "pro", "subscription_status": "active",
            "billing_period_start": PERIOD_START, "billing_period_end": PERIOD_END,
        }

    monkeypatch.setattr(service, "_load_user_billing_row", billing_row)
    _insert_task(allowance_db)

    for status, expected_count in [("queued", 1), ("error", 0), ("queued", 1), ("cancelled", 0)]:
        allowance_db.execute(text("UPDATE tasks SET status = :status"), {"status": status})
        summary = await service.get_usage_summary("user-1")
        assert summary["usage_count"] == expected_count
        assert summary["remaining"] == 1 - expected_count
        if expected_count:
            with pytest.raises(BillingLimitExceeded):
                await service.assert_can_create_task("user-1")
        else:
            assert (await service.assert_can_create_task("user-1"))["can_create_task"]

    _insert_clip(allowance_db, "task-1", "partial-output")
    summary = await service.get_usage_summary("user-1")
    assert summary["usage_count"] == 1
    assert summary["can_create_task"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "cancelled"])
@pytest.mark.parametrize("has_output", [False, True])
async def test_full_quota_resume_requires_an_already_counted_task(allowance_db, monkeypatch, status, has_output):
    config = Config()
    config.monetization_enabled = True
    config.pro_plan_task_limit = 1
    service = BillingService(_SqlSession(allowance_db), config)

    async def billing_row(_user_id):
        return {
            "plan": "pro", "subscription_status": "active",
            "billing_period_start": PERIOD_START, "billing_period_end": PERIOD_END,
        }

    monkeypatch.setattr(service, "_load_user_billing_row", billing_row)
    _insert_task(allowance_db, "resume", status=status)
    if has_output:
        _insert_clip(allowance_db, "resume", "partial")
        assert (await service.assert_can_resume_task("user-1", "resume"))["usage_count"] == 1
    else:
        _insert_task(allowance_db, "uses-last-slot", status="queued")
        with pytest.raises(BillingLimitExceeded):
            await service.assert_can_resume_task("user-1", "resume")


@pytest.mark.asyncio
async def test_refunded_failure_can_resume_with_available_slot(allowance_db, monkeypatch):
    config = Config()
    config.monetization_enabled = True
    config.pro_plan_task_limit = 1
    service = BillingService(_SqlSession(allowance_db), config)

    async def billing_row(_user_id):
        return {
            "plan": "pro", "subscription_status": "active",
            "billing_period_start": PERIOD_START, "billing_period_end": PERIOD_END,
        }

    monkeypatch.setattr(service, "_load_user_billing_row", billing_row)
    _insert_task(allowance_db, "resume", status="error")
    assert (await service.assert_can_resume_task("user-1", "resume"))["remaining"] == 1


@pytest.mark.asyncio
async def test_inactive_subscription_cannot_resume_even_with_partial_output(allowance_db, monkeypatch):
    config = Config()
    config.monetization_enabled = True
    service = BillingService(_SqlSession(allowance_db), config)

    async def billing_row(_user_id):
        return {
            "plan": "pro", "subscription_status": "canceled",
            "billing_period_start": PERIOD_START, "billing_period_end": PERIOD_END,
        }

    monkeypatch.setattr(service, "_load_user_billing_row", billing_row)
    _insert_task(allowance_db, "resume", status="error")
    _insert_clip(allowance_db, "resume", "partial")
    with pytest.raises(BillingLimitExceeded):
        await service.assert_can_resume_task("user-1", "resume")


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,created_at", [
    ("other-user", CREATED_AT),
    ("user-1", PERIOD_START - timedelta(days=1)),
])
async def test_resume_cannot_borrow_other_user_or_previous_period_output(allowance_db, monkeypatch, user_id, created_at):
    config = Config()
    config.monetization_enabled = True
    config.pro_plan_task_limit = 1
    service = BillingService(_SqlSession(allowance_db), config)

    async def billing_row(_user_id):
        return {
            "plan": "pro", "subscription_status": "active",
            "billing_period_start": PERIOD_START, "billing_period_end": PERIOD_END,
        }

    monkeypatch.setattr(service, "_load_user_billing_row", billing_row)
    _insert_task(allowance_db, "uses-last-slot")
    _insert_task(allowance_db, "resume", status="error", user_id=user_id, created_at=created_at)
    _insert_clip(allowance_db, "resume", "partial")
    with pytest.raises(BillingLimitExceeded):
        await service.assert_can_resume_task("user-1", "resume")
