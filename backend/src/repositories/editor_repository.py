"""Persistence for non-destructive editor projects and task-scoped assets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EditorAsset, EditorProject


class EditorProjectVersionConflict(Exception):
    """Raised when an optimistic editor-project update loses a race."""

    def __init__(self, current_version: int):
        super().__init__("Editor project version conflict")
        self.current_version = current_version


class EditorRepository:
    @staticmethod
    async def get_project(
        db: AsyncSession, task_id: str
    ) -> EditorProject | None:
        result = await db.execute(
            select(EditorProject).where(EditorProject.task_id == task_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def save_project(
        cls,
        db: AsyncSession,
        task_id: str,
        project: dict[str, Any],
        expected_version: int | None,
    ) -> dict[str, Any]:
        returning = (
            EditorProject.project,
            EditorProject.version,
            EditorProject.created_at,
            EditorProject.updated_at,
        )

        if expected_version is None:
            insert_statement = pg_insert(EditorProject).values(
                task_id=task_id,
                project=project,
                version=1,
            )
            statement = insert_statement.on_conflict_do_update(
                index_elements=[EditorProject.task_id],
                set_={
                    "project": project,
                    "version": EditorProject.version + 1,
                    "updated_at": func.now(),
                },
            ).returning(*returning)
        elif expected_version == 0:
            statement = (
                pg_insert(EditorProject)
                .values(task_id=task_id, project=project, version=1)
                .on_conflict_do_nothing(index_elements=[EditorProject.task_id])
                .returning(*returning)
            )
        else:
            statement = (
                update(EditorProject)
                .where(
                    EditorProject.task_id == task_id,
                    EditorProject.version == expected_version,
                )
                .values(
                    project=project,
                    version=EditorProject.version + 1,
                    updated_at=func.now(),
                )
                .returning(*returning)
            )

        result = await db.execute(statement)
        row = result.mappings().one_or_none()
        if row is None:
            current = await cls.get_project(db, task_id)
            current_version = current.version if current is not None else 0
            await db.rollback()
            raise EditorProjectVersionConflict(current_version)

        await db.commit()
        return dict(row)

    @staticmethod
    async def list_assets(db: AsyncSession, task_id: str) -> list[EditorAsset]:
        result = await db.execute(
            select(EditorAsset)
            .where(EditorAsset.task_id == task_id)
            .order_by(EditorAsset.created_at.asc(), EditorAsset.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_asset(
        db: AsyncSession, task_id: str, asset_id: str
    ) -> EditorAsset | None:
        result = await db.execute(
            select(EditorAsset).where(
                EditorAsset.id == asset_id,
                EditorAsset.task_id == task_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_asset(
        db: AsyncSession,
        *,
        asset_id: str,
        task_id: str,
        name: str,
        kind: str,
        mime_type: str,
        size_bytes: int,
        file_path: str,
        duration: float | None,
        width: int | None,
        height: int | None,
    ) -> EditorAsset:
        asset = EditorAsset(
            id=asset_id,
            task_id=task_id,
            name=name,
            kind=kind,
            mime_type=mime_type,
            size_bytes=size_bytes,
            file_path=file_path,
            duration=duration,
            width=width,
            height=height,
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def delete_asset(
        db: AsyncSession, task_id: str, asset_id: str
    ) -> bool:
        result = await db.execute(
            delete(EditorAsset)
            .where(
                EditorAsset.id == asset_id,
                EditorAsset.task_id == task_id,
            )
            .returning(EditorAsset.id)
        )
        deleted_id = result.scalar_one_or_none()
        await db.commit()
        return deleted_id is not None

    @staticmethod
    async def delete_asset_and_references(
        db: AsyncSession,
        task_id: str,
        asset_id: str,
        expected_version: int,
    ) -> dict[str, Any] | None:
        """Delete an uploaded asset and its timeline references in one transaction."""
        asset_result = await db.execute(
            select(EditorAsset)
            .where(EditorAsset.id == asset_id, EditorAsset.task_id == task_id)
            .with_for_update()
        )
        asset = asset_result.scalar_one_or_none()
        if asset is None:
            return None

        project_result = await db.execute(
            select(EditorProject)
            .where(EditorProject.task_id == task_id)
            .with_for_update()
        )
        project = project_result.scalar_one_or_none()
        current_version = project.version if project is not None else 0
        if current_version != expected_version:
            await db.rollback()
            raise EditorProjectVersionConflict(current_version)

        project_payload = project.project if project is not None else None
        next_payload = project_payload
        next_version = current_version
        updated_at = project.updated_at if project is not None else None
        if project is not None:
            items = project_payload.get("items", [])
            filtered_items = [
                item
                for item in items
                if not isinstance(item, dict) or item.get("assetId") != asset_id
            ]
            next_payload = {**project_payload, "items": filtered_items}
            next_version += 1
            updated_at = datetime.now(timezone.utc)
            project.project = next_payload
            project.version = next_version
            project.updated_at = updated_at

        file_path = asset.file_path
        await db.delete(asset)
        await db.commit()
        return {
            "file_path": file_path,
            "project": next_payload,
            "version": next_version,
            "updated_at": updated_at,
        }
