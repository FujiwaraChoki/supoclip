import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server

CHANNEL = "https://www.twitch.tv/somestreamer"


def _create(monkeypatch, **kwargs):
    request = AsyncMock(return_value={"task_id": "task-1", "live_session_id": "session-1"})
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_create_clip_task(url=CHANNEL, **kwargs))
    return request, result


def test_live_minutes_clip_the_newest_part(monkeypatch):
    request, _ = _create(monkeypatch, live_minutes=45)
    assert request.await_args.kwargs["json_body"]["source"] == {
        "url": CHANNEL, "live": {"window_seconds": 2700, "follow": False},
    }


def test_keep_clipping_defaults_to_the_last_30_minutes(monkeypatch):
    request, result = _create(monkeypatch, keep_clipping=True)
    assert request.await_args.kwargs["json_body"]["source"]["live"] == {"window_seconds": 1800, "follow": True}
    assert "session-1" in result


def test_a_live_link_without_a_choice_sends_no_live_field(monkeypatch):
    request, _ = _create(monkeypatch)
    assert request.await_args.kwargs["json_body"]["source"] == {"url": CHANNEL}


def test_live_and_a_part_are_not_mixed(monkeypatch):
    request, result = _create(monkeypatch, live_minutes=30, start_seconds=10)
    assert "not both" in result
    request.assert_not_awaited()
