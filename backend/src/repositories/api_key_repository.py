"""
API key repository - handles all database operations for personal and team API keys.

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
        *,
        team_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a new API key row and return its metadata (never the secret)."""
        key_id = str(uuid4())
        result = await db.execute(
            text(
                """
                INSERT INTO api_keys (id, user_id, team_id, name, key_hash, key_prefix, created_at)
                VALUES (:id, :user_id, :team_id, :name, :key_hash, :key_prefix, NOW())
                RETURNING id, team_id, name, key_prefix, created_at, last_used_at, revoked_at
                """
            ),
            {
                "id": key_id,
                "user_id": user_id,
                "team_id": team_id,
                "name": name,
                "key_hash": key_hash,
                "key_prefix": key_prefix,
            },
        )
        row = result.fetchone()
        await db.commit()
        logger.info("Created API key %s for user %s (team=%s)", key_id, user_id, team_id)
        return ApiKeyRepository._serialize(row)

    @staticmethod
    async def list_api_keys(
        db: AsyncSession, user_id: str, *, team_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List API keys for a personal or team workspace, newest first."""
        if team_id:
            result = await db.execute(
                text(
                    """
                    SELECT id, team_id, name, key_prefix, created_at, last_used_at, revoked_at
                    FROM api_keys
                    WHERE team_id = :team_id
                    ORDER BY created_at DESC
                    """
                ),
                {"team_id": team_id},
            )
        else:
            result = await db.execute(
                text(
                    """
                    SELECT id, team_id, name, key_prefix, created_at, last_used_at, revoked_at
                    FROM api_keys
                    WHERE user_id = :user_id AND team_id IS NULL
                    ORDER BY created_at DESC
                    """
                ),
                {"user_id": user_id},
            )
        return [ApiKeyRepository._serialize(row) for row in result.fetchall()]

    @staticmethod
    async def resolve_key_context(
        db: AsyncSession, key_hash: str
    ) -> Optional[Dict[str, Optional[str]]]:
        """Resolve an active key hash to ``{"user_id": ..., "team_id": ...}``."""
        result = await db.execute(
            text(
                """
                SELECT user_id, team_id
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
        return {
            "user_id": row.user_id,
            "team_id": getattr(row, "team_id", None),
        }

    @staticmethod
    async def resolve_user_id(db: AsyncSession, key_hash: str) -> Optional[str]:
        """Resolve a key hash to its owning user_id for active (non-revoked) keys."""
        context = await ApiKeyRepository.resolve_key_context(db, key_hash)
        return context["user_id"] if context else None

    @staticmethod
    async def revoke_api_key(
        db: AsyncSession,
        user_id: str,
        key_id: str,
        *,
        team_id: Optional[str] = None,
    ) -> bool:
        """Revoke a key owned by the user or active team."""
        if team_id:
            result = await db.execute(
                text(
                    """
                    UPDATE api_keys
                    SET revoked_at = NOW()
                    WHERE id = :id AND team_id = :team_id AND revoked_at IS NULL
                    RETURNING id
                    """
                ),
                {"id": key_id, "team_id": team_id},
            )
        else:
            result = await db.execute(
                text(
                    """
                    UPDATE api_keys
                    SET revoked_at = NOW()
                    WHERE id = :id AND user_id = :user_id AND team_id IS NULL AND revoked_at IS NULL
                    RETURNING id
                    """
                ),
                {"id": key_id, "user_id": user_id},
            )
        row = result.fetchone()
        await db.commit()
        if row:
            logger.info("Revoked API key %s for user %s (team=%s)", key_id, user_id, team_id)
        return row is not None

    @staticmethod
    def _serialize(row: Any) -> Dict[str, Any]:
        """Convert a DB row to a JSON-friendly dict (without the secret/hash)."""
        return {
            "id": row.id,
            "team_id": getattr(row, "team_id", None),
            "name": row.name,
            "key_prefix": row.key_prefix,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "revoked": row.revoked_at is not None,
        }
