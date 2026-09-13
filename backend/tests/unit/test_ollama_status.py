import httpx
import pytest

from src.ollama_status import _api_base_url, check_ollama_status


def test_api_base_url_strips_v1_suffix():
    assert _api_base_url("http://localhost:11434/v1") == "http://localhost:11434"


def test_api_base_url_leaves_bare_root_untouched():
    assert _api_base_url("http://localhost:11434") == "http://localhost:11434"


async def test_check_ollama_status_reachable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.9"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}, {"name": "gemma2:2b"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original_async_client(transport=transport, **kwargs)
    )

    status = await check_ollama_status("http://localhost:11434/v1")

    assert status.reachable is True
    assert status.version == "0.32.9"
    assert status.models == ["llama3.2:3b", "gemma2:2b"]
    assert status.error is None


async def test_check_ollama_status_unreachable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original_async_client(transport=transport, **kwargs)
    )

    status = await check_ollama_status("http://localhost:11434/v1")

    assert status.reachable is False
    assert status.error is not None
    assert status.models == []
