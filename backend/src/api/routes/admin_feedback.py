"""Admin feedback inbox and webhook destinations. Every route requires an admin."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...admin_auth import require_admin_user
from ...config import get_config
from ...database import get_db
from ...feedback import CATEGORIES, ENV_WEBHOOKS
from ...feedback import delivery, service, webhooks
from ...feedback.url_safety import UnsafeWebhookUrl

router = APIRouter(prefix="/admin", tags=["admin"])

Category = Literal["bug", "feature", "general", "sales", "cancellation"]


async def _admin(request: Request, db: AsyncSession) -> str:
    return await require_admin_user(request, db, get_config())


def _invalid(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": getattr(exc, "code", "invalid"), "message": str(exc)})


# --- Submissions -----------------------------------------------------------------


@router.get("/feedback")
async def list_feedback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    category: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = Query(default=None, max_length=200),
    since: Optional[datetime] = Query(default=None, alias="from"),
    until: Optional[datetime] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1, le=10000),
):
    await _admin(request, db)
    return await service.list_submissions(
        db, category=category, status=status, query=q, since=since, until=until, page=page
    )


@router.get("/feedback/{submission_id}")
async def get_feedback(submission_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _admin(request, db)
    submission = await service.get_submission(db, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return {"submission": submission}


class StatusUpdate(BaseModel):
    status: Literal["open", "in_progress", "resolved"]


@router.patch("/feedback/{submission_id}")
async def update_feedback(
    submission_id: str, payload: StatusUpdate, request: Request, db: AsyncSession = Depends(get_db)
):
    admin_id = await _admin(request, db)
    if not await service.set_status(db, submission_id, payload.status, admin_id):
        raise HTTPException(status_code=404, detail="Submission not found")
    return {"submission": await service.get_submission(db, submission_id)}


class NoteCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=service.MAX_NOTE_LENGTH)


@router.post("/feedback/{submission_id}/notes", status_code=201)
async def add_feedback_note(
    submission_id: str, payload: NoteCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    admin_id = await _admin(request, db)
    try:
        note = await service.add_note(db, submission_id, payload.body, admin_id)
    except ValueError as exc:
        raise _invalid(exc)
    if note is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return {"note": note}


@router.post("/feedback/deliveries/{delivery_id}/retry")
async def retry_feedback_delivery(delivery_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _admin(request, db)
    if not await delivery.retry_delivery(db, delivery_id):
        raise HTTPException(status_code=409, detail="Only failed or cancelled deliveries can be retried")
    submission_id = (
        await db.execute(
            text("SELECT submission_id FROM feedback_webhook_deliveries WHERE id = :id"), {"id": delivery_id}
        )
    ).scalar()
    if submission_id:
        delivery.schedule_immediate_delivery(submission_id)
    return {"status": "pending"}


# --- Webhooks --------------------------------------------------------------------


def _env_status() -> list[dict]:
    """Which pre-inbox environment webhooks are set (never their values)."""
    config = get_config()
    return [
        {"env_key": key, "name": name, "categories": list(categories), "configured": bool(getattr(config, key.lower(), None))}
        for key, name, categories in ENV_WEBHOOKS
    ]


@router.get("/feedback-webhooks")
async def list_feedback_webhooks(request: Request, db: AsyncSession = Depends(get_db)):
    await _admin(request, db)
    imported = {
        row["env_key"]: row["imported_at"]
        for row in (await db.execute(text("SELECT env_key, imported_at FROM feedback_webhook_env_imports"))).mappings()
    }
    return {
        "webhooks": await webhooks.list_webhooks(db),
        "categories": list(CATEGORIES),
        "environment": [{**item, "imported_at": imported.get(item["env_key"])} for item in _env_status()],
    }


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=webhooks.MAX_NAME_LENGTH)
    kind: Literal["discord", "json"]
    # Length and shape are checked in code: a validation error would log the value.
    url: str
    categories: list[Category] = Field(..., min_length=1)
    enabled: bool = True


@router.post("/feedback-webhooks", status_code=201)
async def create_feedback_webhook(payload: WebhookCreate, request: Request, db: AsyncSession = Depends(get_db)):
    admin_id = await _admin(request, db)
    try:
        webhook, secret = await webhooks.create_webhook(
            db, name=payload.name, kind=payload.kind, url=payload.url, categories=payload.categories,
            enabled=payload.enabled, admin_id=admin_id,
        )
    except (webhooks.InvalidWebhook, UnsafeWebhookUrl) as exc:
        raise _invalid(exc)
    return {"webhook": webhook, "secret": secret}


class WebhookUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=webhooks.MAX_NAME_LENGTH)
    url: Optional[str] = Field(default=None, description="Empty keeps the current URL")
    categories: Optional[list[Category]] = Field(default=None, min_length=1)
    enabled: Optional[bool] = None


@router.patch("/feedback-webhooks/{webhook_id}")
async def update_feedback_webhook(
    webhook_id: str, payload: WebhookUpdate, request: Request, db: AsyncSession = Depends(get_db)
):
    admin_id = await _admin(request, db)
    try:
        webhook = await webhooks.update_webhook(
            db, webhook_id, admin_id=admin_id, name=payload.name, url=payload.url,
            categories=payload.categories, enabled=payload.enabled,
        )
    except (webhooks.InvalidWebhook, UnsafeWebhookUrl) as exc:
        raise _invalid(exc)
    if webhook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"webhook": webhook}


@router.post("/feedback-webhooks/{webhook_id}/rotate-secret")
async def rotate_feedback_webhook_secret(webhook_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    admin_id = await _admin(request, db)
    secret = await webhooks.rotate_secret(db, webhook_id, admin_id)
    if secret is None:
        raise HTTPException(status_code=404, detail="JSON webhook not found")
    return {"secret": secret}


@router.post("/feedback-webhooks/{webhook_id}/test")
async def test_feedback_webhook(webhook_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    admin_id = await _admin(request, db)
    admin = (
        await db.execute(text("SELECT id, email FROM users WHERE id = :id"), {"id": admin_id})
    ).mappings().one()
    result = await delivery.send_test(db, webhook_id, dict(admin))
    if result is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"delivery": result}


@router.get("/feedback-webhooks/{webhook_id}/deliveries")
async def list_feedback_webhook_deliveries(webhook_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _admin(request, db)
    if await webhooks.get_webhook(db, webhook_id) is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"deliveries": await delivery.webhook_deliveries(db, webhook_id)}


@router.delete("/feedback-webhooks/{webhook_id}")
async def delete_feedback_webhook(webhook_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _admin(request, db)
    try:
        deleted = await webhooks.delete_webhook(db, webhook_id)
    except webhooks.ManagedWebhook as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)})
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted"}
