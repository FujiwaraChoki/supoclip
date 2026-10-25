import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server


@pytest.mark.parametrize("preset", ["auto", "short", "medium", "long"])
def test_create_tool_forwards_clip_length(monkeypatch, preset):
    request = AsyncMock(return_value={"task_id": "task-1"})
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    asyncio.run(server.supoclip_create_clip_task(url="https://youtu.be/abcdefghijk", clip_length=preset))
    assert request.await_args.kwargs["json_body"]["clip_length"] == preset


def test_invalid_length_does_not_create_a_task(monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_create_clip_task(url="https://youtu.be/abcdefghijk", clip_length="custom"))
    assert "clip_length must be" in result
    request.assert_not_awaited()
