"""
API key service - mints, lists and revokes personal and team-scoped API keys.

Keys look like ``sk_<random>``. Only the SHA-256 hash is persisted, so the
plaintext key returned by :func:`create_api_key` is the only time it can be
read; callers must surface it to the user immediately.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..auth_headers import API_KEY_PREFIX, hash_api_key
from ..repositories.api_key_repository import ApiKeyRepository

# Number of random bytes encoded into each key (URL-safe base64 -> ~43 chars).
_KEY_ENTROPY_BYTES = 32
# How many leading characters of the key are stored for display purposes.
_PREFIX_LENGTH = 11
MAX_KEYS_PER_USER = 25


class ApiKeyLimitExceeded(Exception):
    """Raised when a workspace already has the maximum number of active keys."""


def _generate_key() -> str:
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(_KEY_ENTROPY_BYTES)}"


class ApiKeyService:
    """Business logic for API-key lifecycle operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ApiKeyRepository

    async def create_api_key(
        self, user_id: str, name: str, *, team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new API key for the personal or team workspace."""
        existing = await self.repo.list_api_keys(self.db, user_id, team_id=team_id)
        active = [key for key in existing if not key.get("revoked")]
        if len(active) >= MAX_KEYS_PER_USER:
            raise ApiKeyLimitExceeded(
                f"You can have at most {MAX_KEYS_PER_USER} active API keys. "
                "Revoke an existing key before creating a new one."
            )

        cleaned_name = (name or "API Key").strip()[:120] or "API Key"
        raw_key = _generate_key()
        record = await self.repo.create_api_key(
            self.db,
            user_id=user_id,
            name=cleaned_name,
            key_hash=hash_api_key(raw_key),
            key_prefix=raw_key[:_PREFIX_LENGTH],
            team_id=team_id,
        )
        # The plaintext key is only ever returned here.
        return {**record, "key": raw_key}

    async def list_api_keys(
        self, user_id: str, *, team_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List workspace API keys (metadata only, never the secret)."""
        return await self.repo.list_api_keys(self.db, user_id, team_id=team_id)

    async def revoke_api_key(
        self, user_id: str, key_id: str, *, team_id: Optional[str] = None
    ) -> bool:
        """Revoke a key owned by the user or team. Returns True if revoked."""
        return await self.repo.revoke_api_key(self.db, user_id, key_id, team_id=team_id)
