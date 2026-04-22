"""Tests for ``src/services/usage_service.py``.

The service is a thin async wrapper over ``TaskUsageRepository`` — tests
patch the repository class methods with ``AsyncMock`` so no DB is needed.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.services import usage_service as us
from src.usage_context import UsageEvent, UsageRecorder, current_usage


class TestStartRecording:
    def test_returns_fresh_recorder_and_activates_it(self):
        recorder, token = us.start_recording()
        try:
            assert isinstance(recorder, UsageRecorder)
            assert recorder.events == []
            assert current_usage.get() is recorder
        finally:
            current_usage.reset(token)
        # After reset, the recorder is no longer active
        assert current_usage.get() is not recorder


class TestPersistRecorder:
    @pytest.mark.asyncio
    async def test_skips_when_recorder_empty(self, monkeypatch):
        fake_insert = AsyncMock()
        monkeypatch.setattr(
            us.TaskUsageRepository, "insert_many", fake_insert
        )
        count = await us.persist_recorder(
            db=object(), recorder=UsageRecorder(), task_id="t1", user_id="u1"
        )
        assert count == 0
        fake_insert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_writes_all_events(self, monkeypatch):
        fake_insert = AsyncMock()
        monkeypatch.setattr(us.TaskUsageRepository, "insert_many", fake_insert)
        recorder = UsageRecorder(events=[
            UsageEvent(service="llm", model="a", input_tokens=100, output_tokens=50, cost_usd=0.01),
            UsageEvent(service="assemblyai", audio_seconds=30.0, cost_usd=0.003),
        ])
        count = await us.persist_recorder(
            db="db", recorder=recorder, task_id="t42", user_id="u7"
        )
        assert count == 2
        fake_insert.assert_awaited_once()
        call_args = fake_insert.await_args
        # positional: (db, task_id, user_id, payload)
        assert call_args.args[1] == "t42"
        assert call_args.args[2] == "u7"
        payload = call_args.args[3]
        assert len(payload) == 2
        # Converted to dicts (dataclass asdict)
        assert payload[0]["service"] == "llm"
        assert payload[0]["input_tokens"] == 100


class TestMonthlySummary:
    @pytest.mark.asyncio
    async def test_sends_month_start_and_decorates_window(self, monkeypatch):
        fake_summary = AsyncMock(return_value={"cost_usd": 1.23})
        monkeypatch.setattr(us.TaskUsageRepository, "summary_since", fake_summary)

        frozen = datetime(2026, 4, 15, 12, 30, 0, tzinfo=timezone.utc)
        result = await us.monthly_summary(db="db", now=frozen)
        assert result["window"] == "month_to_date"
        assert result["window_start"].startswith("2026-04-01")
        assert result["window_end"].startswith("2026-04-15")
        assert result["cost_usd"] == 1.23
        # summary_since called with month-start datetime (day=1, hour=0)
        start_arg = fake_summary.await_args.args[1]
        assert start_arg.day == 1
        assert start_arg.hour == 0
        assert start_arg.minute == 0


class TestTrailingSummary:
    @pytest.mark.asyncio
    async def test_subtracts_days(self, monkeypatch):
        fake = AsyncMock(return_value={"cost_usd": 0})
        monkeypatch.setattr(us.TaskUsageRepository, "summary_since", fake)

        frozen = datetime(2026, 4, 20, tzinfo=timezone.utc)
        result = await us.trailing_summary(db="db", days=7, now=frozen)
        assert result["window"] == "last_7_days"
        assert result["window_start"].startswith("2026-04-13")
        since = fake.await_args.args[1]
        assert since == frozen - timedelta(days=7)


class TestRecentTasks:
    @pytest.mark.asyncio
    async def test_uses_default_days_and_limit(self, monkeypatch):
        fake = AsyncMock(return_value=[{"task_id": "a"}])
        monkeypatch.setattr(us.TaskUsageRepository, "recent_tasks", fake)

        frozen = datetime(2026, 4, 20, tzinfo=timezone.utc)
        result = await us.recent_tasks(db="db", now=frozen)
        assert result == [{"task_id": "a"}]
        # call_args: db, since, limit=limit
        call = fake.await_args
        assert call.args[1] == frozen - timedelta(days=30)
        assert call.kwargs["limit"] == 20

    @pytest.mark.asyncio
    async def test_honors_custom_params(self, monkeypatch):
        fake = AsyncMock(return_value=[])
        monkeypatch.setattr(us.TaskUsageRepository, "recent_tasks", fake)
        frozen = datetime(2026, 4, 20, tzinfo=timezone.utc)
        await us.recent_tasks(db="db", days=3, limit=5, now=frozen)
        call = fake.await_args
        assert call.args[1] == frozen - timedelta(days=3)
        assert call.kwargs["limit"] == 5


class TestTaskBreakdown:
    @pytest.mark.asyncio
    async def test_aggregates_totals(self, monkeypatch):
        events = [
            {"cost_usd": 0.1, "audio_seconds": 10, "input_tokens": 100, "output_tokens": 50},
            {"cost_usd": 0.2, "audio_seconds": 20, "input_tokens": 200, "output_tokens": 150},
        ]
        fake = AsyncMock(return_value=events)
        monkeypatch.setattr(us.TaskUsageRepository, "list_events_for_task", fake)

        result = await us.task_breakdown(db="db", task_id="t1")
        assert result["task_id"] == "t1"
        assert result["events"] is events
        totals = result["totals"]
        assert totals["cost_usd"] == pytest.approx(0.3)
        assert totals["audio_seconds"] == 30
        assert totals["input_tokens"] == 300
        assert totals["output_tokens"] == 200
        assert totals["event_count"] == 2

    @pytest.mark.asyncio
    async def test_empty_events_returns_zero_totals(self, monkeypatch):
        monkeypatch.setattr(
            us.TaskUsageRepository, "list_events_for_task", AsyncMock(return_value=[])
        )
        result = await us.task_breakdown(db="db", task_id="t2")
        assert result["totals"]["event_count"] == 0
        assert result["totals"]["cost_usd"] == 0
