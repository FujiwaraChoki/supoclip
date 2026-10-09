import logging
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...models import User
from ...services.affiliate_email_service import AffiliateEmailService
from ...services.password_reset_email_service import PasswordResetEmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/account", tags=["account"])


class PasswordResetEmailRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        return value


@router.post("/password-reset-email")
async def send_password_reset_email(
    body: PasswordResetEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Only the frontend server (via signed session headers) may trigger this;
    # API keys are deliberately not accepted.
    config = get_config()
    user_id = get_authenticated_user_id(request, config)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.email:
        raise HTTPException(status_code=400, detail="User email is missing")

    service = PasswordResetEmailService(config)

    try:
        email_result = await service.send_reset_email(user, body.url)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to send password reset email for user %s", user_id)
        raise HTTPException(
            status_code=502, detail="Failed to send password reset email"
        ) from exc

    return {"status": "ok", "provider": "ses", "email": email_result}


class AffiliateEmailRequest(BaseModel):
    event: Literal["applied", "approved", "declined"]
    slug: str = Field(min_length=1, max_length=20)
    platform: Optional[str] = Field(default=None, max_length=40)
    profile_url: Optional[str] = Field(default=None, max_length=500)
    audience_size: Optional[str] = Field(default=None, max_length=40)
    video_url: Optional[str] = Field(default=None, max_length=500)
    promotion_plan: Optional[str] = Field(default=None, max_length=1000)
    decline_reason: Optional[str] = Field(default=None, max_length=1000)


@router.post("/affiliate-email")
async def send_affiliate_email(
    body: AffiliateEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Creator program emails, triggered by the frontend server for the applicant's account.

    `applied` notifies every admin; `approved` and `declined` go to the applicant.
    """
    config = get_config()
    user_id = get_authenticated_user_id(request, config)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    service = AffiliateEmailService(config)
    try:
        if body.event == "applied":
            admins = await db.execute(
                select(User).where(User.is_admin.is_(True), User.email.is_not(None))
            )
            for admin in admins.scalars().all():
                await service.send_applied_email(admin.email, user, body.model_dump())
        elif not user.email:
            raise HTTPException(status_code=400, detail="User email is missing")
        elif body.event == "approved":
            await service.send_approved_email(user, body.slug)
        else:
            await service.send_declined_email(user, body.decline_reason)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to send affiliate %s email for user %s", body.event, user_id)
        raise HTTPException(status_code=502, detail="Failed to send affiliate email") from exc

    return {"status": "ok"}
