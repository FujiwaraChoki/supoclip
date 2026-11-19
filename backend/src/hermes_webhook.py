"""Signed requests to a Hermes gateway webhook route (the operator chat).

Hermes' generic V2 signature: ``X-Webhook-Timestamp`` is the unix time in seconds and
``X-Webhook-Signature-V2`` the hex HMAC-SHA256 of ``"<timestamp>.<raw body>"`` with the
route's secret. ``X-Request-ID`` carries the sender's delivery id, which the gateway's
replay guard keys on, so a resend of the same delivery is answered as a duplicate.
Used by cancellation reviews and feedback webhooks.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def signed_headers(secret: str, body: bytes, request_id: str, *, now: int | None = None) -> dict[str, str]:
    timestamp = str(now if now is not None else int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature-V2": signature(secret, timestamp, body),
        "X-Request-ID": request_id,
    }
