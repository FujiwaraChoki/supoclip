import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.routes import tasks
from src.workers.progress import ProgressTracker


@pytest.mark.asyncio
async def test_subscription_closes_when_consumer_stops():
    pubsub = SimpleNamespace(subscribe=AsyncMock(), aclose=AsyncMock())

    async def messages():
        yield {"type": "message", "data": json.dumps({"status": "processing"})}

    pubsub.listen = messages
    updates = ProgressTracker.subscribe_to_progress(SimpleNamespace(pubsub=lambda: pubsub), "task")
    assert await anext(updates) == {"status": "processing"}
    await updates.aclose()
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", ["processing", "cancelled"])
async def test_stream_closes_subscription_before_client(monkeypatch, initial_status):
    events = []

    @asynccontextmanager
    async def database():
        yield object()

    repo = SimpleNamespace(get_task_by_id=AsyncMock(return_value={"user_id": "user", "status": initial_status}))
    monkeypatch.setattr(tasks, "AsyncSessionLocal", database)
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user"))
    monkeypatch.setattr(tasks, "TaskService", lambda db: SimpleNamespace(task_repo=repo))

    async def close_client():
        events.append("client")

    monkeypatch.setattr(tasks.redis, "Redis", lambda **kwargs: SimpleNamespace(aclose=close_client))

    async def updates(redis, task_id):
        try:
            yield {"status": "processing", "progress": 10}
        finally:
            events.append("subscription")

    monkeypatch.setattr(tasks.ProgressTracker, "subscribe_to_progress", updates)
    response = await tasks.get_task_progress_sse("task", object())
    stream = response.body_iterator
    assert (await anext(stream))["event"] == "status"
    if initial_status == "cancelled":
        assert (await anext(stream))["event"] == "close"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert events == []
    else:
        assert (await anext(stream))["event"] == "progress"
        await stream.aclose()
        assert events == ["subscription", "client"]
