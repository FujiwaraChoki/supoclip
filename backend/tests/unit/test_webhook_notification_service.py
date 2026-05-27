"""Unit tests for WebhookNotificationService.

Uses httpx.MockTransport (built-in, no extra dep) to stub the HTTP layer.
"""
from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from src.services.webhook_notification_service import WebhookNotificationService


SECRET = "shh"
TASK_ID = "00000000-0000-0000-0000-000000000001"
URL = "https://example.test/webhook"
COMPLETED_AT = "2026-05-15T12:34:56Z"


def _expected_signature(task_id: str, ts: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), f"{task_id}:{ts}".encode(), hashlib.sha256).hexdigest()


async def _call(service: WebhookNotificationService, transport: httpx.MockTransport, **kwargs) -> bool:
    """Run service.deliver against a stubbed httpx transport.

    Patches httpx.AsyncClient so the service's `async with` uses our transport.
    """
    original = httpx.AsyncClient

    class StubClient(original):  # type: ignore[misc]
        def __init__(self, *args, **kw):
            kw["transport"] = transport
            super().__init__(*args, **kw)

    httpx.AsyncClient = StubClient  # type: ignore[misc]
    try:
        return await service.deliver(
            webhook_url=URL,
            task_id=TASK_ID,
            job_id=None,
            status="completed",
            clips_count=4,
            generated_clips_ids=["a", "b", "c", "d"],
            error_code=None,
            completed_at=COMPLETED_AT,
            **kwargs,
        )
    finally:
        httpx.AsyncClient = original  # type: ignore[misc]


@pytest.mark.asyncio
async def test_payload_includes_per_clip_metadata():
    import json

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    service = WebhookNotificationService(SECRET)
    clips = [
        {"id": "a", "start_time": "00:00", "end_time": "00:08", "text": "hi", "clip_order": 1},
        {"id": "b", "start_time": "00:10", "end_time": "00:20", "text": "yo", "clip_order": 2},
    ]
    delivered = await _call(service, httpx.MockTransport(handler), clips=clips)

    assert delivered is True
    assert bodies[0]["clips"] == clips


@pytest.mark.asyncio
async def test_payload_clips_defaults_to_empty_list():
    import json

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    service = WebhookNotificationService(SECRET)
    # no `clips` kwarg → payload still carries the key as an empty list
    await _call(service, httpx.MockTransport(handler))
    assert bodies[0]["clips"] == []


@pytest.mark.asyncio
async def test_delivers_on_2xx_no_retry():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    service = WebhookNotificationService(SECRET)
    delivered = await _call(service, httpx.MockTransport(handler))

    assert delivered is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_drops_on_4xx_no_retry():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403)

    service = WebhookNotificationService(SECRET)
    delivered = await _call(service, httpx.MockTransport(handler))

    assert delivered is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retries_once_on_5xx():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    service = WebhookNotificationService(SECRET)
    delivered = await _call(service, httpx.MockTransport(handler))

    assert delivered is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_succeeds_after_5xx_then_2xx():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200)

    service = WebhookNotificationService(SECRET)
    delivered = await _call(service, httpx.MockTransport(handler))

    assert delivered is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_signature_matches_hmac_sha256_of_task_id_ts():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ts"] = request.headers["X-Supoclip-Ts"]
        captured["sig"] = request.headers["X-Supoclip-Signature"]
        return httpx.Response(200)

    service = WebhookNotificationService(SECRET)
    await _call(service, httpx.MockTransport(handler))

    assert captured["sig"] == _expected_signature(TASK_ID, captured["ts"])


@pytest.mark.asyncio
async def test_is_configured_reflects_secret_presence():
    assert WebhookNotificationService("").is_configured is False
    assert WebhookNotificationService("anything").is_configured is True
