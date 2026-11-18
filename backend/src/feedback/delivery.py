"""Deliver new submissions to webhooks from the outbox.

A submission's deliveries are written in its own transaction (``service.submit_feedback``).
The API then tries them once right away (``schedule_immediate_delivery``), and the cron
worker's ``deliver_feedback_webhooks`` picks up whatever is still due every minute, with
backoff. A sender claims a delivery with a short lease, so the API and the cron worker
never send the same one at the same time. Receivers get the delivery id in every
request and should deduplicate on it: a send that times out after the receiver got it
is sent again.

Discord webhooks get an embed like the one before the inbox. Generic JSON webhooks
get the payload documented in docs/feedback.md, signed with HMAC-SHA256. The Hermes
webhook (server-configured) gets ``supoclip.feedback`` signed with Hermes' V2 scheme,
and only counts as delivered when the gateway says it filed it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..hermes_webhook import signed_headers as hermes_signed_headers
from . import CATEGORY_COLORS, CATEGORY_LABELS, HERMES_KIND
from .url_safety import UnsafeWebhookUrl, resolve_public_address, validate_webhook_url
from .webhooks import load_destination

logger = logging.getLogger(__name__)

EVENT_CREATED = "feedback.created"
EVENT_TEST = "feedback.test"
PAYLOAD_VERSION = 1
USER_AGENT = "SupoClip-Webhooks/1"
TIMEOUT_SECONDS = 10
SEND_DEADLINE_SECONDS = 20
LEASE = timedelta(minutes=2)
# Delay before attempt 2, 3, ...; after the last one the delivery is failed (about 19 hours in all).
RETRY_DELAYS = (30, 120, 600, 1800, 7200, 21600, 43200)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
MAX_RETRY_AFTER = 3600
BATCH_SIZE = 25
# 4xx answers that may succeed later; any other 4xx is a configuration error and is not retried.
RETRYABLE_4XX = {408, 409, 425, 429}
HERMES_PAYLOAD_TYPE = "supoclip.feedback"
HERMES_FIELDS = ("id", "category", "message", "user_id", "email", "page_url", "platform", "app_version", "created_at")
# Enough for the gateway's {"status": ...} answer; anything longer is not read.
MAX_RESPONSE_BYTES = 4096

_background: set[asyncio.Task] = set()


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status_code: int | None
    error: str | None
    retryable: bool
    retry_after: int | None = None
    duration_ms: int = 0


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def admin_url(submission_id: str) -> str:
    return f"{get_config().app_base_url}/admin/feedback/{submission_id}"


def submission_payload(submission: dict[str, Any]) -> dict[str, Any]:
    """The ``submission`` object of the JSON payload (docs/feedback.md)."""
    user = None
    if submission.get("user_id"):
        user = {"id": submission["user_id"], "email": submission.get("email")}
    return {
        "id": submission["id"],
        "category": submission["category"],
        "category_label": CATEGORY_LABELS.get(submission["category"], submission["category"]),
        "message": submission["message"],
        "status": submission.get("status", "open"),
        "created_at": _iso(submission.get("created_at")),
        "page_url": submission.get("page_url"),
        "platform": submission.get("platform") or "web",
        "app_version": submission.get("app_version"),
        "locale": submission.get("locale"),
        "user": user,
        "admin_url": admin_url(submission["id"]),
    }


def json_body(event: str, delivery_id: str, submission: dict[str, Any]) -> bytes:
    payload = {
        "type": event,
        "version": PAYLOAD_VERSION,
        "delivery_id": delivery_id,
        "sent_at": _iso(datetime.now(timezone.utc)),
        "submission": submission_payload(submission),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def discord_body(event: str, submission: dict[str, Any]) -> bytes:
    category = submission["category"]
    fields = [
        {"name": "Category", "value": category, "inline": True},
        {"name": "User ID", "value": submission.get("user_id") or "anonymous", "inline": True},
    ]
    if submission.get("email"):
        fields.append({"name": "Email", "value": submission["email"][:256], "inline": True})
    platform = submission.get("platform") or "web"
    if submission.get("app_version"):
        platform = f"{platform} {submission['app_version']}"
    fields.append({"name": "Platform", "value": platform[:256], "inline": True})
    if submission.get("page_url"):
        fields.append({"name": "Page", "value": submission["page_url"][:1024], "inline": False})
    title = CATEGORY_LABELS.get(category, category)
    embed = {
        "title": f"[Test] {title}" if event == EVENT_TEST else title,
        "description": submission["message"][:4000],
        "url": admin_url(submission["id"]),
        "color": CATEGORY_COLORS.get(category, 0x6B7280),
        "fields": fields,
        "timestamp": _iso(submission.get("created_at")) or _iso(datetime.now(timezone.utc)),
    }
    # No pings from whatever a user typed.
    return json.dumps({"embeds": [embed], "allowed_mentions": {"parse": []}}, ensure_ascii=False).encode()


def hermes_body(delivery_id: str, submission: dict[str, Any]) -> bytes:
    """Exactly the fields the Hermes route accepts: strings or null, created_at in ISO 8601."""
    fields = {key: submission.get(key) for key in HERMES_FIELDS}
    fields["created_at"] = _iso(submission.get("created_at"))
    fields["platform"] = fields["platform"] or "web"
    payload = {"type": HERMES_PAYLOAD_TYPE, "version": 1, "delivery_id": delivery_id, "submission": fields}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def hermes_outcome(status_code: int, raw: bytes) -> tuple[bool, str | None]:
    """A 2xx counts only when its ``status`` shows the route filed the submission.

    ``ignored`` (the route's script refused it) and ``error`` are failures to retry;
    the script runs again on a resend and Hermes deduplicates on the delivery id.
    """
    try:
        payload = json.loads(raw.decode() or "null")
    except (ValueError, UnicodeDecodeError):
        payload = None
    state = payload.get("status") if isinstance(payload, dict) else None
    if isinstance(state, str) and state and state not in ("ignored", "error"):
        return True, None
    return False, f"HTTP {status_code} {state if isinstance(state, str) else 'without a status'}"[:200]


def sign(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def build_request(
    destination: dict[str, Any], event: str, delivery_id: str, submission: dict[str, Any], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if destination["kind"] == "discord":
        return discord_body(event, submission), headers
    if destination["kind"] == HERMES_KIND:
        body = hermes_body(delivery_id, submission)
        return body, {**headers, **hermes_signed_headers(destination["secret"] or "", body, delivery_id, now=now)}
    body = json_body(event, delivery_id, submission)
    timestamp = now if now is not None else int(time.time())
    headers.update({
        "X-SupoClip-Event": event,
        "X-SupoClip-Delivery": delivery_id,
        "X-SupoClip-Timestamp": str(timestamp),
    })
    if destination.get("secret"):
        headers["X-SupoClip-Signature"] = sign(destination["secret"], body, timestamp)
    return body, headers


async def post_pinned(url: str, kind: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
    """POST to the address that passed the public-address check, not a fresh lookup.

    The URL's host goes in ``Host`` and in TLS SNI, so certificates are still verified
    against the name. Redirects are not followed.
    """
    target = validate_webhook_url(url, kind)
    address = await resolve_public_address(target)
    parts = urlsplit(target.url)
    netloc_host = f"[{address}]" if ":" in address else address
    netloc = f"{netloc_host}:{target.port}" if parts.port else netloc_host
    pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    host = f"[{target.host}]" if ":" in target.host else target.host
    host_header = host if not parts.port else f"{host}:{target.port}"
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
        async with client.stream(
            "POST", pinned,
            content=body,
            headers={**headers, "Host": host_header},
            extensions={"sni_hostname": target.host},
        ) as response:
            # Only the status and headers matter; never buffer an untrusted body.
            return response


async def post_configured(url: str, body: bytes, headers: dict[str, str]) -> tuple[httpx.Response, bytes]:
    """POST to the server-configured Hermes route. No public-address rule (the operator set
    the URL and it may be the gateway's internal address), no redirects, no proxy env."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
        async with client.stream("POST", url, content=body, headers=headers) as response:
            raw = b""
            async for chunk in response.aiter_bytes():
                raw += chunk
                if len(raw) >= MAX_RESPONSE_BYTES:
                    break
            return response, raw[:MAX_RESPONSE_BYTES]


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    try:
        return min(MAX_RETRY_AFTER, max(1, int(float(value)))) if value else None
    except (ValueError, OverflowError):
        return None


async def send(destination: dict[str, Any], event: str, delivery_id: str, submission: dict[str, Any]) -> SendResult:
    """One attempt. Errors name the failure, never the URL (it holds Discord's token)."""
    if destination["kind"] == HERMES_KIND and not (destination.get("url") and destination.get("secret")):
        # This process lacks HERMES_FEEDBACK_WEBHOOK_*; one that has them may pick it up on a retry.
        return SendResult(False, None, "not configured on this server", True)
    body, headers = build_request(destination, event, delivery_id, submission)
    started = time.monotonic()
    elapsed = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
    raw = None
    try:
        async with asyncio.timeout(SEND_DEADLINE_SECONDS):
            if destination["kind"] == HERMES_KIND:
                response, raw = await post_configured(destination["url"], body, headers)
            else:
                response = await post_pinned(destination["url"], destination["kind"], body, headers)
    except UnsafeWebhookUrl as exc:
        return SendResult(False, None, f"blocked: {exc.code}", exc.code == "url_unresolvable", None, elapsed())
    except (httpx.TimeoutException, TimeoutError):
        return SendResult(False, None, "timed out", True, None, elapsed())
    except httpx.HTTPError as exc:
        return SendResult(False, None, f"network error ({type(exc).__name__})", True, None, elapsed())
    code = response.status_code
    if 200 <= code < 300 and raw is not None:
        filed, error = hermes_outcome(code, raw)
        return SendResult(filed, code, error, not filed, None, elapsed())
    if 200 <= code < 300:
        return SendResult(True, code, None, False, None, elapsed())
    if 300 <= code < 400:
        return SendResult(False, code, f"HTTP {code} (redirects are not followed)", False, None, elapsed())
    retryable = code >= 500 or code in RETRYABLE_4XX
    return SendResult(False, code, f"HTTP {code}", retryable, _retry_after(response), elapsed())


async def _claim(db: AsyncSession, *, submission_id: str | None, limit: int) -> list[dict[str, Any]]:
    # Crashes count toward the retry budget. Test sends are never retried.
    await db.execute(text(
        "UPDATE feedback_webhook_deliveries SET status = 'failed', lease_expires_at = NULL, "
        "lease_token = NULL, last_error = 'sender lease expired', updated_at = NOW() "
        "WHERE status = 'pending' AND lease_expires_at < clock_timestamp() "
        "AND (is_test OR attempts >= :maximum)"
    ), {"maximum": MAX_ATTEMPTS})
    scope = "AND submission_id = :submission_id" if submission_id else ""
    rows = await db.execute(
        text(
            # MATERIALIZED runs the pick once. As an IN subquery PostgreSQL may rerun
            # it per row, and with SKIP LOCKED and clock_timestamp() that leased more
            # than :limit rows, which then sat unsent until the lease expired.
            f"""
            WITH picked AS MATERIALIZED (
                SELECT id FROM feedback_webhook_deliveries
                WHERE status = 'pending' AND NOT is_test AND next_attempt_at <= clock_timestamp()
                  AND (lease_expires_at IS NULL OR lease_expires_at < clock_timestamp()) {scope}
                ORDER BY next_attempt_at
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            UPDATE feedback_webhook_deliveries d
            SET lease_token = uuid_generate_v4()::text, lease_expires_at = clock_timestamp() + make_interval(secs => :lease), attempts = d.attempts + 1, updated_at = NOW()
            FROM picked
            WHERE d.id = picked.id
            RETURNING d.id, d.submission_id, d.webhook_id, d.attempts, d.is_test, d.lease_token
            """
        ),
        {"lease": LEASE.total_seconds(), "limit": limit, "submission_id": submission_id},
    )
    claimed = [dict(row) for row in rows.mappings().all()]
    await db.commit()
    return claimed


async def _submission(db: AsyncSession, submission_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(text("SELECT * FROM feedback_submissions WHERE id = :id"), {"id": submission_id})
    ).mappings().first()
    return dict(row) if row else None


async def record_attempt(db: AsyncSession, delivery: dict[str, Any], result: SendResult) -> str:
    """Store the outcome of one attempt and schedule the next. Returns the delivery's new status."""
    attempt = delivery["attempts"]
    if result.ok:
        status, next_at = "delivered", None
    elif result.retryable and attempt < MAX_ATTEMPTS:
        delay = max(RETRY_DELAYS[attempt - 1], result.retry_after or 0)
        status, next_at = "pending", datetime.now(timezone.utc) + timedelta(seconds=delay)
    else:
        status, next_at = "failed", None
    # A unique token also fences a cancelled delivery which an admin retried.
    updated = await db.execute(
        text("""
            UPDATE feedback_webhook_deliveries
            SET status = :status, lease_expires_at = NULL, lease_token = NULL,
                last_status_code = :code, last_error = :error,
                next_attempt_at = COALESCE(:next_at, next_attempt_at),
                delivered_at = CASE WHEN :delivered THEN NOW() ELSE delivered_at END,
                updated_at = NOW()
            WHERE id = :id AND status = 'pending' AND lease_token = :token
              AND lease_expires_at > clock_timestamp()
            RETURNING id
        """),
        {"id": delivery["id"], "token": delivery["lease_token"], "status": status,
         "delivered": status == "delivered", "code": result.status_code,
         "error": result.error, "next_at": next_at},
    )
    if updated.scalar_one_or_none() is None:
        await db.commit()
        return "stale"
    await db.execute(
        text("""
            INSERT INTO feedback_webhook_attempts (delivery_id, attempt, status_code, error, duration_ms)
            VALUES (:id, :attempt, :code, :error, :duration)
        """),
        {"id": delivery["id"], "attempt": attempt, "code": result.status_code,
         "error": result.error, "duration": result.duration_ms},
    )
    await db.commit()
    return status


async def _deliver_one(db: AsyncSession, delivery: dict[str, Any]) -> str:
    destination = await load_destination(db, delivery["webhook_id"])
    submission = await _submission(db, delivery["submission_id"]) if delivery["submission_id"] else None
    # Release the pooled connection and read transaction before any DNS/HTTP I/O.
    await db.commit()
    if destination is None or submission is None:
        return await record_attempt(db, delivery, SendResult(False, None, "webhook or submission is gone", False))
    if not destination["enabled"]:
        await db.execute(
            text("UPDATE feedback_webhook_deliveries SET status = 'cancelled', lease_expires_at = NULL, "
                 "lease_token = NULL, updated_at = NOW() WHERE id = :id AND status = 'pending' "
                 "AND lease_token = :token"),
            {"id": delivery["id"], "token": delivery["lease_token"]},
        )
        await db.commit()
        return "cancelled"
    result = await send(destination, EVENT_CREATED, delivery["id"], submission)
    status = await record_attempt(db, delivery, result)
    if status != "delivered":
        logger.warning(
            "Feedback webhook %s delivery %s attempt %d: %s",
            destination["id"], delivery["id"], delivery["attempts"], result.error,
        )
    return status


async def deliver_due(db: AsyncSession, *, submission_id: str | None = None, limit: int = BATCH_SIZE) -> dict[str, int]:
    outcomes: dict[str, int] = {}
    for _ in range(limit):
        claimed = await _claim(db, submission_id=submission_id, limit=1)
        if not claimed:
            break
        delivery = claimed[0]
        try:
            status = await _deliver_one(db, delivery)
        except Exception as exc:
            await db.rollback()
            # Decryption/configuration failures must also exhaust the retry budget.
            # Exception text can contain a URL, so persist/log only the class name.
            result = SendResult(False, None, f"sender error ({type(exc).__name__})", True)
            status = await record_attempt(db, delivery, result)
            logger.warning("Feedback webhook delivery %s: %s", delivery["id"], result.error)
        outcomes[status] = outcomes.get(status, 0) + 1
    return outcomes


async def deliver_feedback_webhooks(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Cron job (every minute, ``supoclip_cron``): send due deliveries until none are left."""
    from ..database import AsyncSessionLocal

    totals: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        for _ in range(20):
            outcomes = await deliver_due(db)
            for status, count in outcomes.items():
                totals[status] = totals.get(status, 0) + count
            if sum(outcomes.values()) < BATCH_SIZE:
                break
    return totals


def schedule_immediate_delivery(submission_id: str) -> None:
    """Try a new submission's deliveries now, after the response; the cron worker retries."""

    async def run() -> None:
        from ..database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                await deliver_due(db, submission_id=submission_id)
        except Exception:
            logger.exception("Immediate feedback delivery for %s failed", submission_id)

    # The durable cron queue handles overflow; do not accumulate waiting tasks.
    if len(_background) >= 4:
        return
    task = asyncio.get_running_loop().create_task(run())
    _background.add(task)
    task.add_done_callback(_background.discard)


async def retry_delivery(db: AsyncSession, delivery_id: str) -> bool:
    """An admin retries a failed or cancelled delivery: it gets a fresh set of attempts."""
    result = await db.execute(
        text(
            """
            UPDATE feedback_webhook_deliveries
            SET status = 'pending', attempts = 0, next_attempt_at = clock_timestamp(), lease_expires_at = NULL, lease_token = NULL, updated_at = NOW()
            WHERE id = :id AND status IN ('failed', 'cancelled') AND NOT is_test
            """
        ),
        {"id": delivery_id},
    )
    await db.commit()
    return bool(result.rowcount)


TEST_MESSAGE = "This is a test message from the SupoClip feedback inbox. Your webhook works."
HERMES_TEST_CATEGORY = "test"
HERMES_TEST_MESSAGE = "Test from SupoClip admin"


async def send_test(db: AsyncSession, webhook_id: str, admin: dict[str, Any]) -> dict[str, Any] | None:
    """Send a sample submission now and record it in the webhook's delivery log."""
    destination = await load_destination(db, webhook_id)
    if destination is None:
        return None
    delivery_id = str(uuid.uuid4())
    token = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO feedback_webhook_deliveries (id, webhook_id, is_test, attempts, status, lease_expires_at, lease_token) "
            "VALUES (:id, :webhook, TRUE, 1, 'pending', NOW() + INTERVAL '2 minutes', :token)"
        ),
        {"id": delivery_id, "webhook": webhook_id, "token": token},
    )
    await db.commit()
    sample = {
        "id": f"test-{delivery_id[:8]}",
        "user_id": admin.get("id"),
        "email": admin.get("email"),
        "category": "general",
        "message": TEST_MESSAGE,
        "status": "open",
        "platform": "web",
        "page_url": "/admin/feedback/webhooks",
        "created_at": datetime.now(timezone.utc),
    }
    if destination["kind"] == HERMES_KIND:
        sample.update(category=HERMES_TEST_CATEGORY, message=HERMES_TEST_MESSAGE)
    result = await send(destination, EVENT_TEST, delivery_id, sample)
    # A test is one attempt: never retried.
    status = await record_attempt(db, {"id": delivery_id, "attempts": 1, "lease_token": token},
                                  SendResult(result.ok, result.status_code, result.error, False, None,
                                             result.duration_ms))
    return {"id": delivery_id, "status": status, "status_code": result.status_code, "error": result.error}


async def webhook_deliveries(db: AsyncSession, webhook_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT d.id, d.submission_id, d.is_test, d.status, d.attempts, d.next_attempt_at, d.last_status_code,
                   d.last_error, d.delivered_at, d.created_at, s.category,
                   COALESCE(
                     (SELECT json_agg(json_build_object(
                         'attempt', a.attempt, 'status_code', a.status_code, 'error', a.error,
                         'duration_ms', a.duration_ms, 'attempted_at', a.attempted_at) ORDER BY a.attempted_at)
                      FROM feedback_webhook_attempts a WHERE a.delivery_id = d.id),
                     '[]'::json) AS attempt_log
            FROM feedback_webhook_deliveries d
            LEFT JOIN feedback_submissions s ON s.id = d.submission_id
            WHERE d.webhook_id = :id
            ORDER BY d.created_at DESC
            LIMIT :limit
            """
        ),
        {"id": webhook_id, "limit": limit},
    )
    return [dict(row) for row in rows.mappings().all()]
