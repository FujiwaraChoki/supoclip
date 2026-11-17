"""Admin-managed webhook destinations for new submissions.

URLs and signing secrets are stored encrypted (``runtime_settings``'s AES-GCM
key) and never leave this module in clear text except to the sender: the admin
API returns ``url_hint`` only, and a generic JSON webhook's secret exactly once,
when it is created or rotated.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..runtime_settings import decrypt_setting_value, encrypt_setting_value
from . import CATEGORIES, ENV_WEBHOOKS, WEBHOOK_KINDS
from .url_safety import UnsafeWebhookUrl, resolve_public_address, url_hint, validate_webhook_url

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 80
PUBLIC_COLUMNS = """
    w.id, w.name, w.kind, w.url_hint, w.categories, w.enabled, w.env_source,
    (w.secret_encrypted IS NOT NULL) AS has_secret, w.created_at, w.updated_at,
    (SELECT COUNT(*) FROM feedback_webhook_deliveries d WHERE d.webhook_id = w.id AND d.status = 'pending') AS pending,
    (SELECT COUNT(*) FROM feedback_webhook_deliveries d WHERE d.webhook_id = w.id AND d.status = 'failed') AS failed,
    (SELECT MAX(d.delivered_at) FROM feedback_webhook_deliveries d WHERE d.webhook_id = w.id) AS last_delivered_at
"""


class InvalidWebhook(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def _categories(values: Iterable[str]) -> list[str]:
    chosen = [value for value in CATEGORIES if value in set(values)]
    unknown = set(values) - set(CATEGORIES)
    if unknown:
        raise InvalidWebhook("invalid_category", f"Unknown categories: {', '.join(sorted(unknown))}")
    if not chosen:
        raise InvalidWebhook("categories_required", "Choose at least one category.")
    return chosen


def _name(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > MAX_NAME_LENGTH:
        raise InvalidWebhook("invalid_name", f"Names are 1 to {MAX_NAME_LENGTH} characters.")
    return value


async def checked_url(url: str, kind: str) -> str:
    """Validated URL, its host resolved to public addresses only."""
    target = validate_webhook_url(url, kind)
    await resolve_public_address(target)
    return target.url


async def list_webhooks(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(text(f"SELECT {PUBLIC_COLUMNS} FROM feedback_webhooks w ORDER BY w.created_at, w.name"))
    return [dict(row) for row in rows.mappings().all()]


async def get_webhook(db: AsyncSession, webhook_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(text(f"SELECT {PUBLIC_COLUMNS} FROM feedback_webhooks w WHERE w.id = :id"), {"id": webhook_id})
    ).mappings().first()
    return dict(row) if row else None


async def create_webhook(
    db: AsyncSession, *, name: str, kind: str, url: str, categories: Iterable[str], enabled: bool, admin_id: str | None,
    env_source: str | None = None, check_dns: bool = True,
) -> tuple[dict[str, Any], str | None]:
    """Returns the webhook and, for ``json`` webhooks, the new signing secret (shown once)."""
    if kind not in WEBHOOK_KINDS:
        raise InvalidWebhook("invalid_kind", "Unknown webhook type.")
    url = await checked_url(url, kind) if check_dns else validate_webhook_url(url, kind).url
    secret = new_secret() if kind == "json" else None
    webhook_id = str(uuid.uuid4())
    await db.execute(
        text(
            """
            INSERT INTO feedback_webhooks
                (id, name, kind, url_encrypted, url_hint, secret_encrypted, categories, enabled, env_source,
                 created_by, updated_by)
            VALUES (:id, :name, :kind, :url, :hint, :secret, :categories, :enabled, :env_source, :admin, :admin)
            """
        ),
        {
            "id": webhook_id,
            "name": _name(name),
            "kind": kind,
            "url": encrypt_setting_value(url),
            "hint": url_hint(url),
            "secret": encrypt_setting_value(secret) if secret else None,
            "categories": _categories(categories),
            "enabled": enabled,
            "env_source": env_source,
            "admin": admin_id,
        },
    )
    await db.commit()
    return (await get_webhook(db, webhook_id)), secret


async def update_webhook(
    db: AsyncSession, webhook_id: str, *, admin_id: str, name: str | None = None, url: str | None = None,
    categories: Iterable[str] | None = None, enabled: bool | None = None,
) -> dict[str, Any] | None:
    current = (
        await db.execute(text("SELECT kind, enabled FROM feedback_webhooks WHERE id = :id"), {"id": webhook_id})
    ).mappings().first()
    if current is None:
        return None
    sets, params = ["updated_by = :admin", "updated_at = NOW()"], {"id": webhook_id, "admin": admin_id}
    if name is not None:
        sets.append("name = :name")
        params["name"] = _name(name)
    if url is not None and url.strip():
        checked = await checked_url(url, current["kind"])
        sets += ["url_encrypted = :url", "url_hint = :hint"]
        params.update(url=encrypt_setting_value(checked), hint=url_hint(checked))
    if categories is not None:
        sets.append("categories = :categories")
        params["categories"] = _categories(categories)
    if enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = enabled
    await db.execute(text(f"UPDATE feedback_webhooks SET {', '.join(sets)} WHERE id = :id"), params)
    if enabled is False and current["enabled"]:
        # Disabling stops what is still queued, so re-enabling never sends a backlog.
        await db.execute(
            text(
                "UPDATE feedback_webhook_deliveries SET status = 'cancelled', lease_token = NULL, lease_expires_at = NULL, updated_at = NOW() "
                "WHERE webhook_id = :id AND status = 'pending'"
            ),
            {"id": webhook_id},
        )
    await db.commit()
    return await get_webhook(db, webhook_id)


async def rotate_secret(db: AsyncSession, webhook_id: str, admin_id: str) -> str | None:
    secret = new_secret()
    result = await db.execute(
        text(
            "UPDATE feedback_webhooks SET secret_encrypted = :secret, updated_by = :admin, updated_at = NOW() "
            "WHERE id = :id AND kind = 'json'"
        ),
        {"id": webhook_id, "secret": encrypt_setting_value(secret), "admin": admin_id},
    )
    await db.commit()
    return secret if result.rowcount else None


async def delete_webhook(db: AsyncSession, webhook_id: str) -> bool:
    result = await db.execute(text("DELETE FROM feedback_webhooks WHERE id = :id"), {"id": webhook_id})
    await db.commit()
    return bool(result.rowcount)


async def load_destination(db: AsyncSession, webhook_id: str) -> dict[str, Any] | None:
    """The webhook with its URL and secret decrypted, for the sender only."""
    row = (
        await db.execute(
            text("SELECT id, name, kind, url_encrypted, secret_encrypted, enabled FROM feedback_webhooks WHERE id = :id"),
            {"id": webhook_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "enabled": row["enabled"],
        "url": decrypt_setting_value(row["url_encrypted"]),
        "secret": decrypt_setting_value(row["secret_encrypted"]) if row["secret_encrypted"] else None,
    }


async def import_env_webhooks(db: AsyncSession, config) -> list[str]:
    """Copy the pre-inbox Discord webhooks from the environment into rows, once each.

    Runs at API start. Each variable is imported at most once (``feedback_webhook_env_imports``):
    after that, admins own the row, and editing or deleting it is final. An empty or
    invalid variable is skipped and tried again on the next start.
    """
    imported = []
    for env_key, name, categories in ENV_WEBHOOKS:
        url = getattr(config, env_key.lower(), None)
        if not url:
            continue
        claimed = await db.execute(
            text(
                "INSERT INTO feedback_webhook_env_imports (env_key) VALUES (:key) ON CONFLICT (env_key) DO NOTHING"
            ),
            {"key": env_key},
        )
        if not claimed.rowcount:
            await db.rollback()
            continue
        try:
            # No DNS check here: these already worked, and a lookup hiccup at start must not drop them.
            webhook, _ = await create_webhook(
                db, name=name, kind="discord", url=url, categories=categories, enabled=True, admin_id=None,
                env_source=env_key, check_dns=False,
            )
        except (InvalidWebhook, UnsafeWebhookUrl) as exc:
            await db.rollback()
            logger.warning("Not importing %s as a feedback webhook: %s", env_key, exc)
            continue
        await db.execute(
            text("UPDATE feedback_webhook_env_imports SET webhook_id = :webhook WHERE env_key = :key"),
            {"key": env_key, "webhook": webhook["id"]},
        )
        await db.commit()
        imported.append(env_key)
        logger.info("Imported %s as feedback webhook %s", env_key, webhook["id"])
    return imported
