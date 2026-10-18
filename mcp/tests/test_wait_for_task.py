import asyncio
from dataclasses import replace
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from supoclip_mcp import server
from supoclip_mcp.client import SupoClipClient
from supoclip_mcp.config import Settings


def setup(monkeypatch, responses):
    """Serve `responses` (a list of httpx.Response factories) in order."""
    calls = []

    def handler(request):
        calls.append(request)
        return responses[min(len(calls), len(responses)) - 1]()

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    cfg = Settings(api_url="https://api.supoclip.test", api_key="sk_test", user_id=None,
        auth_secret=None, timeout=30, download_dir="downloads",
        mcp_transport="stdio", mcp_host="127.0.0.1", mcp_port=9100, mcp_mount_path="/",
        mcp_public_url=None, mcp_require_bearer_auth=False)
    monkeypatch.setattr(server, "_client", lambda: SupoClipClient(cfg))

    async def no_sleep(_):
        pass

    monkeypatch.setattr(server.asyncio, "sleep", no_sleep)
    return calls


def wait():
    return asyncio.run(server.supoclip_wait_for_task("t1", timeout_seconds=60, poll_interval_seconds=2))


def wait_failure() -> str:
    """Error text of a wait that failed, whether returned or raised."""
    try:
        out = wait()
    except Exception as exc:
        return str(exc)
    assert out.startswith("Error:"), out
    return out


def ok(status, progress=0):
    return lambda: httpx.Response(200, json={"status": status, "progress": progress})


def test_transient_backend_error_does_not_abort_the_wait(monkeypatch):
    calls = setup(monkeypatch, [ok("processing", 10), lambda: httpx.Response(502, text="bad gateway"), ok("completed", 100)])
    out = wait()
    assert '"status": "completed"' in out
    assert len(calls) == 3


def test_transient_errors_eventually_surface(monkeypatch):
    calls = setup(monkeypatch, [lambda: httpx.Response(503, text="down")])
    out = wait_failure()
    assert "503" in out
    assert len(calls) == 5


def test_permanent_errors_fail_immediately(monkeypatch):
    calls = setup(monkeypatch, [lambda: httpx.Response(404, json={"detail": "Task not found"})])
    out = wait_failure()
    assert "Task not found" in out
    assert len(calls) == 1


def test_missing_credentials_are_not_retried(monkeypatch):
    calls = setup(monkeypatch, [ok("completed")])
    monkeypatch.setattr(server, "_client", lambda: SupoClipClient(replace(server.SETTINGS, api_key=None, user_id=None, auth_secret=None)))
    out = wait_failure()
    assert "SUPOCLIP_API_KEY" in out
    assert calls == []
