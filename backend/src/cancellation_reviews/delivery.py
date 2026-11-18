"""Post a report to the operator chat's signed webhook (Hermes gateway route).

The route runs a script that files the message in a durable inbox, keyed by the
delivery id, then triggers the chat job that posts it. Only an answer that proves
the message was filed counts as delivered:

- 202 ``{"status": "accepted" | "triggered" ...}``: filed and the job was fired
- 200 ``{"status": "duplicate"}``: this delivery id was already accepted
Anything else (``ignored`` means the inbox script refused it, 4xx/5xx, network)
is retried with backoff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from ..hermes_webhook import signed_headers

PAYLOAD_TYPE = "supoclip.cancellation_report"
MAX_MESSAGE_CHARS = 12000


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    detail: str


def signed_request(secret: str, delivery_id: str, message: str, kind: str, *, now: int | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {"type": PAYLOAD_TYPE, "version": 1, "delivery_id": delivery_id, "kind": kind, "text": message[:MAX_MESSAGE_CHARS]},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    # The gateway's replay guard keys on X-Request-ID, so a retry of the same delivery is a duplicate.
    return body, signed_headers(secret, body, delivery_id, now=now)


def interpret(status_code: int, payload: object) -> DeliveryResult:
    state = payload.get("status") if isinstance(payload, dict) else None
    if status_code == 202 and state not in (None, "ignored", "error"):
        return DeliveryResult(True, f"accepted ({state})")
    if status_code == 200 and state == "duplicate":
        return DeliveryResult(True, "already accepted earlier")
    return DeliveryResult(False, f"HTTP {status_code} {state or ''}".strip())


async def deliver(url: str, secret: str, delivery_id: str, message: str, kind: str) -> DeliveryResult:
    body, headers = signed_request(secret, delivery_id, message, kind)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return DeliveryResult(False, f"network error ({type(exc).__name__})")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return interpret(response.status_code, payload)
