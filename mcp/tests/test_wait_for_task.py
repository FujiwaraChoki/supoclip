import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server


@pytest.mark.parametrize("stall_request", [False, True], ids=["between-polls", "during-request"])
def test_wait_for_task_honors_timeout(monkeypatch, stall_request):
    async def request_status(*args):
        if stall_request:
            await asyncio.Event().wait()
        return {"status": "processing", "progress": 42}

    request = AsyncMock(side_effect=request_status)
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))

    async def wait_for_task():
        return await asyncio.wait_for(
            server.supoclip_wait_for_task("task-1", timeout_seconds=0.05, poll_interval_seconds=60),
            timeout=1,
        )

    result = json.loads(asyncio.run(wait_for_task()))

    request.assert_awaited_once_with("GET", "/tasks/task-1")
    assert result["timed_out"] is True
    assert result["task"] == ({} if stall_request else {"status": "processing", "progress": 42})
    assert result["status"] == ("unknown" if stall_request else "processing")
    assert result["progress"] == (0 if stall_request else 42)


def test_wait_for_task_returns_terminal_status_when_progress_notification_stalls(monkeypatch):
    task = {"status": "completed", "progress": 100, "clips": []}
    request = AsyncMock(return_value=task)
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))

    async def report_progress(**kwargs):
        await asyncio.Event().wait()

    async def wait_for_task():
        return await asyncio.wait_for(
            server.supoclip_wait_for_task(
                "task-1", timeout_seconds=0.05, ctx=SimpleNamespace(report_progress=report_progress)
            ),
            timeout=1,
        )

    result = json.loads(asyncio.run(wait_for_task()))

    assert result == {"status": "completed", "progress": 100, "timed_out": False, "task": task}
