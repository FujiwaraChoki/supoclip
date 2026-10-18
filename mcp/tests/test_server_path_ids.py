import asyncio
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from supoclip_mcp import server
from supoclip_mcp.client import SupoClipClient
from supoclip_mcp.config import Settings


def settings(tmp_path):
    return Settings(api_url="https://api.supoclip.test", api_key="sk_test", user_id=None,
        auth_secret=None, timeout=30, download_dir=str(tmp_path),
        mcp_transport="stdio", mcp_host="127.0.0.1", mcp_port=9100, mcp_mount_path="/",
        mcp_public_url=None, mcp_require_bearer_auth=False)


@pytest.fixture
def seen(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, content=None) if not request.url.path.endswith(("/file", "/export")) else httpx.Response(200, content=b"x")

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    cfg = settings(tmp_path)
    monkeypatch.setattr(server, "SETTINGS", cfg)
    monkeypatch.setattr(server, "_client", lambda: SupoClipClient(cfg))
    return requests


def run(coro):
    return asyncio.run(coro)


def rejected(coro) -> bool:
    """True if the tool reports an error, whether returned as text or raised."""
    try:
        text = run(coro)
    except Exception as exc:
        text = str(exc)
    return "Invalid" in text and "id" in text


def test_task_id_cannot_escape_the_tasks_route(seen):
    assert rejected(server.supoclip_delete_task("../api-keys/key-1"))
    assert seen == []


@pytest.mark.parametrize("bad", [".", "..", "a/b", "a?b=1", "a#b", "a\\b", "a%2Fb"])
def test_ids_that_change_the_route_are_rejected(seen, bad):
    for call in (
        lambda: server.supoclip_get_task(bad),
        lambda: server.supoclip_list_clips(bad),
        lambda: server.supoclip_cancel_task(bad),
        lambda: server.supoclip_resume_task(bad),
        lambda: server.supoclip_delete_task(bad),
        lambda: server.supoclip_download_clip(bad, "clip"),
        lambda: server.supoclip_download_clip("task", bad),
        lambda: server.supoclip_export_clip("task", bad),
        lambda: server.supoclip_export_clip(bad, "clip"),
    ):
        assert rejected(call())
    assert seen == []


def test_regular_ids_still_reach_the_expected_paths(seen):
    run(server.supoclip_get_task("4f1c-aa"))
    run(server.supoclip_download_clip("4f1c-aa", "c_1"))
    assert [r.url.path for r in seen] == ["/tasks/4f1c-aa", "/tasks/4f1c-aa/clips/c_1/file"]
