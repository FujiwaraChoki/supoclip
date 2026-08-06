"""Workspace, brand, collection, import, and webhook workflow APIs."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import re
import secrets
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...runtime_settings import encrypt_setting_value
from ...services.export_service import render_export
from ...services.webhook_service import emit_webhook_event
from ...utils.async_helpers import run_in_thread
from ...workers.job_queue import JobQueue


router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _user_id(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return (normalized or "workspace")[:140]


async def _workspace_role(
    db: AsyncSession, workspace_id: str, user_id: str
) -> str | None:
    result = await db.execute(
        text(
            """
            SELECT CASE WHEN w.owner_id = :user_id THEN 'owner' ELSE wm.role END AS role
            FROM workspaces w
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = w.id
             AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE w.id = :workspace_id
              AND (w.owner_id = :user_id OR wm.user_id = :user_id)
            LIMIT 1
            """
        ),
        {"workspace_id": workspace_id, "user_id": user_id},
    )
    row = result.mappings().first()
    return str(row["role"]) if row and row.get("role") else None


async def _require_workspace(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    allowed_roles: set[str] | None = None,
) -> str:
    role = await _workspace_role(db, workspace_id, user_id)
    if not role:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if allowed_roles and role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Workspace permission required")
    return role


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class WorkspaceInvite(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: str = "member"


class BrandKitWrite(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    workspace_id: str | None = None
    is_default: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)


class CollectionWrite(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    workspace_id: str | None = None


class CollectionClipWrite(BaseModel):
    clip_id: str


class SourceSubscriptionWrite(BaseModel):
    provider: str
    external_source_id: str
    source_url: str
    display_name: str
    workspace_id: str | None = None
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)


class WebhookWrite(BaseModel):
    url: str
    events: list[str] = Field(default_factory=lambda: ["task.completed"])
    workspace_id: str | None = None


class ExportWrite(BaseModel):
    task_id: str | None = None
    collection_id: str | None = None
    export_type: str = "zip"
    workspace_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


async def _require_collection(
    db: AsyncSession, collection_id: str, user_id: str, *, require_write: bool = False
) -> dict:
    result = await db.execute(
        text(
            """
            SELECT c.*,
                   CASE WHEN c.user_id = :user_id THEN 'owner' ELSE wm.role END AS access_role
            FROM collections c
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = c.workspace_id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE c.id = :id AND (c.user_id = :user_id OR wm.user_id = :user_id)
            """
        ),
        {"id": collection_id, "user_id": user_id},
    )
    collection = result.mappings().first()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if require_write and collection["access_role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer access is read-only")
    return dict(collection)


@router.get("/workspaces")
async def list_workspaces(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT DISTINCT w.id, w.name, w.slug, w.logo_url, w.owner_id,
                   CASE WHEN w.owner_id = :user_id THEN 'owner' ELSE wm.role END AS role,
                   w.created_at, w.updated_at
            FROM workspaces w
            LEFT JOIN workspace_members wm ON wm.workspace_id = w.id
            WHERE w.owner_id = :user_id
               OR (wm.user_id = :user_id AND wm.status = 'active')
            ORDER BY w.created_at
            """
        ),
        {"user_id": user_id},
    )
    return {"workspaces": [dict(row) for row in result.mappings()]}


@router.post("/workspaces")
async def create_workspace(
    body: WorkspaceCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    workspace_id = str(uuid4())
    slug = f"{_slug(body.name)}-{workspace_id[:8]}"
    email_result = await db.execute(
        text("SELECT email FROM users WHERE id = :user_id"), {"user_id": user_id}
    )
    email = email_result.scalar_one()
    await db.execute(
        text(
            """
            INSERT INTO workspaces (id, owner_id, name, slug)
            VALUES (:id, :owner_id, :name, :slug)
            """
        ),
        {"id": workspace_id, "owner_id": user_id, "name": body.name, "slug": slug},
    )
    await db.execute(
        text(
            """
            INSERT INTO workspace_members
                (id, workspace_id, user_id, email, role, status, joined_at)
            VALUES (:id, :workspace_id, :user_id, :email, 'owner', 'active', NOW())
            """
        ),
        {
            "id": str(uuid4()),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "email": email,
        },
    )
    await db.commit()
    return {"id": workspace_id, "name": body.name, "slug": slug, "role": "owner"}


@router.get("/workspaces/{workspace_id}/members")
async def list_members(
    workspace_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    await _require_workspace(db, workspace_id, user_id)
    result = await db.execute(
        text(
            """
            SELECT id, user_id, email, role, status, joined_at, created_at
            FROM workspace_members WHERE workspace_id = :workspace_id
            ORDER BY created_at
            """
        ),
        {"workspace_id": workspace_id},
    )
    return {"members": [dict(row) for row in result.mappings()]}


@router.post("/workspaces/{workspace_id}/invites")
async def invite_member(
    workspace_id: str,
    body: WorkspaceInvite,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    await _require_workspace(db, workspace_id, user_id, {"owner", "admin"})
    role = body.role if body.role in {"admin", "editor", "member", "viewer"} else "member"
    token = secrets.token_urlsafe(32)
    member_id = str(uuid4())
    await db.execute(
        text(
            """
            INSERT INTO workspace_members
                (id, workspace_id, email, role, status, invite_token, invited_by)
            VALUES (:id, :workspace_id, :email, :role, 'invited', :token, :invited_by)
            ON CONFLICT (workspace_id, email) DO UPDATE SET
                role = EXCLUDED.role,
                status = 'invited',
                invite_token = EXCLUDED.invite_token,
                invited_by = EXCLUDED.invited_by,
                updated_at = NOW()
            RETURNING id
            """
        ),
        {
            "id": member_id,
            "workspace_id": workspace_id,
            "email": body.email.strip().lower(),
            "role": role,
            "token": token,
            "invited_by": user_id,
        },
    )
    await db.commit()
    return {"invite_token": token, "role": role, "email": body.email.strip().lower()}


@router.post("/invites/{invite_token}/accept")
async def accept_invite(
    invite_token: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    user_result = await db.execute(
        text("SELECT email FROM users WHERE id = :user_id"), {"user_id": user_id}
    )
    email = str(user_result.scalar_one()).lower()
    result = await db.execute(
        text(
            """
            UPDATE workspace_members
            SET user_id = :user_id, status = 'active', joined_at = NOW(),
                invite_token = NULL, updated_at = NOW()
            WHERE invite_token = :invite_token AND lower(email) = :email
            RETURNING workspace_id, role
            """
        ),
        {"user_id": user_id, "invite_token": invite_token, "email": email},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Invitation not found")
    await db.commit()
    return dict(row)


@router.get("/brand-kits")
async def list_brand_kits(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT DISTINCT bk.* FROM brand_kits bk
            LEFT JOIN workspace_members wm ON wm.workspace_id = bk.workspace_id
            LEFT JOIN workspaces w ON w.id = bk.workspace_id
            WHERE bk.user_id = :user_id OR w.owner_id = :user_id
               OR (wm.user_id = :user_id AND wm.status = 'active')
            ORDER BY bk.is_default DESC, bk.created_at
            """
        ),
        {"user_id": user_id},
    )
    kits = []
    for row in result.mappings():
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json") or "{}")
        kits.append(item)
    return {"brand_kits": kits}


@router.post("/brand-kits")
async def create_brand_kit(
    body: BrandKitWrite, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    if body.workspace_id:
        await _require_workspace(db, body.workspace_id, user_id, {"owner", "admin", "editor"})
    kit_id = str(uuid4())
    if body.is_default:
        await db.execute(
            text("UPDATE brand_kits SET is_default = false WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    await db.execute(
        text(
            """
            INSERT INTO brand_kits
                (id, workspace_id, user_id, name, is_default, settings_json)
            VALUES (:id, :workspace_id, :user_id, :name, :is_default, :settings_json)
            """
        ),
        {
            "id": kit_id,
            "workspace_id": body.workspace_id,
            "user_id": user_id,
            "name": body.name,
            "is_default": body.is_default,
            "settings_json": json.dumps(body.settings),
        },
    )
    await db.commit()
    return {"id": kit_id, **body.model_dump()}


@router.patch("/brand-kits/{kit_id}")
async def update_brand_kit(
    kit_id: str,
    body: BrandKitWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    owner = await db.execute(
        text("SELECT user_id, workspace_id FROM brand_kits WHERE id = :id"), {"id": kit_id}
    )
    row = owner.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    if row["user_id"] != user_id:
        await _require_workspace(db, row["workspace_id"], user_id, {"owner", "admin", "editor"})
    if body.is_default:
        await db.execute(
            text("UPDATE brand_kits SET is_default = false WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    await db.execute(
        text(
            """
            UPDATE brand_kits SET name = :name, is_default = :is_default,
                settings_json = :settings_json, updated_at = NOW()
            WHERE id = :id
            """
        ),
        {
            "id": kit_id,
            "name": body.name,
            "is_default": body.is_default,
            "settings_json": json.dumps(body.settings),
        },
    )
    await db.commit()
    return {"id": kit_id, **body.model_dump()}


@router.delete("/brand-kits/{kit_id}")
async def delete_brand_kit(
    kit_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text("DELETE FROM brand_kits WHERE id = :id AND user_id = :user_id RETURNING id"),
        {"id": kit_id, "user_id": user_id},
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Brand kit not found")
    await db.commit()
    return {"deleted": True}


@router.get("/collections")
async def list_collections(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT c.*, COUNT(DISTINCT cc.clip_id) AS clip_count
            FROM collections c LEFT JOIN collection_clips cc ON cc.collection_id = c.id
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = c.workspace_id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE c.user_id = :user_id OR wm.user_id = :user_id
            GROUP BY c.id ORDER BY c.updated_at DESC
            """
        ),
        {"user_id": user_id},
    )
    return {"collections": [dict(row) for row in result.mappings()]}


@router.post("/collections")
async def create_collection(
    body: CollectionWrite, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    if body.workspace_id:
        await _require_workspace(
            db, body.workspace_id, user_id, {"owner", "admin", "editor", "member"}
        )
    collection_id = str(uuid4())
    await db.execute(
        text(
            """
            INSERT INTO collections (id, workspace_id, user_id, name, description)
            VALUES (:id, :workspace_id, :user_id, :name, :description)
            """
        ),
        {"id": collection_id, "user_id": user_id, **body.model_dump()},
    )
    await db.commit()
    return {"id": collection_id, **body.model_dump()}


@router.post("/collections/{collection_id}/clips")
async def add_collection_clip(
    collection_id: str,
    body: CollectionClipWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    collection = await _require_collection(
        db, collection_id, user_id, require_write=True
    )
    allowed = await db.execute(
        text(
            """
            SELECT t.workspace_id, t.user_id FROM generated_clips gc
            JOIN tasks t ON t.id = gc.task_id
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = t.workspace_id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE gc.id = :clip_id AND (
                t.user_id = :user_id OR (wm.user_id = :user_id AND wm.role <> 'viewer')
            )
            """
        ),
        {"clip_id": body.clip_id, "user_id": user_id},
    )
    clip_access = allowed.mappings().first()
    if not clip_access:
        raise HTTPException(status_code=404, detail="Collection or clip not found")
    if collection.get("workspace_id") != clip_access.get("workspace_id"):
        raise HTTPException(status_code=422, detail="Collection and clip must use the same workspace")
    await db.execute(
        text(
            """
            INSERT INTO collection_clips (collection_id, clip_id, added_by)
            VALUES (:collection_id, :clip_id, :user_id)
            ON CONFLICT DO NOTHING
            """
        ),
        {"collection_id": collection_id, "clip_id": body.clip_id, "user_id": user_id},
    )
    await db.commit()
    return {"added": True}


@router.get("/collections/{collection_id}")
async def get_collection(
    collection_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    collection = await _require_collection(db, collection_id, user_id)
    clips_result = await db.execute(
        text(
            """
            SELECT gc.*, cc.created_at AS added_at FROM generated_clips gc
            JOIN collection_clips cc ON cc.clip_id = gc.id
            WHERE cc.collection_id = :id ORDER BY cc.created_at
            """
        ),
        {"id": collection_id},
    )
    collection.pop("access_role", None)
    return {"collection": collection, "clips": [dict(row) for row in clips_result.mappings()]}


@router.delete("/collections/{collection_id}")
async def delete_collection(
    collection_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    await _require_collection(db, collection_id, user_id, require_write=True)
    result = await db.execute(
        text("DELETE FROM collections WHERE id = :id RETURNING id"),
        {"id": collection_id},
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Collection not found")
    await db.commit()
    return {"deleted": True}


@router.delete("/collections/{collection_id}/clips/{clip_id}")
async def remove_collection_clip(
    collection_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    await _require_collection(db, collection_id, user_id, require_write=True)
    result = await db.execute(
        text(
            """
            DELETE FROM collection_clips cc
            WHERE cc.collection_id = :collection_id AND cc.clip_id = :clip_id
            RETURNING cc.clip_id
            """
        ),
        {"collection_id": collection_id, "clip_id": clip_id},
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Collection clip not found")
    await db.commit()
    return {"removed": True}


@router.get("/source-subscriptions")
async def list_source_subscriptions(
    request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text("SELECT * FROM source_subscriptions WHERE user_id = :user_id ORDER BY created_at"),
        {"user_id": user_id},
    )
    subscriptions = []
    for row in result.mappings():
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json") or "{}")
        subscriptions.append(item)
    return {"source_subscriptions": subscriptions}


@router.post("/source-subscriptions")
async def create_source_subscription(
    body: SourceSubscriptionWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    if body.workspace_id:
        await _require_workspace(
            db, body.workspace_id, user_id, {"owner", "admin", "editor", "member"}
        )
    provider = body.provider.strip().lower()
    if provider != "youtube":
        raise HTTPException(status_code=422, detail="Only YouTube channel auto-import is supported")
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", body.external_source_id.strip()):
        raise HTTPException(status_code=422, detail="Enter a valid YouTube channel ID")
    subscription_id = str(uuid4())
    await db.execute(
        text(
            """
            INSERT INTO source_subscriptions
                (id, workspace_id, user_id, provider, external_source_id,
                 source_url, display_name, enabled, settings_json)
            VALUES (:id, :workspace_id, :user_id, :provider, :external_source_id,
                    :source_url, :display_name, :enabled, :settings_json)
            ON CONFLICT (user_id, provider, external_source_id) DO UPDATE SET
                source_url = EXCLUDED.source_url,
                display_name = EXCLUDED.display_name,
                enabled = EXCLUDED.enabled,
                settings_json = EXCLUDED.settings_json,
                updated_at = NOW()
            """
        ),
        {
            "id": subscription_id,
            "user_id": user_id,
            **body.model_dump(exclude={"settings"}),
            "provider": provider,
            "settings_json": json.dumps(body.settings),
        },
    )
    await db.commit()
    return {"id": subscription_id, **body.model_dump(), "provider": provider}


@router.post("/source-subscriptions/poll")
async def poll_source_subscriptions_now(
    request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    job_id = await JobQueue.enqueue_job("poll_sources_task", user_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/webhooks")
async def list_webhooks(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT id, workspace_id, url, events, enabled, created_at, updated_at
            FROM webhook_endpoints WHERE user_id = :user_id ORDER BY created_at
            """
        ),
        {"user_id": user_id},
    )
    items = []
    for row in result.mappings():
        item = dict(row)
        item["events"] = json.loads(item["events"] or "[]")
        items.append(item)
    return {"webhooks": items}


@router.post("/webhooks")
async def create_webhook(
    body: WebhookWrite, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    if not body.url.startswith("https://"):
        raise HTTPException(status_code=422, detail="Webhook URL must use HTTPS")
    if body.workspace_id:
        await _require_workspace(db, body.workspace_id, user_id, {"owner", "admin"})
    secret = secrets.token_urlsafe(32)
    endpoint_id = str(uuid4())
    await db.execute(
        text(
            """
            INSERT INTO webhook_endpoints
                (id, workspace_id, user_id, url, secret_encrypted, events)
            VALUES (:id, :workspace_id, :user_id, :url, :secret, :events)
            """
        ),
        {
            "id": endpoint_id,
            "workspace_id": body.workspace_id,
            "user_id": user_id,
            "url": body.url,
            "secret": encrypt_setting_value(secret),
            "events": json.dumps(sorted(set(body.events))),
        },
    )
    await db.commit()
    return {"id": endpoint_id, "secret": secret, **body.model_dump()}


@router.post("/webhooks/{endpoint_id}/test")
async def test_webhook(
    endpoint_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    allowed = await db.execute(
        text("SELECT 1 FROM webhook_endpoints WHERE id = :id AND user_id = :user_id"),
        {"id": endpoint_id, "user_id": user_id},
    )
    if not allowed.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Webhook not found")
    deliveries = await emit_webhook_event(
        db, user_id=user_id, event_type="webhook.test",
        payload={"message": "SupoClip webhook connection test", "endpoint_id": endpoint_id},
        endpoint_id=endpoint_id,
    )
    return {"deliveries": [item for item in deliveries if item.get("id")]}


@router.post("/assets")
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    asset_type: str = Form("logo"),
    name: str | None = Form(None),
    brand_kit_id: str | None = Form(None),
    workspace_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    if asset_type not in {"logo", "font", "intro", "outro", "overlay", "music", "image", "video"}:
        raise HTTPException(status_code=422, detail="Unsupported asset type")
    if workspace_id:
        await _require_workspace(db, workspace_id, user_id, {"owner", "admin", "editor"})
    if brand_kit_id:
        allowed = await db.execute(
            text(
                """
                SELECT bk.user_id, bk.workspace_id FROM brand_kits bk
                WHERE bk.id = :id
                """
            ),
            {"id": brand_kit_id, "user_id": user_id},
        )
        kit = allowed.mappings().first()
        if not kit:
            raise HTTPException(status_code=404, detail="Brand kit not found")
        if kit["user_id"] != user_id:
            if not kit["workspace_id"]:
                raise HTTPException(status_code=404, detail="Brand kit not found")
            await _require_workspace(
                db, kit["workspace_id"], user_id, {"owner", "admin", "editor"}
            )
    payload = await file.read()
    if len(payload) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Asset exceeds the 100 MB limit")
    asset_id = str(uuid4())
    suffix = Path(file.filename or "asset").suffix[:12]
    path = Path(get_config().temp_dir) / "assets" / user_id / f"{asset_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    await db.execute(
        text(
            """
            INSERT INTO media_assets
                (id, workspace_id, user_id, brand_kit_id, asset_type, name,
                 file_path, mime_type, size_bytes)
            VALUES (:id, :workspace_id, :user_id, :brand_kit_id, :asset_type, :name,
                    :file_path, :mime_type, :size_bytes)
            """
        ),
        {"id": asset_id, "workspace_id": workspace_id, "user_id": user_id,
         "brand_kit_id": brand_kit_id, "asset_type": asset_type,
         "name": name or file.filename or "Asset", "file_path": str(path),
         "mime_type": file.content_type or mimetypes.guess_type(path.name)[0],
         "size_bytes": len(payload)},
    )
    await db.commit()
    return {"id": asset_id, "name": name or file.filename, "asset_type": asset_type,
            "size_bytes": len(payload), "file_url": f"/workflows/assets/{asset_id}/file"}


@router.get("/assets")
async def list_assets(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT ma.id, ma.workspace_id, ma.brand_kit_id, ma.asset_type, ma.name,
                   ma.mime_type, ma.size_bytes, ma.metadata_json, ma.created_at, ma.updated_at
            FROM media_assets ma
            LEFT JOIN workspaces w ON w.id = ma.workspace_id
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = ma.workspace_id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE ma.user_id = :user_id OR w.owner_id = :user_id OR wm.user_id = :user_id
            ORDER BY ma.created_at DESC
            """
        ),
        {"user_id": user_id},
    )
    return {"assets": [{**dict(row), "file_url": f"/workflows/assets/{row.id}/file"}
                       for row in result.mappings()]}


@router.get("/assets/{asset_id}/file")
async def asset_file(asset_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text(
            """
            SELECT ma.file_path, ma.mime_type, ma.name FROM media_assets ma
            LEFT JOIN workspaces w ON w.id = ma.workspace_id
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = ma.workspace_id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE ma.id = :id AND (
                ma.user_id = :user_id OR w.owner_id = :user_id OR wm.user_id = :user_id
            )
            """
        ),
        {"id": asset_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if not row or not Path(row["file_path"]).is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(row["file_path"], media_type=row["mime_type"], filename=row["name"])


async def _export_clips(
    db: AsyncSession, body: ExportWrite, user_id: str
) -> tuple[list[dict], str | None]:
    workspace_id: str | None = None
    if body.task_id:
        result = await db.execute(
            text(
                """
                SELECT gc.*, t.workspace_id AS export_workspace_id
                FROM generated_clips gc JOIN tasks t ON t.id = gc.task_id
                LEFT JOIN workspace_members wm
                  ON wm.workspace_id = t.workspace_id AND wm.user_id = :user_id
                 AND wm.status = 'active'
                WHERE gc.task_id = :task_id
                  AND (t.user_id = :user_id OR wm.user_id = :user_id)
                ORDER BY gc.clip_order
                """
            ),
            {"task_id": body.task_id, "user_id": user_id},
        )
    elif body.collection_id:
        result = await db.execute(
            text(
                """
                SELECT gc.*, c.workspace_id AS export_workspace_id FROM generated_clips gc
                JOIN collection_clips cc ON cc.clip_id = gc.id
                JOIN collections c ON c.id = cc.collection_id
                LEFT JOIN workspace_members wm
                  ON wm.workspace_id = c.workspace_id AND wm.user_id = :user_id
                 AND wm.status = 'active'
                WHERE c.id = :collection_id
                  AND (c.user_id = :user_id OR wm.user_id = :user_id)
                ORDER BY cc.created_at
                """
            ),
            {"collection_id": body.collection_id, "user_id": user_id},
        )
    else:
        raise HTTPException(status_code=422, detail="task_id or collection_id is required")
    clips = [dict(row) for row in result.mappings()]
    if not clips:
        raise HTTPException(status_code=404, detail="No clips found for export")
    workspace_id = clips[0].pop("export_workspace_id", None)
    for clip in clips[1:]:
        clip.pop("export_workspace_id", None)
    return clips, workspace_id


@router.post("/exports")
async def create_export(
    body: ExportWrite, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    export_type = body.export_type.strip().lower()
    if export_type not in {"zip", "csv", "srt", "fcpxml", "edl"}:
        raise HTTPException(status_code=422, detail="Unsupported export type")
    clips, workspace_id = await _export_clips(db, body, user_id)
    job_id = str(uuid4())
    await db.execute(
        text(
            """
            INSERT INTO export_jobs
                (id, workspace_id, user_id, task_id, collection_id, export_type, status, settings_json)
            VALUES (:id, :workspace_id, :user_id, :task_id, :collection_id, :type, 'processing', :settings)
            """
        ),
        {"id": job_id, "workspace_id": workspace_id, "user_id": user_id,
         "task_id": body.task_id, "collection_id": body.collection_id,
         "type": export_type, "settings": json.dumps(body.settings)},
    )
    await db.commit()
    try:
        path = await run_in_thread(
            render_export, export_type, clips, Path(get_config().temp_dir) / "exports", job_id
        )
        await db.execute(
            text("UPDATE export_jobs SET status = 'completed', file_path = :path, updated_at = NOW() WHERE id = :id"),
            {"id": job_id, "path": str(path)},
        )
        await db.commit()
        return {"id": job_id, "status": "completed", "export_type": export_type,
                "file_url": f"/workflows/exports/{job_id}/file"}
    except Exception as exc:
        await db.execute(
            text("UPDATE export_jobs SET status = 'failed', error = :error, updated_at = NOW() WHERE id = :id"),
            {"id": job_id, "error": str(exc)[:2000]},
        )
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc


@router.get("/exports")
async def list_exports(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text("SELECT * FROM export_jobs WHERE user_id = :user_id ORDER BY created_at DESC"),
        {"user_id": user_id},
    )
    return {"exports": [{**dict(row), "file_url": f"/workflows/exports/{row.id}/file"
                         if row.file_path else None} for row in result.mappings()]}


@router.get("/exports/{job_id}/file")
async def export_file(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _user_id(request, db)
    result = await db.execute(
        text("SELECT file_path FROM export_jobs WHERE id = :id AND user_id = :user_id AND status = 'completed'"),
        {"id": job_id, "user_id": user_id},
    )
    file_path = result.scalar_one_or_none()
    if not file_path or not Path(file_path).is_file():
        raise HTTPException(status_code=404, detail="Export not found")
    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=mime, filename=Path(file_path).name)
