import asyncio
from pathlib import Path
import sys

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from supoclip_mcp import server
from supoclip_mcp.client import SupoClipClient
from supoclip_mcp.config import Settings


def settings(api_key):
    return Settings(api_url="https://api.supoclip.test", api_key=api_key, user_id=None,
        auth_secret=None, timeout=30, download_dir="downloads",
        mcp_transport="stdio", mcp_host="127.0.0.1", mcp_port=9100, mcp_mount_path="/",
        mcp_public_url=None, mcp_require_bearer_auth=False)


def call_tool(monkeypatch, name, arguments, handler, api_key="sk_test"):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    cfg = settings(api_key)
    monkeypatch.setattr(server, "SETTINGS", cfg)
    monkeypatch.setattr(server, "_client", lambda: SupoClipClient(cfg))

    async def go():
        async with create_connected_server_and_client_session(server.mcp._mcp_server) as session:
            return await session.call_tool(name, arguments)

    return asyncio.run(go())


def test_backend_error_is_flagged_as_tool_error(monkeypatch):
    result = call_tool(monkeypatch, "supoclip_get_task", {"task_id": "t1"},
                       lambda request: httpx.Response(404, json={"detail": "Task not found"}))
    assert result.isError is True
    assert "Task not found" in result.content[0].text


def test_missing_credentials_is_flagged_as_tool_error(monkeypatch):
    result = call_tool(monkeypatch, "supoclip_get_task", {"task_id": "t1"},
                       lambda request: httpx.Response(200, json={}), api_key=None)
    assert result.isError is True
    assert "SUPOCLIP_API_KEY" in result.content[0].text


def test_successful_call_is_not_an_error(monkeypatch):
    result = call_tool(monkeypatch, "supoclip_get_task", {"task_id": "t1"},
                       lambda request: httpx.Response(200, json={"status": "completed"}))
    assert result.isError is False
    assert '"completed"' in result.content[0].text
