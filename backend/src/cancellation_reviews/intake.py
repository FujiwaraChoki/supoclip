"""Stripe webhook verification and the trimmed subscription snapshot a review needs.

No Stripe API key is involved: the signing secret proves the event came from the
account, and the event carries the subscription as it was at that moment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SIGNATURE_TOLERANCE_SECONDS = 300
RELEVANT_EVENT_TYPES = frozenset({"customer.subscription.updated", "customer.subscription.deleted"})
# A change to any of these can start, withdraw, end or annotate a cancellation.
CANCELLATION_FIELDS = frozenset({"cancel_at", "cancel_at_period_end", "canceled_at", "cancellation_details", "status"})
LIVE_STATUSES = frozenset({"active", "trialing", "past_due"})
MIGRATION_OPERATION_METADATA = "billing_migration_operation"
SYNTHETIC_TEST_METADATA = "supoclip_synthetic_test"
ACCOUNT_ID_PATTERN = re.compile(r"^acct_[A-Za-z0-9]+$")


class SignatureError(ValueError):
    pass


def parse_webhook_secrets(raw: str | None) -> dict[str, list[str]]:
    """``acct_A:whsec_1,acct_A:whsec_2,acct_B:whsec_3`` -> {account: [secrets]}.

    Two secrets for one account cover a secret roll (Stripe signs with both).
    """
    secrets: dict[str, list[str]] = {}
    for part in (raw or "").split(","):
        account, _, secret = part.strip().partition(":")
        account, secret = account.strip(), secret.strip()
        if ACCOUNT_ID_PATTERN.match(account) and secret.startswith("whsec_"):
            secrets.setdefault(account, []).append(secret)
    return secrets


def verify_stripe_signature(
    payload: bytes, header: str | None, secrets: list[str], *, now: float | None = None,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
) -> None:
    """Stripe's scheme: ``t=<ts>,v1=<hex hmac-sha256 of "<ts>.<payload>">``, fresh within tolerance."""
    if not header or not secrets:
        raise SignatureError("missing signature or secret")
    timestamp = None
    signatures: list[str] = []
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not timestamp.isdigit() or not signatures:
        raise SignatureError("malformed signature header")
    if abs((now if now is not None else time.time()) - int(timestamp)) > tolerance:
        raise SignatureError("signature timestamp outside tolerance")
    signed = timestamp.encode() + b"." + payload
    for secret in secrets:
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if any(hmac.compare_digest(expected, candidate) for candidate in signatures):
            return
    raise SignatureError("no matching signature")


def _object_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _scheduled(fields: dict[str, Any]) -> bool:
    return bool(fields.get("cancel_at_period_end")) or fields.get("cancel_at") is not None


@dataclass(frozen=True)
class CancellationEvent:
    event_id: str
    event_type: str
    created: datetime
    livemode: bool
    account: str | None
    subscription_id: str
    customer_id: str
    snapshot: dict[str, Any]


def subscription_snapshot(subscription: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """The fields a review uses. Customer-entered text stays here and nowhere else."""
    items = ((subscription.get("items") or {}).get("data") or [])
    first = items[0] if items else {}
    price = first.get("price") or {}
    recurring = price.get("recurring") or {}
    details = subscription.get("cancellation_details") or {}
    metadata = subscription.get("metadata") or {}
    previous = previous or {}
    previous_details = previous.get("cancellation_details") if isinstance(previous.get("cancellation_details"), dict) else None
    return {
        "status": subscription.get("status"),
        "cancel_at": subscription.get("cancel_at"),
        "cancel_at_period_end": bool(subscription.get("cancel_at_period_end")),
        "canceled_at": subscription.get("canceled_at"),
        "ended_at": subscription.get("ended_at"),
        "start_date": subscription.get("start_date"),
        "created": subscription.get("created"),
        "trial_end": subscription.get("trial_end"),
        "current_period_start": first.get("current_period_start") or subscription.get("current_period_start"),
        "current_period_end": first.get("current_period_end") or subscription.get("current_period_end"),
        "price_ids": [p for p in ((item.get("price") or {}).get("id") for item in items) if p],
        "interval": recurring.get("interval"),
        "amount": price.get("unit_amount"),
        "currency": price.get("currency"),
        "cancellation_reason": details.get("reason"),
        "cancellation_feedback": details.get("feedback"),
        "cancellation_comment": details.get("comment"),
        "migration_operation": metadata.get(MIGRATION_OPERATION_METADATA),
        "synthetic_test": str(metadata.get(SYNTHETIC_TEST_METADATA, "")).lower() == "true",
        "previous": {
            "keys": sorted(k for k in previous if k in CANCELLATION_FIELDS),
            "scheduled": _scheduled({**subscription, **previous}) if previous else None,
            "status": previous.get("status"),
            "feedback": previous_details.get("feedback") if previous_details else None,
            "comment_present": bool(previous_details.get("comment")) if previous_details else None,
        },
    }


def parse_cancellation_event(payload: bytes) -> CancellationEvent | None:
    """The event when it can start, change or end a cancellation; None for everything else."""
    event = json.loads(payload)
    if not isinstance(event, dict) or event.get("object") != "event":
        raise ValueError("not a Stripe event")
    event_type = event.get("type")
    if event_type not in RELEVANT_EVENT_TYPES:
        return None
    data = event.get("data") or {}
    subscription = data.get("object") or {}
    if subscription.get("object") != "subscription":
        return None
    previous = data.get("previous_attributes") if isinstance(data.get("previous_attributes"), dict) else None
    if event_type == "customer.subscription.updated":
        changed = set(previous or {}) & CANCELLATION_FIELDS
        if not changed:
            return None
        # A status change alone (trial -> active, past_due -> active) is not about cancelling.
        if changed == {"status"} and subscription.get("status") in LIVE_STATUSES and not _scheduled(subscription):
            return None
    subscription_id = _object_id(subscription)
    customer_id = _object_id(subscription.get("customer"))
    event_id = event.get("id")
    created = event.get("created")
    if not (subscription_id and customer_id and isinstance(event_id, str) and isinstance(created, int)):
        raise ValueError("event is missing ids")
    return CancellationEvent(
        event_id=event_id,
        event_type=event_type,
        created=datetime.fromtimestamp(created, timezone.utc),
        livemode=bool(event.get("livemode")),
        account=event.get("account") if isinstance(event.get("account"), str) else None,
        subscription_id=subscription_id,
        customer_id=customer_id,
        snapshot=subscription_snapshot(subscription, previous),
    )
