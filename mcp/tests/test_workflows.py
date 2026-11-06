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


def test_highlights_body_has_no_workflow_fields(monkeypatch):
    request, _ = _create(monkeypatch)
    body = request.await_args.kwargs["json_body"]
    assert "workflow" not in body
    assert "workflow_options" not in body
    assert "instructions" not in body


def test_explicit_highlights_is_the_same_request(monkeypatch):
    plain, _ = _create(monkeypatch)
    explicit, _ = _create(monkeypatch, workflow="highlights")
    assert plain.await_args.kwargs["json_body"] == explicit.await_args.kwargs["json_body"]


def test_series_with_part_count(monkeypatch):
    request, _ = _create(monkeypatch, workflow="series", part_count=5, clip_length="medium")
    body = request.await_args.kwargs["json_body"]
    assert body["workflow"] == "series"
    assert body["workflow_options"] == {"part_count": 5}
    assert body["clip_length"] == "medium"


def test_series_auto_sends_no_options(monkeypatch):
    request, _ = _create(monkeypatch, workflow="series")
    body = request.await_args.kwargs["json_body"]
    assert body["workflow"] == "series"
    assert "workflow_options" not in body


def test_topic_is_normalized(monkeypatch):
    request, _ = _create(monkeypatch, workflow="topic", topic="  building​ a\n business  ")
    assert request.await_args.kwargs["json_body"]["workflow_options"] == {"topic": "building a business"}


@pytest.mark.parametrize("topic", ["钱", "猫", "AI", "5G"])
def test_short_topics_that_name_something(monkeypatch, topic):
    request, _ = _create(monkeypatch, workflow="topic", topic=topic)
    assert request.await_args.kwargs["json_body"]["workflow_options"] == {"topic": topic}


@pytest.mark.parametrize("workflow", ["recap", "trailer"])
def test_single_output_workflows(monkeypatch, workflow):
    request, _ = _create(monkeypatch, workflow=workflow, clip_length="long")
    body = request.await_args.kwargs["json_body"]
    assert body["workflow"] == workflow
    assert "workflow_options" not in body


def test_instructions_go_top_level_for_any_workflow(monkeypatch):
    request, _ = _create(monkeypatch, instructions="  Skip the sponsor read.\n\n\n\nKeep it funny.  ")
    body = request.await_args.kwargs["json_body"]
    assert body["instructions"] == "Skip the sponsor read.\n\nKeep it funny."
    assert "workflow" not in body


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"workflow": "podcast"}, "workflow must be"),
        ({"workflow": "topic"}, "topic is required"),
        ({"workflow": "topic", "topic": "!!"}, "topic is required"),
        ({"workflow": "topic", "topic": "a"}, "topic is required"),
        ({"workflow": "topic", "topic": "x" * 121}, "at most 120"),
        ({"workflow": "highlights", "topic": "pricing"}, "only used with workflow='topic'"),
        ({"workflow": "series", "part_count": 1}, "0 (auto) or 2 to 10"),
        ({"workflow": "series", "part_count": 11}, "0 (auto) or 2 to 10"),
        ({"workflow": "recap", "part_count": 3}, "only used with workflow='series'"),
        ({"instructions": "x" * 501}, "at most 500"),
    ],
)
def test_invalid_workflow_requests_do_not_create_a_task(monkeypatch, kwargs, message):
    request, result = _create(monkeypatch, **kwargs)
    assert message in result
    request.assert_not_awaited()


def test_instruction_limit_counts_after_normalization(monkeypatch):
    request, _ = _create(monkeypatch, instructions="word " * 100 + "     " * 50)
    assert len(request.await_args.kwargs["json_body"]["instructions"]) == 499


def test_list_workflows_reads_the_gate(monkeypatch):
    info = {"enabled": False, "workflows": [{"id": "highlights", "available": True, "min_source_seconds": 0}]}
    request = AsyncMock(return_value=info)
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    result = asyncio.run(server.supoclip_list_workflows())
    assert request.await_args.args == ("GET", "/tasks/workflows")
    assert '"enabled": false' in result


def test_create_clip_alias_stays_highlights(monkeypatch):
    request = AsyncMock(return_value={"task_id": "task-1"})
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(request=request))
    asyncio.run(server.supoclip_create_clip(url=URL))
    assert "workflow" not in request.await_args.kwargs["json_body"]


@pytest.mark.parametrize(
    ("raw", "keep_newlines", "expected"),
    [
        # Cross-checked against backend normalize_text: separators like \x1c are
        # dropped as control characters, not turned into line breaks.
        ("b　\t\x1c钱钱", True, "b 钱钱"),
        ("b　　\x0bb\x1cb\xa0\n", True, "b\nbb"),
        ("b　　\x0bb\x1cb\xa0\n", False, "b bb"),
        ("\n钱\x1e\x85é\x85", True, "钱\né"),
        ("a \r\n \r\n\r\n b", True, "a\n\nb"),
    ],
)
def test_text_is_normalized_exactly_like_the_api(raw, keep_newlines, expected):
    assert server._normalize_text(raw, keep_newlines=keep_newlines) == expected
