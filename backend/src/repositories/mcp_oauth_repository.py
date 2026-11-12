"""
OAuth clients registered with the hosted MCP server (``mcp_oauth_clients``).

Raw SQL like the other repositories. Pending authorize requests and
authorization codes are short-lived and live in Redis (see
``services/mcp_oauth_service.py``), not here.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class McpOAuthClientRepository:
    @staticmethod
    async def create_client(
        db: AsyncSession,
        *,
        client_id: str,
        client_secret: Optional[str],
        client_name: Optional[str],
        metadata: Dict[str, Any],
    ) -> None:
        await db.execute(
            text(
                """
                INSERT INTO mcp_oauth_clients (client_id, client_secret, client_name, metadata)
                VALUES (:client_id, :client_secret, :client_name, CAST(:metadata AS JSONB))
                """
            ),
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "client_name": client_name,
                "metadata": json.dumps(metadata),
            },
        )
        await db.commit()

    @staticmethod
    async def get_client(db: AsyncSession, client_id: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT client_id, client_secret, client_name, metadata
                FROM mcp_oauth_clients
                WHERE client_id = :client_id
                """
            ),
            {"client_id": client_id},
        )
        row = result.mappings().fetchone()
        if row is None:
            return None
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return {
            "client_id": row["client_id"],
            "client_secret": row["client_secret"],
            "client_name": row["client_name"],
            "metadata": metadata or {},
        }

    @staticmethod
    async def touch_client(db: AsyncSession, client_id: str) -> None:
        await db.execute(
            text("UPDATE mcp_oauth_clients SET last_used_at = NOW() WHERE client_id = :client_id"),
            {"client_id": client_id},
        )
        await db.commit()
