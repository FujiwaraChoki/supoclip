import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...models import User
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
