import logging
from typing import Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...models import User
from ...services.subscription_email_service import SubscriptionEmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


class SubscriptionEmailRequest(BaseModel):
    event: Literal["subscribed", "unsubscribed", "cancellation_scheduled", "checkout_recovery"]
    recovery_url: Optional[str] = None


# Stripe hosts the link that recreates an expired Checkout Session.
_RECOVERY_HOSTS = {"buy.stripe.com", "checkout.stripe.com"}
_PAID_OR_PENDING_STATUSES = {"active", "trialing", "past_due"}


def _valid_recovery_url(url: Optional[str]) -> bool:
    if not url or len(url) > 2048 or any(c in url for c in "{}<>\"\'\\ \n\r\t"):
        return False
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname in _RECOVERY_HOSTS
        and parts.port is None
        and not parts.username
        and not parts.password
    )


def _get_user_id_from_headers(request: Request) -> str:
    config = get_config()
    return get_authenticated_user_id(request, config)


@router.post("/subscription-email")
async def send_subscription_email(
    body: SubscriptionEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id_from_headers(request)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.email:
        raise HTTPException(status_code=400, detail="User email is missing")

    if body.event == "checkout_recovery" and not _valid_recovery_url(body.recovery_url):
        raise HTTPException(status_code=400, detail="Invalid recovery URL")

    service = SubscriptionEmailService(get_config())

    try:
        if body.event == "subscribed":
            email_result = await service.send_subscribed_email(user)
        elif body.event == "cancellation_scheduled":
            # Re-check current state so a resumed or ended subscription does not
            # receive an outdated cancellation notice.
            if (
                not user.subscription_cancel_at
                or user.subscription_provider != "stripe"
                or user.subscription_status not in {"active", "trialing", "past_due"}
            ):
                return {"status": "skipped", "reason": "no_scheduled_cancellation"}
            email_result = await service.send_cancellation_scheduled_email(user, db)
        elif body.event == "checkout_recovery":
            # The user may have finished upgrading another way since the Checkout expired.
            if (
                user.plan != "free"
                or user.subscription_status in _PAID_OR_PENDING_STATUSES
                or user.subscription_provider in {"apple", "affiliate"}
            ):
                return {"status": "skipped", "reason": "already_subscribed"}
            email_result = await service.send_checkout_recovery_email(
                user, body.recovery_url, db
            )
            if email_result.get("skipped"):
                return {"status": "skipped", "reason": email_result.get("reason")}
        else:
            email_result = await service.send_unsubscribed_email(user)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Failed to send %s email for user %s", body.event, user_id
        )
        raise HTTPException(
            status_code=502, detail="Failed to send subscription email"
        ) from exc

    return {"status": "ok", "provider": "ses", "email": email_result}
