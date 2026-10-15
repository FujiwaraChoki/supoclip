import asyncio
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from supoclip_mcp.client import SupoClipClient, SupoClipError
from supoclip_mcp.config import Settings


def configured_client():
    return SupoClipClient(Settings(api_url="https://api.supoclip.test", api_key=None,
        user_id="owner", auth_secret="test-secret", timeout=30, download_dir="downloads",
        mcp_transport="stdio", mcp_host="127.0.0.1", mcp_port=9100, mcp_mount_path="/mcp",
        mcp_public_url=None, mcp_require_bearer_auth=False))


def mock_requests(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_download_follows_s3_redirect_without_forwarding_auth(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "api.supoclip.test":
            assert "x-supoclip-signature" in request.headers
            return httpx.Response(307, headers={"Location": "https://private-s3.test/video.mp4?signature=test"})
        assert "x-supoclip-signature" not in request.headers
        assert "x-supoclip-user-id" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"actual video bytes")

    mock_requests(monkeypatch, handler)
    result = asyncio.run(configured_client().download("/tasks/task/clips/clip/file", str(tmp_path), "clip.mp4"))
    assert len(requests) == 2
    assert result["bytes"] == 18
    assert (tmp_path / "clip.mp4").read_bytes() == b"actual video bytes"


def test_failed_storage_response_does_not_overwrite_previous_download(monkeypatch, tmp_path):
    def handler(request):
        if request.url.host == "api.supoclip.test":
            return httpx.Response(307, headers={"Location": "https://private-s3.test/video.mp4?signature=secret"})
        return httpx.Response(403, content=b"expired signed URL")

    mock_requests(monkeypatch, handler)
    (tmp_path / "clip.mp4").write_bytes(b"previous download")
    with pytest.raises(SupoClipError, match="Storage download failed"):
        asyncio.run(configured_client().download("/tasks/task/clips/clip/file", str(tmp_path), "clip.mp4"))
    assert (tmp_path / "clip.mp4").read_bytes() == b"previous download"
    assert list(tmp_path.glob("*.part")) == []


def test_external_redirect_requires_https(monkeypatch, tmp_path):
    mock_requests(monkeypatch, lambda _: httpx.Response(307, headers={"Location": "http://private-s3.test/video.mp4"}))
    with pytest.raises(SupoClipError, match="HTTPS"):
        asyncio.run(configured_client().download("/tasks/task/clips/clip/file", str(tmp_path), "clip.mp4"))
