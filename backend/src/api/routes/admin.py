from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...admin_auth import require_admin_user
from ...config import Config
from ...database import get_db
from ...services.youtube_cookie_manager import YouTubeCookieManager
from ...youtube_auth_types import (
    CreateYouTubeCookieAccountRequest,
    UploadYouTubeCookiesRequest,
    UpdateYouTubeCookieAccountRequest,
)

router = APIRouter(prefix="/admin", tags=["admin"])
config = Config()
cookie_manager = YouTubeCookieManager()


@router.get("/youtube-cookie-accounts")
async def list_youtube_cookie_accounts(
    request: Request, db: AsyncSession = Depends(get_db)
):
    await require_admin_user(request, db, config)
    accounts = await cookie_manager.list_accounts()
    payload = [{"account": account.model_dump(mode="json")} for account in accounts]
    return {"accounts": payload}


@router.post("/youtube-cookie-accounts")
async def create_youtube_cookie_account(
    request: Request,
    body: CreateYouTubeCookieAccountRequest,
    db: AsyncSession = Depends(get_db),
):
    admin_user_id = await require_admin_user(request, db, config)
    account = await cookie_manager.create_account(
        label=body.label,
        email_hint=body.email_hint,
        priority=body.priority,
        created_by_user_id=admin_user_id,
    )
    return {"account": account.model_dump(mode="json")}


@router.patch("/youtube-cookie-accounts/{account_id}")
async def update_youtube_cookie_account(
    account_id: str,
    request: Request,
    body: UpdateYouTubeCookieAccountRequest,
    db: AsyncSession = Depends(get_db),
):
    await require_admin_user(request, db, config)
    account = await cookie_manager.update_account(
        account_id=account_id,
        label=body.label,
        email_hint=body.email_hint,
        priority=body.priority,
        status=body.status,
        action=body.action,
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"account": account.model_dump(mode="json")}


@router.post("/youtube-cookie-accounts/{account_id}/verify")
async def verify_youtube_cookie_account(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin_user(request, db, config)
    account = await cookie_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    verified = await cookie_manager.verify_account(account_id)
    refreshed_account = await cookie_manager.get_account(account_id)
    return {
        "account_id": account_id,
        "verified": verified,
        "account": (
            refreshed_account.model_dump(mode="json") if refreshed_account else None
        ),
    }


@router.post("/youtube-cookie-accounts/{account_id}/manual-cookies")
async def upload_youtube_manual_cookies(
    account_id: str,
    request: Request,
    body: UploadYouTubeCookiesRequest,
    db: AsyncSession = Depends(get_db),
):
    admin_user_id = await require_admin_user(request, db, config)
    account = await cookie_manager.upload_manual_cookies(
        account_id=account_id,
        cookies_text=body.cookies_text,
        uploaded_by_user_id=admin_user_id,
    )
    return {"account": account.model_dump(mode="json")}


@router.get("/youtube-cookie-accounts/{account_id}/manual-cookies")
async def get_youtube_manual_cookies(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin_user(request, db, config)
    cookies_text = await cookie_manager.get_manual_cookies_text(account_id)
    return {"cookies_text": cookies_text}


@router.get("/youtube-cookie-accounts/{account_id}/events")
async def list_youtube_cookie_events(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin_user(request, db, config)
    account = await cookie_manager.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    events = await cookie_manager.list_events(account_id)
    return {"events": [event.model_dump(mode="json") for event in events]}
