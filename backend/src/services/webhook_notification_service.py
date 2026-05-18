"""Outbound webhook delivery for task lifecycle events.

Posts a signed JSON payload to a per-task `webhook_url`. Two event flavours:

  * Terminal (``completed`` / ``error``):
      ``deliver(...)`` — best-effort with 1 retry on transient failure.
      Caller is responsible for idempotency via
      ``tasks.webhook_delivered_at`` (atomic stamp-then-deliver).

  * Progress (``progress``):
      ``deliver_progress(...)`` — fire-and-forget, no retry. Receiver is
      expected to dedupe / discard out-of-order frames if it cares;
      progress frames are cheap to lose since the next tick is seconds
      away.

Signature scheme matches the inbound HMAC auth in both directions:

    X-Supoclip-Ts:        <unix seconds>
    X-Supoclip-Signature: hex(HMAC_SHA256(secret, f"{task_id}:{ts}"))

Wire shape (all events):
    {
      "event":   "completed" | "error" | "progress",
      "task_id": "...",
      "job_id":  "..." | null,
      ...event-specific fields
    }

The ``event`` discriminator is new for progress support. Terminal payloads
also carry it now so receivers can dispatch on a single field; the legacy
``status`` field stays in place on terminal events for backwards-compat.
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

    def _sign(self, task_id: str) -> tuple[str, str]:
        """Return ``(ts, signature)`` for the HMAC headers."""
        ts = str(int(time.time()))
        signature = hmac.new(
            self._secret.encode(),
            f"{task_id}:{ts}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return ts, signature

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
        ts, signature = self._sign(task_id)

        payload: dict[str, Any] = {
            # `event` is the new discriminator — mirrors `status` for
            # terminal frames so receivers can dispatch on a single field.
            "event": status,
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
                if 500 <= resp.status_code < 600:
                    logger.warning(
                        "Webhook delivery 5xx for task %s (attempt %d): %s",
                        task_id, attempt, resp.status_code,
                    )
                    continue
                # 1xx/3xx — receiver isn't speaking our protocol. Don't retry.
                logger.warning(
                    "Webhook delivery got unexpected status for task %s: %s; not retrying",
                    task_id, resp.status_code,
                )
                return False
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                logger.warning(
                    "Webhook delivery network error for task %s (attempt %d): %s",
                    task_id, attempt, exc,
                )
        return False

    async def deliver_progress(
        self,
        *,
        webhook_url: str,
        task_id: str,
        job_id: Optional[str],
        progress: int,
        message: Optional[str] = None,
    ) -> bool:
        """Fire a `progress` webhook. No retry — progress is best-effort.

        Receiver is expected to dispatch on ``event == "progress"`` and
        treat the payload as advisory (update a UI progress bar etc.).
        Dropping a frame is harmless because more are coming.
        """
        ts, signature = self._sign(task_id)

        payload: dict[str, Any] = {
            "event": "progress",
            "task_id": task_id,
            "job_id": job_id,
            "progress": progress,
        }
        if message is not None:
            payload["message"] = message

        headers = {
            "Content-Type": "application/json",
            "X-Supoclip-Ts": ts,
            "X-Supoclip-Signature": signature,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(webhook_url, json=payload, headers=headers)
            if 200 <= resp.status_code < 300:
                return True
            logger.warning(
                "Progress webhook for task %s returned %s; dropping (no retry)",
                task_id, resp.status_code,
            )
            return False
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "Progress webhook network error for task %s: %s; dropping (no retry)",
                task_id, exc,
            )
            return False
