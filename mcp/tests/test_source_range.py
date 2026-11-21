import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server

URL = "https://youtu.be/abcdefghijk"


def _create(monkeypatch, **kwargs):
    request = AsyncMock(return_value={"task_id": "task-1"})
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_create_clip_task(url=URL, **kwargs))
    return request, result


def test_part_of_a_video_is_sent_on_the_source(monkeypatch):
    request, _ = _create(monkeypatch, start_seconds=3600, end_seconds=10800)
    source = request.await_args.kwargs["json_body"]["source"]
    assert source == {"url": URL, "start_seconds": 3600, "end_seconds": 10800}


def test_whole_video_sends_no_range(monkeypatch):
    request, _ = _create(monkeypatch)
    assert request.await_args.kwargs["json_body"]["source"] == {"url": URL}


def test_open_ended_part_sends_only_the_given_end(monkeypatch):
    request, _ = _create(monkeypatch, start_seconds=600)
    assert request.await_args.kwargs["json_body"]["source"] == {"url": URL, "start_seconds": 600}


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"start_seconds": -1}, "start_seconds must be"),
        ({"start_seconds": 600, "end_seconds": 600}, "end_seconds must be after"),
    ],
)
def test_invalid_part_does_not_create_a_task(monkeypatch, kwargs, message):
    request, result = _create(monkeypatch, **kwargs)
    assert message in result
    request.assert_not_awaited()
