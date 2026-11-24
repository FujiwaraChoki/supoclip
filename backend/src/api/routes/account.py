import logging
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...models import User
from ...services.affiliate_email_service import AffiliateEmailService
from ...services.email_change_email_service import EmailChangeEmailService
from ...services.password_reset_email_service import PasswordResetEmailService
from ...services.verification_email_service import VerificationEmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/account", tags=["account"])


class EmailLinkRequest(BaseModel):
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
    body: EmailLinkRequest,
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


@router.post("/verification-email")
async def send_verification_email(
    body: EmailLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Only the frontend server may supply Better Auth's generated link.
    config = get_config()
    user_id = get_authenticated_user_id(request, config)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.emailVerified:
        return {"status": "ok"}

    try:
        await VerificationEmailService(config).send_verification_email(user, body.url)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to send verification email for user %s", user_id)
        raise HTTPException(status_code=502, detail="Failed to send verification email") from exc
    return {"status": "ok"}


class AffiliateEmailRequest(BaseModel):
    event: Literal["applied", "approved", "declined"]
    slug: str = Field(min_length=1, max_length=20)
    platform: Optional[str] = Field(default=None, max_length=40)
    profile_url: Optional[str] = Field(default=None, max_length=500)
    audience_size: Optional[str] = Field(default=None, max_length=40)
    video_url: Optional[str] = Field(default=None, max_length=500)
    promotion_plan: Optional[str] = Field(default=None, max_length=1000)
    decline_reason: Optional[str] = Field(default=None, max_length=1000)
    country: Optional[str] = Field(default=None, max_length=2)
    # Approval: whether the creator lives where commission can be paid out.
    commission: bool = True


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
            await service.send_approved_email(user, body.slug, body.commission)
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


# One address, no whitespace or display name: the frontend has already validated it.
EMAIL_PATTERN = r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$"


class EmailChangeEmailRequest(BaseModel):
    kind: Literal["current_code", "new_code", "changed"]
    code: Optional[str] = Field(default=None, pattern=r"^[0-9]{6}$")
    new_email: Optional[str] = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    old_email: Optional[str] = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)

    @model_validator(mode="after")
    def check_fields(self) -> "EmailChangeEmailRequest":
        if self.kind != "changed" and not self.code:
            raise ValueError("code is required")
        if self.kind != "current_code" and not self.new_email:
            raise ValueError("new_email is required")
        if self.kind == "changed" and not self.old_email:
            raise ValueError("old_email is required")
        return self


@router.post("/email-change-email")
async def send_email_change_email(
    body: EmailChangeEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Codes and the "email changed" notice for a self-serve email change.

    Only the frontend server (signed session headers, no API keys) calls this.
    The current-address code always goes to the address on file.
    """
    config = get_config()
    user_id = get_authenticated_user_id(request, config)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.email:
        raise HTTPException(status_code=400, detail="User email is missing")

    # Only mail what an open request or a just-made change expects, so this
    # can't relay to arbitrary addresses.
    notice_id = None
    if body.kind == "changed":
        # Once per change in email_change_log, within an hour of it.
        claimed = await db.execute(
            text(
                "UPDATE email_change_log SET notified_at = now() WHERE id = ("
                "SELECT id FROM email_change_log WHERE user_id = :user_id AND old_email = :old_email "
                "AND new_email = :new_email AND notified_at IS NULL AND changed_at > now() - interval '1 hour' "
                "ORDER BY changed_at DESC LIMIT 1) AND notified_at IS NULL RETURNING id"
            ),
            {"user_id": user_id, "old_email": body.old_email, "new_email": body.new_email},
        )
        row = claimed.first()
        await db.commit()
        notice_id = row[0] if row else None
        expected = notice_id is not None and body.new_email.lower() == user.email.lower()
    else:
        stage, address = ("verify_current", None) if body.kind == "current_code" else ("verify_new", body.new_email)
        open_request = await db.execute(
            text(
                "SELECT 1 FROM email_change_requests WHERE user_id = :user_id AND stage = :stage "
                "AND (CAST(:address AS TEXT) IS NULL OR new_email = :address)"
            ),
            {"user_id": user_id, "stage": stage, "address": address},
        )
        expected = open_request.first() is not None
    if not expected:
        raise HTTPException(status_code=409, detail="No matching email change request")

    service = EmailChangeEmailService(config)
    try:
        if body.kind == "current_code":
            await service.send_code(user, user.email, body.code, new_address=False)
        elif body.kind == "new_code":
            await service.send_code(user, body.new_email, body.code, new_address=True)
        else:
            await service.send_changed_notice(user, body.old_email, body.new_email)
    except Exception as exc:
        if notice_id is not None:
            await db.execute(
                text("UPDATE email_change_log SET notified_at = NULL WHERE id = :id"), {"id": notice_id}
            )
            await db.commit()
        if isinstance(exc, RuntimeError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        logger.exception("Failed to send email change %s email for user %s", body.kind, user_id)
        raise HTTPException(status_code=502, detail="Failed to send email") from exc
    return {"status": "ok"}
