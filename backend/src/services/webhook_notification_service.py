"""Outbound webhook delivery for terminal task status.

Posts a signed JSON payload to a per-task `webhook_url` when a task reaches
`completed` or `error`. Signature scheme matches the inbound HMAC auth:

    X-Supoclip-Ts:        <unix seconds>
    X-Supoclip-Signature: hex(HMAC_SHA256(secret, f"{task_id}:{ts}"))

Delivery policy:
- 1 retry on 5xx / network error / timeout (total of 2 attempts)
- No retry on 4xx — receiver said "no thanks", that's permanent
- Idempotency via `tasks.webhook_delivered_at` (caller responsibility)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class WebhookNotificationService:
    def __init__(self, secret: str, *, timeout_seconds: float = 10.0):
        self._secret = secret
        self._timeout = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self._secret)

    async def deliver(
        self,
        *,
        webhook_url: str,
        task_id: str,
        job_id: Optional[str],
        status: str,
        clips_count: int,
        generated_clips_ids: list[str],
        error_code: Optional[str],
        completed_at: str,
        message: Optional[str] = None,
    ) -> bool:
        ts = str(int(time.time()))
        signature = hmac.new(
            self._secret.encode(),
            f"{task_id}:{ts}".encode(),
            hashlib.sha256,
        ).hexdigest()

        payload: dict[str, Any] = {
            "task_id": task_id,
            "job_id": job_id,
            "status": status,
            "clips_count": clips_count,
            "generated_clips_ids": generated_clips_ids,
            "error_code": error_code,
            "completed_at": completed_at,
        }
        if message is not None:
            payload["message"] = message

        headers = {
            "Content-Type": "application/json",
            "X-Supoclip-Ts": ts,
            "X-Supoclip-Signature": signature,
        }

        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(webhook_url, json=payload, headers=headers)
                if 200 <= resp.status_code < 300:
                    logger.info(
                        "Webhook delivered for task %s (attempt %d, status %d)",
                        task_id, attempt, resp.status_code,
                    )
                    return True
                if 400 <= resp.status_code < 500:
                    logger.warning(
                        "Webhook delivery rejected (4xx) for task %s: %s",
                        task_id, resp.status_code,
                    )
                    return False
                logger.warning(
                    "Webhook delivery 5xx for task %s (attempt %d): %s",
                    task_id, attempt, resp.status_code,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                logger.warning(
                    "Webhook delivery network error for task %s (attempt %d): %s",
                    task_id, attempt, exc,
                )
        return False
