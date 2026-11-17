"""Feedback webhooks without a database: URL safety (SSRF), payloads, signing, retry policy."""

import hashlib
import hmac
import json
import socket
from datetime import datetime, timezone

import httpx
import pytest

from src.feedback import delivery
from src.feedback.url_safety import (
    UnsafeWebhookUrl,
    WebhookTarget,
    resolve_public_address,
    url_hint,
    validate_webhook_url,
)

DISCORD = "https://discord.com/api/webhooks/123456/abcdefTOKEN"
SUBMISSION = {
    "id": "sub-1",
    "user_id": "user-1",
    "email": "ana@example.com",
    "category": "bug",
    "message": "Captions drift @everyone",
    "status": "open",
    "platform": "ios",
    "app_version": "2.4.0",
    "page_url": "/tasks/abc",
    "locale": "de",
    "created_at": datetime(2026, 11, 17, 9, 30, tzinfo=timezone.utc),
}


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("", "url_required"),
        ("http://example.com/hook", "url_not_https"),
        ("ftp://example.com/hook", "url_not_https"),
        ("https://user:pass@example.com/hook", "url_has_credentials"),
        ("https://localhost/hook", "url_private_address"),
        ("https://api.localhost/hook", "url_private_address"),
        ("https://backend/hook", "url_private_address"),
        ("https://redis.internal/hook", "url_private_address"),
        ("https://127.0.0.1/hook", "url_private_address"),
        ("https://10.0.0.5/hook", "url_private_address"),
        ("https://172.16.3.4/hook", "url_private_address"),
        ("https://192.168.1.1/hook", "url_private_address"),
        ("https://169.254.169.254/latest/meta-data", "url_private_address"),
        ("https://100.64.0.1/hook", "url_private_address"),
        ("https://0.0.0.0/hook", "url_private_address"),
        ("https://[::1]/hook", "url_private_address"),
        ("https://[fd00::1]/hook", "url_private_address"),
        ("https://[::ffff:127.0.0.1]/hook", "url_private_address"),
        ("https://example.com/" + "a" * 2100, "url_too_long"),
        ("https://exa mple.com/hook", "url_invalid"),
    ],
)
def test_unsafe_urls_are_rejected(url, code):
    with pytest.raises(UnsafeWebhookUrl) as raised:
        validate_webhook_url(url, "json")
    assert raised.value.code == code


def test_public_https_urls_pass():
    target = validate_webhook_url("https://hooks.example.com:8443/in?x=1", "json")
    assert (target.host, target.port) == ("hooks.example.com", 8443)
    assert validate_webhook_url("https://8.8.8.8/hook", "json").host == "8.8.8.8"
    assert validate_webhook_url(DISCORD, "discord").host == "discord.com"


@pytest.mark.parametrize(
    "url", ["https://example.com/api/webhooks/1/x", "https://discord.com/channels/1", "https://evil-discord.com/api/webhooks/1/x"]
)
def test_discord_webhooks_must_be_discord_webhook_urls(url):
    with pytest.raises(UnsafeWebhookUrl) as raised:
        validate_webhook_url(url, "discord")
    assert raised.value.code == "url_not_discord"


def _resolver(monkeypatch, *addresses):
    async def getaddrinfo(host, port, type=0):  # noqa: A002
        if not addresses:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)


@pytest.mark.asyncio
async def test_a_name_resolving_to_a_private_address_is_blocked(monkeypatch):
    target = WebhookTarget("https://rebind.example.com/x", "rebind.example.com", 443)
    _resolver(monkeypatch, "93.184.216.34", "10.0.0.7")
    with pytest.raises(UnsafeWebhookUrl) as raised:
        await resolve_public_address(target)
    assert raised.value.code == "url_private_address"

    _resolver(monkeypatch)
    with pytest.raises(UnsafeWebhookUrl) as raised:
        await resolve_public_address(target)
    assert raised.value.code == "url_unresolvable"

    _resolver(monkeypatch, "2606:4700::1111", "93.184.216.34")
    assert await resolve_public_address(target) == "93.184.216.34"


@pytest.mark.asyncio
async def test_the_sender_connects_to_the_checked_address_with_the_real_host(monkeypatch):
    _resolver(monkeypatch, "93.184.216.34")
    seen = {}

    async def handler(request: httpx.Request):
        seen.update(url=str(request.url), host=request.headers["host"], sni=request.extensions.get("sni_hostname"))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(delivery.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    response = await delivery.post_pinned("https://hooks.example.com/in?a=1", "json", b"{}", {})
    assert response.status_code == 204
    assert seen == {"url": "https://93.184.216.34/in?a=1", "host": "hooks.example.com", "sni": "hooks.example.com"}


def test_url_hint_hides_the_token():
    hint = url_hint(DISCORD)
    assert hint == "discord.com/…OKEN"
    assert "abcdef" not in hint and "123456" not in hint


def test_discord_embed_matches_the_old_one_and_cannot_ping():
    body = json.loads(delivery.discord_body(delivery.EVENT_CREATED, SUBMISSION))
    embed = body["embeds"][0]
    assert embed["title"] == "Bug Report"
    assert embed["description"] == SUBMISSION["message"]
    assert embed["color"] == 0xEF4444
    assert embed["fields"][:2] == [
        {"name": "Category", "value": "bug", "inline": True},
        {"name": "User ID", "value": "user-1", "inline": True},
    ]
    assert {"name": "Platform", "value": "ios 2.4.0", "inline": True} in embed["fields"]
    assert embed["url"].endswith("/admin/feedback/sub-1")
    assert body["allowed_mentions"] == {"parse": []}


def test_json_payload_is_signed_over_timestamp_and_body():
    destination = {"kind": "json", "secret": "whsec_test"}
    body, headers = delivery.build_request(destination, delivery.EVENT_CREATED, "del-1", SUBMISSION, now=1700000000)
    payload = json.loads(body)
    assert payload["type"] == "feedback.created" and payload["version"] == 1 and payload["delivery_id"] == "del-1"
    assert payload["submission"]["user"] == {"id": "user-1", "email": "ana@example.com"}
    assert payload["submission"]["created_at"] == "2026-11-17T09:30:00Z"
    assert headers["X-SupoClip-Delivery"] == "del-1" and headers["X-SupoClip-Event"] == "feedback.created"
    expected = hmac.new(b"whsec_test", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert headers["X-SupoClip-Signature"] == f"t=1700000000,v1={expected}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "ok", "retryable"),
    [(200, True, False), (204, True, False), (500, False, True), (503, False, True), (429, False, True),
     (404, False, False), (401, False, False), (301, False, False)],
)
async def test_send_classifies_answers(monkeypatch, status, ok, retryable):
    async def post(*_args, **_kwargs):
        return httpx.Response(status, headers={"retry-after": "120"} if status == 429 else {})

    monkeypatch.setattr(delivery, "post_pinned", post)
    result = await delivery.send({"kind": "discord", "url": DISCORD}, delivery.EVENT_CREATED, "d", SUBMISSION)
    assert (result.ok, result.retryable, result.status_code) == (ok, retryable, status)
    if status == 429:
        assert result.retry_after == 120


@pytest.mark.asyncio
async def test_send_errors_never_contain_the_url(monkeypatch):
    async def post(url, *_args, **_kwargs):
        raise httpx.ConnectError(f"cannot connect to {url}")

    monkeypatch.setattr(delivery, "post_pinned", post)
    result = await delivery.send({"kind": "discord", "url": DISCORD}, delivery.EVENT_CREATED, "d", SUBMISSION)
    assert result.retryable and result.error == "network error (ConnectError)"
    assert "TOKEN" not in result.error
