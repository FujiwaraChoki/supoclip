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
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..runtime_settings import decrypt_setting_value, encrypt_setting_value
from . import CATEGORIES, ENV_WEBHOOKS, HERMES_KIND, HERMES_NAME, WEBHOOK_KINDS
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


class ManagedWebhook(InvalidWebhook):
    """The Hermes webhook comes from the server's environment: admins may only switch it on or off."""

    def __init__(self):
        super().__init__("webhook_managed", "This webhook is configured on the server. You can only turn it on or off.")


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


# What the Hermes row stores instead of a URL. Its real URL and secret are read from the
# environment by each sender, so nothing written to the row (an older API's admin edit
# during a release switch, say) can redirect it, and an older sender fails on this.
HERMES_URL_PLACEHOLDER = "server-configured:HERMES_FEEDBACK_WEBHOOK_URL"


def _public(row) -> dict[str, Any]:
    webhook = dict(row)
    if webhook["kind"] == HERMES_KIND:
        # Stored empty so older code never queues for it; it receives every category.
        webhook["categories"] = list(CATEGORIES)
    return webhook


async def list_webhooks(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(text(f"SELECT {PUBLIC_COLUMNS} FROM feedback_webhooks w ORDER BY w.created_at, w.name"))
    return [_public(row) for row in rows.mappings().all()]


async def get_webhook(db: AsyncSession, webhook_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(text(f"SELECT {PUBLIC_COLUMNS} FROM feedback_webhooks w WHERE w.id = :id"), {"id": webhook_id})
    ).mappings().first()
    return _public(row) if row else None


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
    if current["kind"] == HERMES_KIND and (name is not None or (url or "").strip() or categories is not None):
        raise ManagedWebhook()
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
    kind = (
        await db.execute(text("SELECT kind FROM feedback_webhooks WHERE id = :id"), {"id": webhook_id})
    ).scalar()
    if kind == HERMES_KIND:
        raise ManagedWebhook()
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
    if row["kind"] == HERMES_KIND:
        # From this process's environment only; None when it isn't set here (the sender retries).
        config = get_config()
        url, secret = config.hermes_feedback_webhook_url, config.hermes_feedback_webhook_secret
    else:
        url = decrypt_setting_value(row["url_encrypted"])
        secret = decrypt_setting_value(row["secret_encrypted"]) if row["secret_encrypted"] else None
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "enabled": row["enabled"], "url": url, "secret": secret}


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


async def sync_hermes_webhook(db: AsyncSession, config) -> str:
    """Keep the server-configured Hermes webhook in step with the environment. Runs at API start.

    Both ``HERMES_FEEDBACK_WEBHOOK_URL`` and ``HERMES_FEEDBACK_WEBHOOK_SECRET`` set: one
    ``hermes`` row, which ``service.submit_feedback`` queues for every category. The row holds
    neither value (``load_destination`` reads them from the sender's own environment, so the
    cron worker needs both too) and no categories, so an older release running alongside
    never queues for it. The admin's on/off choice is kept. Either one unset: the row and its
    delivery log are removed, so nothing is queued for it. Returns "created", "updated",
    "removed" or "absent".

    The URL is not held to the public-https rule for admin-entered webhooks: it comes from
    the operator and may name the gateway on an internal address. It must be http(s).
    """
    url = (getattr(config, "hermes_feedback_webhook_url", None) or "").strip()
    secret = (getattr(config, "hermes_feedback_webhook_secret", None) or "").strip()
    if not url or not secret:
        removed = await db.execute(text("DELETE FROM feedback_webhooks WHERE kind = :kind"), {"kind": HERMES_KIND})
        await db.commit()
        return "removed" if removed.rowcount else "absent"
    if urlsplit(url).scheme.lower() not in ("http", "https") or not urlsplit(url).hostname:
        raise InvalidWebhook("url_invalid", "HERMES_FEEDBACK_WEBHOOK_URL must be an http(s) URL.")
    params = {"kind": HERMES_KIND, "name": HERMES_NAME, "url": encrypt_setting_value(HERMES_URL_PLACEHOLDER)}
    updated = await db.execute(
        text(
            "UPDATE feedback_webhooks SET name = :name, url_encrypted = :url, url_hint = '', "
            "secret_encrypted = NULL, categories = '{}', env_source = 'HERMES_FEEDBACK_WEBHOOK_URL', "
            "updated_at = NOW() WHERE kind = :kind"
        ),
        params,
    )
    if updated.rowcount:
        await db.commit()
        return "updated"
    await db.execute(
        text(
            """
            INSERT INTO feedback_webhooks
                (id, name, kind, url_encrypted, url_hint, secret_encrypted, categories, enabled, env_source)
            VALUES (:id, :name, :kind, :url, '', NULL, '{}', TRUE, 'HERMES_FEEDBACK_WEBHOOK_URL')
            ON CONFLICT (kind) WHERE kind = 'hermes' DO NOTHING
            """
        ),
        {**params, "id": str(uuid.uuid4())},
    )
    await db.commit()
    return "created"
