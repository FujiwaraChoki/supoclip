"""Signed webhook delivery with persisted attempts."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..runtime_settings import decrypt_setting_value


async def emit_webhook_event(
    db: AsyncSession,
    *,
    user_id: str,
    event_type: str,
    payload: dict,
    endpoint_id: str | None = None,
) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT id, url, secret_encrypted, events FROM webhook_endpoints
            WHERE user_id = :user_id AND enabled = true
              AND (:endpoint_id IS NULL OR id = :endpoint_id)
            """
        ),
        {"user_id": user_id, "endpoint_id": endpoint_id},
    )
    deliveries = []
    for endpoint in result.mappings():
        try:
            events = json.loads(endpoint["events"] or "[]")
        except json.JSONDecodeError:
            events = []
        if endpoint_id is None and event_type not in events and "*" not in events:
            continue
        delivery_id = str(uuid4())
        envelope = {
            "id": delivery_id,
            "type": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        body = json.dumps(envelope, separators=(",", ":"), default=str)
        await db.execute(
            text(
                """
                INSERT INTO webhook_deliveries
                    (id, endpoint_id, event_type, payload_json, status)
                VALUES (:id, :endpoint_id, :event_type, :payload, 'processing')
                """
            ),
            {"id": delivery_id, "endpoint_id": endpoint["id"],
             "event_type": event_type, "payload": body},
        )
        await db.commit()
        timestamp = str(int(time.time()))
        secret = decrypt_setting_value(endpoint["secret_encrypted"])
        signature = hmac.new(
            secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
        ).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.post(
                    endpoint["url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "SupoClip-Webhooks/1.0",
                        "X-SupoClip-Event": event_type,
                        "X-SupoClip-Timestamp": timestamp,
                        "X-SupoClip-Signature": f"v1={signature}",
                        "X-SupoClip-Delivery": delivery_id,
                    },
                )
            response.raise_for_status()
            await db.execute(
                text(
                    """
                    UPDATE webhook_deliveries SET status = 'delivered', response_status = :status,
                        attempt_count = attempt_count + 1, updated_at = NOW() WHERE id = :id
                    """
                ),
                {"id": delivery_id, "status": response.status_code},
            )
            deliveries.append({"id": delivery_id, "status": "delivered"})
        except Exception as exc:
            await db.execute(
                text(
                    """
                    UPDATE webhook_deliveries SET status = 'retrying', attempt_count = attempt_count + 1,
                        last_error = :error, next_attempt_at = :next_attempt, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": delivery_id, "error": str(exc)[:2000],
                 "next_attempt": datetime.now(timezone.utc) + timedelta(minutes=5)},
            )
            deliveries.append({"id": delivery_id, "status": "retrying", "error": str(exc)})
        await db.commit()
    return deliveries


async def retry_webhook_deliveries(db: AsyncSession) -> dict:
    result = await db.execute(
        text(
            """
            SELECT wd.*, we.url, we.secret_encrypted
            FROM webhook_deliveries wd JOIN webhook_endpoints we ON we.id = wd.endpoint_id
            WHERE wd.status = 'retrying' AND wd.next_attempt_at <= NOW() AND we.enabled = true
              AND wd.attempt_count < 5
            ORDER BY wd.next_attempt_at LIMIT 25 FOR UPDATE SKIP LOCKED
            """
        )
    )
    delivered, retrying, failed = [], [], []
    for row in result.mappings():
        timestamp = str(int(time.time()))
        body = row["payload_json"]
        secret = decrypt_setting_value(row["secret_encrypted"])
        signature = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.post(
                    row["url"], content=body,
                    headers={"Content-Type": "application/json", "User-Agent": "SupoClip-Webhooks/1.0",
                             "X-SupoClip-Event": row["event_type"], "X-SupoClip-Timestamp": timestamp,
                             "X-SupoClip-Signature": f"v1={signature}", "X-SupoClip-Delivery": row["id"]},
                )
            response.raise_for_status()
            await db.execute(
                text("UPDATE webhook_deliveries SET status = 'delivered', response_status = :status, attempt_count = attempt_count + 1, last_error = NULL, updated_at = NOW() WHERE id = :id"),
                {"id": row["id"], "status": response.status_code},
            )
            delivered.append(row["id"])
        except Exception as exc:
            attempt = int(row["attempt_count"] or 0) + 1
            terminal = attempt >= 5
            delay = min(60, 2 ** attempt)
            await db.execute(
                text("UPDATE webhook_deliveries SET status = :status, attempt_count = :attempt, last_error = :error, next_attempt_at = :next_attempt, updated_at = NOW() WHERE id = :id"),
                {"id": row["id"], "status": "failed" if terminal else "retrying", "attempt": attempt,
                 "error": str(exc)[:2000], "next_attempt": datetime.now(timezone.utc) + timedelta(minutes=delay)},
            )
            (failed if terminal else retrying).append(row["id"])
        await db.commit()
    return {"delivered": delivered, "retrying": retrying, "failed": failed}
