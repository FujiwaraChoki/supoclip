"""Feedback submissions: stored in the inbox, then sent to the admin's webhooks (docs/feedback.md)."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import get_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...feature_flags import client_platform
from ...feedback import CATEGORIES
from ...feedback.delivery import schedule_immediate_delivery
from ...feedback.service import FeedbackRateLimited, FeedbackUserGone, SubmissionInput, submit_feedback as store_submission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])

APP_VERSION_HEADER = "x-supoclip-app-version"


class FeedbackRequest(BaseModel):
    category: str = Field(..., description="One of: bug, feature, general, sales, cancellation")
    message: str = Field(..., min_length=1, max_length=2000)
    page_url: Optional[str] = Field(default=None, max_length=2000, description="Page the user was on")
    locale: Optional[str] = Field(default=None, max_length=20)
    app_version: Optional[str] = Field(default=None, max_length=50)


@router.post("/feedback")
async def submit_feedback(body: FeedbackRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = get_authenticated_user_id(request, get_config())
    if body.category not in CATEGORIES:
        # Keep the existing 422 + string detail contract for signed mobile clients.
        raise HTTPException(status_code=422, detail=f"Invalid category. Must be one of: {', '.join(CATEGORIES)}")
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="Message is empty")
    try:
        stored = await store_submission(
            db,
            SubmissionInput(
                category=body.category,
                message=body.message,
                user_id=user_id,
                page_url=body.page_url,
                platform=client_platform(request),
                user_agent=request.headers.get("user-agent"),
                app_version=body.app_version or request.headers.get(APP_VERSION_HEADER),
                locale=body.locale,
            ),
        )
    except FeedbackUserGone:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    except FeedbackRateLimited:
        raise HTTPException(status_code=429, detail="Too many messages. Try again in a few minutes.")
    if stored["deliveries"]:
        schedule_immediate_delivery(stored["id"])
    return {"status": "ok", "id": stored["id"]}
