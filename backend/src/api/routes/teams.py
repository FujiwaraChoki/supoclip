"""Team helper routes (invitation emails, team AI edit/billing usage lookup)."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id, resolve_auth_context
from ...config import get_config
from ...database import get_db
from ...models import User
from ...repositories.team_repository import TEAM_MANAGER_ROLES, TeamRepository
from ...services.ai_edit_allowance_service import AIEditAllowanceService
from ...services.billing_service import BillingService
from ...services.team_email_service import TeamEmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams", tags=["teams"])


class TeamInviteEmailRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=64)
    recipient_email: str = Field(min_length=3, max_length=255)
    team_name: str = Field(min_length=1, max_length=120)
    role: Literal["admin", "member"] = "member"
    invite_url: str = Field(min_length=1, max_length=2000)


@router.post("/invite-email")
async def send_team_invite_email(
    body: TeamInviteEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a team invitation email on behalf of an authenticated team manager."""
    config = get_config()
    user_id = get_authenticated_user_id(request, config)

    role = await TeamRepository.get_member_role(db, body.team_id, user_id)
    if role not in TEAM_MANAGER_ROLES:
        raise HTTPException(
            status_code=403, detail="Only team owners and admins can send invitations"
        )

    user_result = await db.execute(select(User).where(User.id == user_id))
    inviter = user_result.scalar_one_or_none()
    inviter_name = (
        (inviter.name if inviter and inviter.name else None)
        or (inviter.email if inviter and inviter.email else None)
        or "A teammate"
    )

    service = TeamEmailService(config)
    if not service.is_configured:
        return {"status": "skipped", "reason": "ses_not_configured"}

    try:
        result = await service.send_invitation_email(
            recipient=body.recipient_email,
            team_name=body.team_name,
            inviter_name=inviter_name,
            role=body.role,
            invite_url=body.invite_url,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to send team invitation email for team %s", body.team_id)
        raise HTTPException(
            status_code=502, detail="Failed to send team invitation email"
        ) from exc

    return {"status": "ok", "provider": "ses", "email": result}


@router.get("/{team_id}/usage")
async def get_team_usage(
    team_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return pooled video-minute and AI-edit usage for a team the caller belongs to."""
    config = get_config()
    auth_ctx = await resolve_auth_context(request, db, config)
    role = await TeamRepository.get_member_role(db, team_id, auth_ctx.user_id)
    if not role:
        raise HTTPException(status_code=403, detail="Not a member of this team")
    if auth_ctx.via_api_key and auth_ctx.team_id != team_id:
        raise HTTPException(status_code=403, detail="API key is not scoped to this team")

    billing_service = BillingService(db, config)
    billing = await billing_service.get_usage_summary(
        auth_ctx.user_id, team_id=team_id
    )
    ai_edits = await AIEditAllowanceService(db, config).summary(
        auth_ctx.user_id, team_id=team_id
    )
    member_usage = await billing_service.get_team_member_usage_map(
        team_id,
        only_user_id=None if role in TEAM_MANAGER_ROLES else auth_ctx.user_id,
    )
    return {"billing": billing, "ai_edits": ai_edits, "member_usage": member_usage}
