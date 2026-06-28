"""
API key repository - handles all database operations for per-user API keys.

API keys authenticate programmatic clients (such as the SupoClip MCP server)
directly against the backend. Only the SHA-256 hash of a key is ever stored;
the plaintext key is shown to the user exactly once at creation time.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class ApiKeyRepository:
    """Repository for API-key database operations."""

    @staticmethod
    async def create_api_key(
        db: AsyncSession,
        user_id: str,
        name: str,
        key_hash: str,
        key_prefix: str,
    ) -> Dict[str, Any]:
        """Insert a new API key row and return its metadata (never the secret)."""
        key_id = str(uuid4())
        result = await db.execute(
            text(
                """
                INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, created_at)
                VALUES (:id, :user_id, :name, :key_hash, :key_prefix, NOW())
                RETURNING id, name, key_prefix, created_at, last_used_at, revoked_at
                """
            ),
            {
                "id": key_id,
                "user_id": user_id,
                "name": name,
                "key_hash": key_hash,
                "key_prefix": key_prefix,
            },
        )
        row = result.fetchone()
        await db.commit()
        logger.info("Created API key %s for user %s", key_id, user_id)
        return ApiKeyRepository._serialize(row)

    @staticmethod
    async def list_api_keys(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        """List all API keys for a user, newest first. Never returns the secret."""
        result = await db.execute(
            text(
                """
                SELECT id, name, key_prefix, created_at, last_used_at, revoked_at
                FROM api_keys
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                """
            ),
            {"user_id": user_id},
        )
        return [ApiKeyRepository._serialize(row) for row in result.fetchall()]

    @staticmethod
    async def resolve_user_id(db: AsyncSession, key_hash: str) -> Optional[str]:
        """
        Resolve a key hash to its owning user_id for active (non-revoked) keys.

        Also records the last-used timestamp. Returns ``None`` when the key is
        unknown or revoked.
        """
        result = await db.execute(
            text(
                """
                SELECT user_id
                FROM api_keys
                WHERE key_hash = :key_hash AND revoked_at IS NULL
                LIMIT 1
                """
            ),
            {"key_hash": key_hash},
        )
        row = result.fetchone()
        if not row:
            return None

        await db.execute(
            text(
                "UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = :key_hash"
            ),
            {"key_hash": key_hash},
        )
        await db.commit()
        return row.user_id

    @staticmethod
    async def revoke_api_key(db: AsyncSession, user_id: str, key_id: str) -> bool:
        """Revoke a key owned by the user. Returns True if a key was revoked."""
        result = await db.execute(
            text(
                """
                UPDATE api_keys
                SET revoked_at = NOW()
                WHERE id = :id AND user_id = :user_id AND revoked_at IS NULL
                RETURNING id
                """
            ),
            {"id": key_id, "user_id": user_id},
        )
        row = result.fetchone()
        await db.commit()
        if row:
            logger.info("Revoked API key %s for user %s", key_id, user_id)
        return row is not None

    @staticmethod
    def _serialize(row: Any) -> Dict[str, Any]:
        """Convert a DB row to a JSON-friendly dict (without the secret/hash)."""
        return {
            "id": row.id,
            "name": row.name,
            "key_prefix": row.key_prefix,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "revoked": row.revoked_at is not None,
        }
