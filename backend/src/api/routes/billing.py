import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import USER_ID_HEADER, get_signed_user_id
from ...config import get_config
from ...database import get_db
from ...models import User
from ...services.subscription_email_service import SubscriptionEmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


class SubscriptionEmailRequest(BaseModel):
    event: Literal["subscribed", "unsubscribed"]


def _get_user_id_from_headers(request: Request) -> str:
    config = get_config()
    if config.monetization_enabled:
        return get_signed_user_id(request, config)

    user_id = request.headers.get("user_id") or request.headers.get(USER_ID_HEADER)
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")

    return user_id


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

    service = SubscriptionEmailService(get_config())

    try:
        if body.event == "subscribed":
            email_result = await service.send_subscribed_email(user)
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

    return {"status": "ok", "provider": "resend", "email": email_result}
