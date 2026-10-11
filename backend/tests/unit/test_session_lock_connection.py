from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.repositories import session_lock_connection as lock_module
from src.repositories.generation_allowance_guard import generation_allowance_guard


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_turn_successful_admission_into_retryable_error(monkeypatch):
    connection = SimpleNamespace(
        execute=AsyncMock(), commit=AsyncMock(),
        invalidate=AsyncMock(side_effect=RuntimeError("Connection already disconnected")),
        close=AsyncMock(),
    )
    engine = SimpleNamespace(connect=AsyncMock(return_value=connection), dispose=AsyncMock())
    monkeypatch.setattr(lock_module, "create_async_engine", lambda *_args, **_kwargs: engine)
    db = SimpleNamespace(bind=SimpleNamespace(url="test-url"), close=AsyncMock())
    async with generation_allowance_guard(db, "user-1", enabled=True):
        result = {"task_id": "successfully-committed-and-enqueued"}
    assert result["task_id"] == "successfully-committed-and-enqueued"
    connection.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()
