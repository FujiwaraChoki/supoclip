"""Exercise monthly AI accounting against Postgres, including competing requests."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.services import ai_edit_allowance_service as quota
from src.services import ai_edit_service as ai
from src.editor_document import EditDocument
from tests.fixtures.factories import create_user


@pytest.mark.parametrize("plan,limit", [("pro", 10), ("scale", 100)])
async def test_concurrent_clips_share_one_durable_monthly_allowance(
    initialized_database, db_session, app, plan, limit
):
    owner = str(uuid4())
    await create_user(db_session, user_id=owner, plan=plan, subscription_status="active")
    app.state.config.monetization_enabled = True
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    slots = asyncio.Semaphore(16)

    async def submit():
        async with slots, sessions() as db:
            try:
                return await quota.AIEditAllowanceService(db, app.state.config).reserve(owner, uuid4().hex)
            except quota.AIEditLimitExceeded:
                return None

    results = await asyncio.gather(*(submit() for _ in range(limit + 8)))
    assert sum(result is not None for result in results) == limit
    # A new service/connection sees the same count (no Redis dependency).
    async with sessions() as db:
        summary = await quota.AIEditAllowanceService(db, app.state.config).summary(owner)
    assert summary["used"] == limit
    assert summary["remaining"] == 0 and not summary["allowed"]
    assert await db_session.scalar(text("SELECT COUNT(*) FROM ai_edit_usage_events WHERE user_id=:id"), {"id": owner}) == limit


async def test_upgrade_keeps_used_edits_and_inactive_plans_cannot_submit(db_session, app):
    owner = str(uuid4())
    await create_user(db_session, user_id=owner, plan="pro", subscription_status="active")
    app.state.config.monetization_enabled = True
    service = quota.AIEditAllowanceService(db_session, app.state.config)
    await service.reserve(owner, uuid4().hex)
    await db_session.execute(text("UPDATE users SET plan='scale' WHERE id=:id"), {"id": owner})
    await db_session.commit()
    assert (await service.summary(owner))["remaining"] == 99
    await db_session.execute(text("UPDATE users SET subscription_status='canceled' WHERE id=:id"), {"id": owner})
    await db_session.commit()
    with pytest.raises(quota.AIEditLimitExceeded):
        await service.reserve(owner, uuid4().hex)
    assert (await service.summary(owner))["used"] == 1


@pytest.mark.parametrize("annual", [False, True])
async def test_reset_windows_are_monthly_and_half_open(db_session, app, monkeypatch, annual):
    owner = str(uuid4())
    await create_user(db_session, user_id=owner, plan="pro", subscription_status="active")
    app.state.config.monetization_enabled = True
    start = datetime(2026, 2, 28, 10, tzinfo=timezone.utc)
    end = datetime(2026, 3, 31, 10, tzinfo=timezone.utc) if annual else datetime(2026, 3, 28, 10, tzinfo=timezone.utc)
    period_start = datetime(2026, 1, 31, 10, tzinfo=timezone.utc) if annual else start
    period_end = datetime(2027, 1, 31, 10, tzinfo=timezone.utc) if annual else end
    await db_session.execute(text("UPDATE users SET billing_period_start=:start,billing_period_end=:end WHERE id=:id"),
                             {"id": owner, "start": period_start, "end": period_end})
    for moment in [start-timedelta(microseconds=1), start, end-timedelta(microseconds=1), end]:
        await db_session.execute(text("INSERT INTO ai_edit_usage_events(job_id,user_id,created_at) VALUES (:job,:id,:at)"),
                                 {"job": uuid4().hex, "id": owner, "at": moment})
    await db_session.commit()
    monkeypatch.setattr(quota, "datetime", SimpleNamespace(now=lambda _: datetime(2026, 3, 5, tzinfo=timezone.utc)))
    summary = await quota.AIEditAllowanceService(db_session, app.state.config).summary(owner)
    assert summary["used"] == 2 and summary["remaining"] == 8
    assert summary["resetsAt"] == end.isoformat()


class Redis:
    def __init__(self): self.data = {}
    async def get(self, key): return self.data.get(key)
    async def set(self, key, value, **kwargs): self.data[key] = value
    async def delete(self, key): self.data.pop(key, None)


@pytest.mark.parametrize("outcome", ["failed", "expired", "completed"])
async def test_worker_refunds_failures_but_counts_completed_noops(db_session, app, monkeypatch, outcome):
    owner, job = str(uuid4()), uuid4().hex
    await create_user(db_session, user_id=owner, plan="pro", subscription_status="active")
    app.state.config.monetization_enabled = True
    service = quota.AIEditAllowanceService(db_session, app.state.config)
    assert (await service.reserve(owner, job))["remaining"] == 9
    redis = Redis()
    if outcome != "expired":
        doc = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 5}]}).model_dump()
        await redis.set(ai.request_key(job), json.dumps({"document": doc, "duration": 5, "instruction": "mute"}))
    monkeypatch.setattr(ai, "plan_edit", AsyncMock(
        side_effect=ai.AIEditError("Unavailable") if outcome == "failed" else None,
        return_value=ai.EditPlan(summary="No change needed.", operations=[]),
    ))
    await ai.ai_edit_job({"redis": redis}, job)
    result = json.loads(await redis.get(ai.result_key(job)))
    assert result["status"] == ("completed" if outcome == "completed" else "failed")
    assert (await service.summary(owner))["remaining"] == (9 if outcome == "completed" else 10)
    if outcome != "completed":
        await quota.refund_ai_edit_usage(job)
        assert (await service.summary(owner))["remaining"] == 10


async def test_self_hosting_does_not_meter_or_query_subscriptions():
    db = SimpleNamespace(execute=AsyncMock())
    service = quota.AIEditAllowanceService(db, SimpleNamespace(monetization_enabled=False))
    result = await service.reserve("local-owner", uuid4().hex)
    assert result["limit"] is None and result["remaining"] is None and result["allowed"]
    db.execute.assert_not_awaited()
