"""
Source repository - handles all database operations for video sources.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, Dict, Any  # Dict/Any kept for return type hints
import logging

logger = logging.getLogger(__name__)


class SourceRepository:
    """Repository for source-related database operations."""

    @staticmethod
    async def create_source(
        db: AsyncSession, source_type: str, title: str, url: Optional[str] = None
    ) -> str:
        """Create a new source record and return its ID."""
        source_id = str(uuid4())
        try:
            result = await db.execute(
                text(
                    """
                    INSERT INTO sources (id, type, title, url, created_at, updated_at)
                    VALUES (:source_id, :source_type, :title, :url, NOW(), NOW())
                    RETURNING id
                    """
                ),
                {
                    "source_id": source_id,
                    "source_type": source_type,
                    "title": title,
                    "url": url,
                },
            )
            source_id = result.scalar()
            await db.commit()
        except Exception:
            await db.rollback()
            result = await db.execute(
                text(
                    """
                    INSERT INTO sources (id, type, title, created_at, updated_at)
                    VALUES (:source_id, :source_type, :title, NOW(), NOW())
                    RETURNING id
                    """
                ),
                {
                    "source_id": source_id,
                    "source_type": source_type,
                    "title": title,
                },
            )
            source_id = result.scalar()
            await db.commit()

        logger.info(f"Created source {source_id}: {title} ({source_type})")
        return source_id

    @staticmethod
    async def get_source_by_id(
        db: AsyncSession, source_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get source by ID."""
        result = await db.execute(
            text("SELECT * FROM sources WHERE id = :source_id"),
            {"source_id": source_id},
        )
        row = result.fetchone()

        if not row:
            return None

        return {
            "id": row.id,
            "type": row.type,
            "title": row.title,
            "url": getattr(row, "url", None),
            "created_at": row.created_at,
        }

    @staticmethod
    async def update_source_title(db: AsyncSession, source_id: str, title: str) -> None:
        """Update the title of a source."""
        await db.execute(
            text("UPDATE sources SET title = :title WHERE id = :source_id"),
            {"title": title, "source_id": source_id},
        )
        await db.commit()
        logger.info(f"Updated source {source_id} title to: {title}")
