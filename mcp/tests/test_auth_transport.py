from dataclasses import replace
from pathlib import Path
import sys

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from supoclip_mcp import server


def test_http_initialization_requires_a_valid_supoclip_api_key(monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", replace(
        server.SETTINGS,
        mcp_host="127.0.0.1",
        mcp_port=9100,
        mcp_require_bearer_auth=True,
        mcp_public_url="http://127.0.0.1:9100",
    ))

    async def valid_key(self, token):
        return token == "sk_transport_test_key"

    monkeypatch.setattr(server.SupoClipApiKeyVerifier, "_is_valid_key", valid_key)
    mcp = server._build_mcp()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "auth-regression", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(mcp.streamable_http_app(), base_url="http://127.0.0.1:9100") as client:
        assert client.post("/mcp", json=payload, headers=headers).status_code == 401
        for token in ("invalid-format", "sk_rejected_key"):
            rejected = client.post("/mcp", json=payload, headers={
                **headers, "Authorization": f"Bearer {token}",
            })
            assert rejected.status_code == 401
        accepted = client.post("/mcp", json=payload, headers={
            **headers, "Authorization": "Bearer sk_transport_test_key",
        })
        assert accepted.status_code == 200
        assert "serverInfo" in accepted.text
