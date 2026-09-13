"""Repository for reusable per-user settings templates (project_templates table)."""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class TemplateRepository:
    """Raw-SQL access to the project_templates table.

    `settings` is stored as JSON-encoded text (same convention as
    generated_clips.reactions/hook_title_variants) rather than a native JSON
    column, matching the rest of this codebase's asyncpg/raw-SQL style.
    """

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "schema_version": row.schema_version,
            "settings": json.loads(row.settings) if row.settings else {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    async def create(
        db: AsyncSession,
        template_id: str,
        user_id: str,
        name: str,
        settings: Dict[str, Any],
        schema_version: int,
    ) -> Dict[str, Any]:
        await db.execute(
            text(
                """
                INSERT INTO project_templates (id, user_id, name, schema_version, settings)
                VALUES (:id, :user_id, :name, :schema_version, :settings)
                """
            ),
            {
                "id": template_id,
                "user_id": user_id,
                "name": name,
                "schema_version": schema_version,
                "settings": json.dumps(settings),
            },
        )
        await db.commit()
        return await TemplateRepository.get_by_id(db, user_id, template_id)

    @staticmethod
    async def list_for_user(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT id, name, schema_version, settings, created_at, updated_at
                FROM project_templates
                WHERE user_id = :user_id
                ORDER BY updated_at DESC
                """
            ),
            {"user_id": user_id},
        )
        return [TemplateRepository._row_to_dict(row) for row in result.fetchall()]

    @staticmethod
    async def get_by_id(
        db: AsyncSession, user_id: str, template_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT id, name, schema_version, settings, created_at, updated_at
                FROM project_templates
                WHERE id = :id AND user_id = :user_id
                """
            ),
            {"id": template_id, "user_id": user_id},
        )
        row = result.fetchone()
        return TemplateRepository._row_to_dict(row) if row else None

    @staticmethod
    async def rename(
        db: AsyncSession, user_id: str, template_id: str, name: str
    ) -> Optional[Dict[str, Any]]:
        await db.execute(
            text(
                """
                UPDATE project_templates
                SET name = :name, updated_at = NOW()
                WHERE id = :id AND user_id = :user_id
                """
            ),
            {"id": template_id, "user_id": user_id, "name": name},
        )
        await db.commit()
        return await TemplateRepository.get_by_id(db, user_id, template_id)

    @staticmethod
    async def delete(db: AsyncSession, user_id: str, template_id: str) -> bool:
        result = await db.execute(
            text(
                "DELETE FROM project_templates WHERE id = :id AND user_id = :user_id"
            ),
            {"id": template_id, "user_id": user_id},
        )
        await db.commit()
        return result.rowcount > 0
