"""Feedback submission endpoint — forwards to Discord webhook."""

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...auth_headers import get_signed_user_id
from ...config import get_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])

VALID_CATEGORIES = {"bug", "feature", "general", "sales"}

CATEGORY_COLORS = {
    "bug": 0xEF4444,
    "feature": 0x3B82F6,
    "general": 0xA855F7,
    "sales": 0x22C55E,
}

CATEGORY_LABELS = {
    "bug": "Bug Report",
    "feature": "Feature Request",
    "general": "General Feedback",
    "sales": "Sales Inquiry",
}

# Sales category routes to its own webhook; everything else goes to the feedback webhook
SALES_CATEGORIES = {"sales"}


class FeedbackRequest(BaseModel):
    category: str = Field(..., description="One of: bug, feature, general")
    message: str = Field(..., min_length=1, max_length=2000)


@router.post("/feedback")
async def submit_feedback(body: FeedbackRequest, request: Request):
    config = get_config()
    if config.monetization_enabled:
        user_id = get_signed_user_id(request, config)
    else:
        user_id = request.headers.get("user_id")

    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")

    is_sales = body.category in SALES_CATEGORIES
    webhook_url = (
        config.discord_sales_webhook_url if is_sales else config.discord_feedback_webhook_url
    )

    if not webhook_url:
        raise HTTPException(status_code=400, detail="Feedback is not configured")

    if body.category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Must be one of: {', '.join(VALID_CATEGORIES)}",
        )

    embed = {
        "title": CATEGORY_LABELS.get(body.category, body.category),
        "description": body.message,
        "color": CATEGORY_COLORS.get(body.category, 0x6B7280),
        "fields": [
            {"name": "Category", "value": body.category, "inline": True},
            {"name": "User ID", "value": user_id or "anonymous", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                webhook_url,
                json={"embeds": [embed]},
                timeout=10,
            )
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Discord webhook failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Failed to submit feedback"
        ) from e

    return {"status": "ok"}
