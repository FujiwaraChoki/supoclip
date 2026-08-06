"""Social account connections, generated copy, scheduling, and publishing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...content_ai import generate_social_copy
from ...database import get_db
from ...runtime_settings import decrypt_setting_value, encrypt_setting_value
from ...services.social_service import (
    account_identity,
    exchange_code,
    oauth_authorize_url,
    publish_post,
    fetch_tiktok_publish_status,
    refresh_access_token,
    sign_state,
    verify_media_token,
    verify_state,
)


router = APIRouter(prefix="/social", tags=["social publishing"])


class ManualAccount(BaseModel):
    platform: str
    external_account_id: str
    display_name: str
    access_token: str = Field(min_length=8)
    refresh_token: str | None = None
    workspace_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class SocialPostWrite(BaseModel):
    clip_id: str
    social_account_id: str
    title: str | None = None
    description: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    scheduled_for: datetime | None = None
    generate_copy: bool = True


async def _user_id(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


async def _require_workspace_admin(
    db: AsyncSession, workspace_id: str, user_id: str
) -> None:
    result = await db.execute(
        text(
            """
            SELECT CASE WHEN w.owner_id = :user_id THEN 'owner' ELSE wm.role END
            FROM workspaces w
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = w.id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE w.id = :workspace_id
              AND (w.owner_id = :user_id OR wm.user_id = :user_id)
            """
        ),
        {"workspace_id": workspace_id, "user_id": user_id},
    )
    role = result.scalar_one_or_none()
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Workspace admin permission required")


@router.get("/accounts")
async def list_accounts(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT sa.id, sa.workspace_id, sa.platform, sa.external_account_id,
                   sa.display_name, sa.token_expires_at, sa.scopes, sa.status,
                   sa.metadata_json, sa.created_at, sa.updated_at
            FROM social_accounts sa
            LEFT JOIN workspaces w ON w.id = sa.workspace_id
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = sa.workspace_id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE sa.user_id = :user_id OR w.owner_id = :user_id OR wm.user_id = :user_id
            ORDER BY sa.created_at DESC
            """
        ),
        {"user_id": user_id},
    )
    items = []
    for row in result.mappings():
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        items.append(item)
    return {"accounts": items}


@router.post("/accounts/manual")
async def connect_manual(
    body: ManualAccount, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    platform = body.platform.strip().lower()
    if platform not in {"youtube", "tiktok", "instagram", "facebook"}:
        raise HTTPException(status_code=422, detail="Unsupported platform")
    if body.workspace_id:
        await _require_workspace_admin(db, body.workspace_id, user_id)
    account_id = str(uuid4())
    result = await db.execute(
        text(
            """
            INSERT INTO social_accounts
                (id, workspace_id, user_id, platform, external_account_id, display_name,
                 access_token_encrypted, refresh_token_encrypted, metadata_json)
            VALUES (:id, :workspace_id, :user_id, :platform, :external_id, :display_name,
                    :access_token, :refresh_token, :metadata)
            ON CONFLICT (workspace_id, platform, external_account_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                access_token_encrypted = EXCLUDED.access_token_encrypted,
                refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                metadata_json = EXCLUDED.metadata_json,
                status = 'active', updated_at = NOW()
            RETURNING id
            """
        ),
        {"id": account_id, "workspace_id": body.workspace_id, "user_id": user_id,
         "platform": platform, "external_id": body.external_account_id,
         "display_name": body.display_name,
         "access_token": encrypt_setting_value(body.access_token),
         "refresh_token": encrypt_setting_value(body.refresh_token) if body.refresh_token else None,
         "metadata": json.dumps(body.metadata)},
    )
    persisted_account_id = str(result.scalar_one())
    await db.commit()
    return {"id": persisted_account_id, "platform": platform, "display_name": body.display_name}


@router.get("/oauth/{platform}/start")
async def oauth_start(
    platform: str, request: Request, workspace_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    platform = platform.lower()
    if workspace_id:
        await _require_workspace_admin(db, workspace_id, user_id)
    try:
        state = sign_state({"user_id": user_id, "workspace_id": workspace_id, "platform": platform})
        return {"authorize_url": oauth_authorize_url(platform, state)}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/oauth/{platform}/callback")
async def oauth_callback(
    platform: str, code: str, state: str, db: AsyncSession = Depends(get_db)
):
    config = get_config()
    try:
        state_data = verify_state(state)
        if state_data.get("platform") != platform:
            raise ValueError("OAuth provider mismatch")
        token = await exchange_code(platform, code)
        access_token = token["access_token"]
        identity = await account_identity(platform, access_token, token)
        access_token = identity.get("access_token") or access_token
        expires_at = None
        if token.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token["expires_in"]))
        account_id = str(uuid4())
        await db.execute(
            text(
                """
                INSERT INTO social_accounts
                    (id, workspace_id, user_id, platform, external_account_id, display_name,
                     access_token_encrypted, refresh_token_encrypted, token_expires_at,
                     scopes, metadata_json)
                VALUES (:id, :workspace_id, :user_id, :platform, :external_id, :display_name,
                        :access_token, :refresh_token, :expires_at, :scopes, :metadata)
                ON CONFLICT (workspace_id, platform, external_account_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    access_token_encrypted = EXCLUDED.access_token_encrypted,
                    refresh_token_encrypted = COALESCE(EXCLUDED.refresh_token_encrypted, social_accounts.refresh_token_encrypted),
                    token_expires_at = EXCLUDED.token_expires_at, scopes = EXCLUDED.scopes,
                    metadata_json = EXCLUDED.metadata_json, status = 'active', updated_at = NOW()
                """
            ),
            {"id": account_id, "workspace_id": state_data.get("workspace_id"),
             "user_id": state_data["user_id"], "platform": platform,
             "external_id": identity["id"], "display_name": identity["name"],
             "access_token": encrypt_setting_value(access_token),
             "refresh_token": encrypt_setting_value(token["refresh_token"]) if token.get("refresh_token") else None,
             "expires_at": expires_at, "scopes": token.get("scope"),
             "metadata": json.dumps(identity.get("metadata") or {})},
        )
        await db.commit()
        return RedirectResponse(f"{config.app_base_url}/settings/integrations?connected={platform}")
    except Exception as exc:
        await db.rollback()
        return RedirectResponse(f"{config.app_base_url}/settings/integrations?error={type(exc).__name__}")


@router.delete("/accounts/{account_id}")
async def disconnect_account(
    account_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text("DELETE FROM social_accounts WHERE id = :id AND user_id = :user_id RETURNING id"),
        {"id": account_id, "user_id": user_id},
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Account not found")
    await db.commit()
    return {"deleted": True}


@router.post("/posts")
async def create_post(
    body: SocialPostWrite, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT gc.task_id, gc.text, sa.platform, t.workspace_id
            FROM generated_clips gc JOIN tasks t ON t.id = gc.task_id
            JOIN social_accounts sa ON sa.id = :account_id
            LEFT JOIN workspace_members twm
              ON twm.workspace_id = t.workspace_id AND twm.user_id = :user_id
             AND twm.status = 'active'
            LEFT JOIN workspace_members awm
              ON awm.workspace_id = sa.workspace_id AND awm.user_id = :user_id
             AND awm.status = 'active'
            WHERE gc.id = :clip_id
              AND (t.user_id = :user_id OR (twm.user_id = :user_id AND twm.role <> 'viewer'))
              AND (sa.user_id = :user_id OR (awm.user_id = :user_id AND awm.role <> 'viewer'))
              AND (sa.workspace_id IS NULL OR sa.workspace_id = t.workspace_id)
            """
        ),
        {"account_id": body.social_account_id, "clip_id": body.clip_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Clip or social account not found")
    title, description, hashtags = body.title, body.description, body.hashtags
    if body.generate_copy and (not title or not description):
        generated = await generate_social_copy(row["text"] or "", row["platform"])
        title = title or generated.title
        description = description or generated.description
        hashtags = hashtags or generated.hashtags
    post_id = str(uuid4())
    status = "scheduled" if body.scheduled_for else "draft"
    await db.execute(
        text(
            """
            INSERT INTO social_posts
                (id, workspace_id, user_id, task_id, clip_id, social_account_id, platform,
                 title, description, hashtags, scheduled_for, status)
            VALUES (:id, :workspace_id, :user_id, :task_id, :clip_id, :account_id, :platform,
                    :title, :description, :hashtags, :scheduled_for, :status)
            """
        ),
        {"id": post_id, "workspace_id": row["workspace_id"], "user_id": user_id,
         "task_id": row["task_id"],
         "clip_id": body.clip_id, "account_id": body.social_account_id,
         "platform": row["platform"], "title": title, "description": description,
         "hashtags": " ".join(f"#{tag.lstrip('#')}" for tag in hashtags),
         "scheduled_for": body.scheduled_for, "status": status},
    )
    await db.commit()
    return {"id": post_id, "status": status, "platform": row["platform"],
            "title": title, "description": description, "hashtags": hashtags}


async def publish_social_post(db: AsyncSession, post_id: str, user_id: str | None = None) -> dict:
    result = await db.execute(
        text(
            """
            SELECT sp.*, sa.external_account_id, sa.access_token_encrypted,
                   sa.refresh_token_encrypted, sa.token_expires_at, sa.status AS account_status,
                   gc.file_path
            FROM social_posts sp JOIN social_accounts sa ON sa.id = sp.social_account_id
            JOIN generated_clips gc ON gc.id = sp.clip_id
            WHERE sp.id = :id AND (:user_id IS NULL OR sp.user_id = :user_id)
            """
        ),
        {"id": post_id, "user_id": user_id},
    )
    post = result.mappings().first()
    if not post:
        raise ValueError("Social post not found")
    if post["account_status"] != "active":
        raise RuntimeError("Social account is disconnected or expired")
    await db.execute(
        text("UPDATE social_posts SET status = 'publishing', attempt_count = attempt_count + 1, updated_at = NOW() WHERE id = :id"),
        {"id": post_id},
    )
    await db.commit()
    try:
        access_token = decrypt_setting_value(post["access_token_encrypted"])
        expires_at = post["token_expires_at"]
        now = datetime.now(timezone.utc)
        if expires_at and expires_at <= now + timedelta(minutes=2):
            if not post["refresh_token_encrypted"]:
                await db.execute(
                    text("UPDATE social_accounts SET status = 'expired', updated_at = NOW() WHERE id = :id"),
                    {"id": post["social_account_id"]},
                )
                await db.commit()
                raise RuntimeError("Social account authorization expired; reconnect the account")
            refreshed = await refresh_access_token(
                post["platform"],
                decrypt_setting_value(post["refresh_token_encrypted"]),
            )
            access_token = refreshed["access_token"]
            refreshed_expires_at = now + timedelta(seconds=int(refreshed.get("expires_in") or 3600))
            rotated_refresh = refreshed.get("refresh_token")
            await db.execute(
                text(
                    """
                    UPDATE social_accounts SET access_token_encrypted = :access_token,
                        refresh_token_encrypted = COALESCE(:refresh_token, refresh_token_encrypted),
                        token_expires_at = :expires_at, scopes = COALESCE(:scopes, scopes),
                        status = 'active', updated_at = NOW() WHERE id = :id
                    """
                ),
                {
                    "id": post["social_account_id"],
                    "access_token": encrypt_setting_value(access_token),
                    "refresh_token": encrypt_setting_value(rotated_refresh) if rotated_refresh else None,
                    "expires_at": refreshed_expires_at,
                    "scopes": refreshed.get("scope"),
                },
            )
            await db.commit()
        published = await publish_post(
            platform=post["platform"], access_token=access_token,
            account_id=post["external_account_id"], clip_path=Path(post["file_path"]),
            title=post["title"] or "SupoClip", description=post["description"] or "",
            hashtags=post["hashtags"] or "", post_id=post_id,
        )
        is_pending = bool(published.get("pending"))
        await db.execute(
            text(
                """
                UPDATE social_posts SET status = :status,
                    published_at = CASE WHEN :is_pending THEN NULL ELSE NOW() END,
                    external_post_id = :external_id, metadata_json = :metadata,
                    last_error = NULL, updated_at = NOW() WHERE id = :id
                """
            ),
            {"id": post_id, "status": "publishing" if is_pending else "published",
             "is_pending": is_pending, "external_id": published["external_post_id"],
             "metadata": json.dumps(published.get("response") or {})},
        )
        await db.commit()
        return {
            "id": post_id,
            "status": "publishing" if is_pending else "published",
            **published,
        }
    except Exception as exc:
        await db.execute(
            text("UPDATE social_posts SET status = 'failed', last_error = :error, updated_at = NOW() WHERE id = :id"),
            {"id": post_id, "error": str(exc)[:2000]},
        )
        await db.commit()
        raise


async def reconcile_tiktok_posts(db: AsyncSession) -> dict:
    """Resolve uploaded TikTok posts without re-uploading or creating duplicates."""
    result = await db.execute(
        text(
            """
            SELECT sp.id, sp.external_post_id, sa.id AS social_account_id,
                   sa.access_token_encrypted, sa.refresh_token_encrypted,
                   sa.token_expires_at
            FROM social_posts sp
            JOIN social_accounts sa ON sa.id = sp.social_account_id
            WHERE sp.platform = 'tiktok' AND sp.status = 'publishing'
              AND sp.external_post_id IS NOT NULL
            ORDER BY sp.updated_at LIMIT 20
            FOR UPDATE SKIP LOCKED
            """
        )
    )
    published, processing, rejected, errors = [], [], [], []
    for row in result.mappings():
        try:
            access_token = decrypt_setting_value(row["access_token_encrypted"])
            expires_at = row["token_expires_at"]
            now = datetime.now(timezone.utc)
            if expires_at and expires_at <= now + timedelta(minutes=2):
                if not row["refresh_token_encrypted"]:
                    raise RuntimeError("TikTok authorization expired; reconnect the account")
                refreshed = await refresh_access_token(
                    "tiktok", decrypt_setting_value(row["refresh_token_encrypted"])
                )
                access_token = refreshed["access_token"]
                rotated_refresh = refreshed.get("refresh_token")
                await db.execute(
                    text(
                        """
                        UPDATE social_accounts SET access_token_encrypted = :access_token,
                            refresh_token_encrypted = COALESCE(:refresh_token, refresh_token_encrypted),
                            token_expires_at = :expires_at, updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": row["social_account_id"],
                        "access_token": encrypt_setting_value(access_token),
                        "refresh_token": encrypt_setting_value(rotated_refresh) if rotated_refresh else None,
                        "expires_at": now + timedelta(seconds=int(refreshed.get("expires_in") or 3600)),
                    },
                )
            state = await fetch_tiktok_publish_status(
                access_token, row["external_post_id"]
            )
            provider_status = state["status"]
            if provider_status == "PUBLISH_COMPLETE":
                await db.execute(
                    text(
                        """
                        UPDATE social_posts SET status = 'published', published_at = NOW(),
                            metadata_json = :metadata, last_error = NULL, updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"id": row["id"], "metadata": json.dumps(state)},
                )
                published.append(row["id"])
            elif provider_status == "FAILED":
                reason = state.get("fail_reason") or "TikTok rejected the post"
                await db.execute(
                    text(
                        """
                        UPDATE social_posts SET status = 'rejected', metadata_json = :metadata,
                            last_error = :error, updated_at = NOW() WHERE id = :id
                        """
                    ),
                    {"id": row["id"], "metadata": json.dumps(state), "error": reason},
                )
                rejected.append(row["id"])
            else:
                processing.append(row["id"])
            await db.commit()
        except Exception as exc:
            await db.rollback()
            errors.append({"id": row["id"], "error": str(exc)})
    return {
        "published": published,
        "processing": processing,
        "rejected": rejected,
        "errors": errors,
    }


@router.post("/posts/{post_id}/publish")
async def publish_now(post_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    try:
        return await publish_social_post(db, post_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Publishing failed: {exc}") from exc


@router.get("/posts")
async def list_posts(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text("SELECT * FROM social_posts WHERE user_id = :user_id ORDER BY created_at DESC"),
        {"user_id": user_id},
    )
    return {"posts": [dict(row) for row in result.mappings()]}


@router.get("/media/{post_id}")
async def public_post_media(post_id: str, token: str = Query(...), db: AsyncSession = Depends(get_db)):
    if not verify_media_token(post_id, token):
        raise HTTPException(status_code=403, detail="Invalid or expired media token")
    result = await db.execute(
        text(
            """
            SELECT gc.file_path FROM social_posts sp
            JOIN generated_clips gc ON gc.id = sp.clip_id WHERE sp.id = :id
            """
        ),
        {"id": post_id},
    )
    path = result.scalar_one_or_none()
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path, media_type="video/mp4")
