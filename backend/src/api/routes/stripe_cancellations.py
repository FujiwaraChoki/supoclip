"""Stripe webhook for cancellation reviews: POST /webhooks/stripe/cancellations/{account_id}.

Separate from the billing webhook (frontend), with its own endpoint and signing
secret per Stripe account, so it can never delay or fail billing. It stores the
event and answers; the cron worker does the investigation.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from ...cancellation_reviews import store
from ...cancellation_reviews.intake import (
    ACCOUNT_ID_PATTERN,
    SignatureError,
    parse_cancellation_event,
    parse_webhook_secrets,
    verify_stripe_signature,
)
from ...config import get_config
from ...database import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

MAX_BODY_BYTES = 512 * 1024


@router.post("/webhooks/stripe/cancellations/{account_id}", include_in_schema=False)
async def stripe_cancellation_webhook(account_id: str, request: Request):
    config = get_config()
    secrets = parse_webhook_secrets(config.stripe_cancellation_webhook_secrets).get(account_id)
    if not ACCOUNT_ID_PATTERN.match(account_id) or not secrets:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    try:
        verify_stripe_signature(body, request.headers.get("stripe-signature"), secrets)
    except SignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature") from None
    try:
        event = parse_cancellation_event(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event") from None
    if event is None:
        return {"received": True, "stored": False}
    if event.account and event.account != account_id:
        raise HTTPException(status_code=400, detail="Event belongs to another account")
    async with AsyncSessionLocal() as db:
        # Another product's customers in a shared Stripe account are never stored.
        if account_id != config.stripe_source_account_id and not await store.is_supoclip_customer(
            db, account_id, event.customer_id
        ):
            return {"received": True, "stored": False}
        stored = await store.insert_event(db, account_id, event)
    logger.info("Stripe cancellation event %s (%s) on %s stored=%s", event.event_id, event.event_type,
                event.subscription_id, stored)
    return {"received": True, "stored": stored}
