"""Feature flags from the ``feature_flags`` table, per platform.

Admins switch them in /admin (the frontend writes the table directly), so a
change reaches every backend process within ``CACHE_TTL_SECONDS``.

A flag is on for a request when its platform column is on, or when the caller
is an admin and ``admins_bypass`` is set. A flag that has no row, or a table
that can't be read, falls back to ``DEFAULT_FLAGS`` (off for everyone but
admins), so a missing migration never opens a feature.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

Platform = Literal["web", "ios"]
PLATFORMS: tuple[Platform, ...] = ("web", "ios")

# Sent by the iOS app (and forwarded by the web proxies). Anything else,
# including API keys and the MCP server, counts as web.
CLIENT_HEADER = "x-supoclip-client"

CACHE_TTL_SECONDS = 15.0

WORKFLOWS = "workflows"
TIKTOK_PUBLISHING = "tiktok_publishing"


@dataclass(frozen=True)
class FeatureFlag:
    key: str
    web_enabled: bool = False
    ios_enabled: bool = False
    admins_bypass: bool = True
    description: str = ""

    def enabled_on(self, platform: Platform) -> bool:
        return self.ios_enabled if platform == "ios" else self.web_enabled


DEFAULT_FLAGS: dict[str, FeatureFlag] = {
    WORKFLOWS: FeatureFlag(WORKFLOWS),
    TIKTOK_PUBLISHING: FeatureFlag(TIKTOK_PUBLISHING, admins_bypass=False),
}

_cache: Optional[tuple[float, dict[str, FeatureFlag]]] = None


def client_platform(request: Optional[Request]) -> Platform:
    """``ios`` when the request says it comes from the iOS app, else ``web``."""
    if request is None:
        return "web"
    value = (request.headers.get(CLIENT_HEADER) or "").strip().lower()
    return "ios" if value == "ios" else "web"


def clear_cache() -> None:
    global _cache
    _cache = None


async def _read_flags(db) -> dict[str, FeatureFlag]:
    # A savepoint keeps a failed read (say, before the migration ran) from
    # aborting the caller's transaction.
    async with db.begin_nested():
        rows = await db.execute(
            text(
                "SELECT key, web_enabled, ios_enabled, admins_bypass, description FROM feature_flags"
            )
        )
        return {
            row.key: FeatureFlag(
                key=row.key,
                web_enabled=bool(row.web_enabled),
                ios_enabled=bool(row.ios_enabled),
                admins_bypass=bool(row.admins_bypass),
                description=row.description or "",
            )
            for row in rows
        }


async def load_flags(db) -> dict[str, FeatureFlag]:
    """Every flag, from the cache when it's fresh. Unknown keys get their default."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    flags = dict(DEFAULT_FLAGS)
    if db is not None:
        try:
            flags.update(await _read_flags(db))
        except Exception as exc:  # noqa: BLE001 - fall back to the defaults, never open a feature
            logger.warning("Could not read feature flags, using defaults: %s", exc)
    _cache = (now, flags)
    return flags


def evaluate(flag: FeatureFlag, platform: Platform, is_admin: bool) -> bool:
    return flag.enabled_on(platform) or (flag.admins_bypass and is_admin)


async def _is_admin(db, user_id: Optional[str]) -> bool:
    if db is None or not user_id:
        return False
    row = (
        await db.execute(text("SELECT is_admin FROM users WHERE id = :user_id"), {"user_id": user_id})
    ).fetchone()
    return bool(row and row.is_admin)


async def is_enabled(db, key: str, *, platform: Platform, user_id: Optional[str] = None) -> bool:
    """Is ``key`` on for this user on this platform? Looks up the user only for the admin bypass."""
    flags = await load_flags(db)
    flag = flags.get(key) or DEFAULT_FLAGS.get(key) or FeatureFlag(key)
    if flag.enabled_on(platform):
        return True
    return flag.admins_bypass and await _is_admin(db, user_id)


async def evaluated_flags(db, *, platform: Platform, user_id: Optional[str]) -> dict[str, bool]:
    """Every flag as a boolean for this user and platform, for ``GET /feature-flags``."""
    flags = await load_flags(db)
    admin: Optional[bool] = None
    result: dict[str, bool] = {}
    for key, flag in sorted(flags.items()):
        if flag.enabled_on(platform):
            result[key] = True
            continue
        if flag.admins_bypass and admin is None:
            admin = await _is_admin(db, user_id)
        result[key] = evaluate(flag, platform, bool(admin))
    return result
