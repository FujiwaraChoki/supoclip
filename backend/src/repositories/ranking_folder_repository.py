"""
Ranking folder-library repository — the persistent per-user, per-folder pool
of clips a ranking project draws from. Distinct from RankingRepository
(ranking_inputs), which holds only the 5 clips attached to one specific
project: this is the pool those 5 get selected/swapped from, and the place
"prefer unused" usage counts and cross-ranking text memory live, so both
survive across many ranking projects drawn from the same folder.
"""

import random
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Any, Dict, List, Optional

MIN_CLIPS_PER_FOLDER = 5
SELECTION_SIZE = 5


class RankingFolderRepository:
    """Repository for ranking_folders / ranking_folder_clips."""

    @staticmethod
    async def get_or_create_folder(db: AsyncSession, user_id: str, name: str) -> str:
        result = await db.execute(
            text("SELECT id FROM ranking_folders WHERE user_id = :user_id AND name = :name"),
            {"user_id": user_id, "name": name},
        )
        row = result.fetchone()
        if row:
            return row.id

        folder_id = str(uuid4())
        await db.execute(
            text(
                "INSERT INTO ranking_folders (id, user_id, name, created_at) "
                "VALUES (:id, :user_id, :name, NOW())"
            ),
            {"id": folder_id, "user_id": user_id, "name": name},
        )
        await db.commit()
        return folder_id

    @staticmethod
    async def list_folders(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            text("""
                SELECT f.id, f.name, f.created_at, COUNT(c.id) AS clip_count
                FROM ranking_folders f
                LEFT JOIN ranking_folder_clips c ON c.folder_id = f.id
                WHERE f.user_id = :user_id
                GROUP BY f.id, f.name, f.created_at
                ORDER BY f.created_at DESC
            """),
            {"user_id": user_id},
        )
        return [
            {"id": row.id, "name": row.name, "created_at": row.created_at, "clip_count": row.clip_count}
            for row in result.fetchall()
        ]

    @staticmethod
    async def get_folder(db: AsyncSession, user_id: str, folder_id: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text("SELECT id, name FROM ranking_folders WHERE id = :id AND user_id = :user_id"),
            {"id": folder_id, "user_id": user_id},
        )
        row = result.fetchone()
        return {"id": row.id, "name": row.name} if row else None

    @staticmethod
    async def upsert_clip(
        db: AsyncSession,
        folder_id: str,
        file_path: str,
        original_filename: str,
        duration_seconds: Optional[float],
        content_hash: str,
        thumbnail_path: Optional[str] = None,
    ) -> str:
        """Insert a clip, or return the existing row's id (by content_hash)
        unchanged — a re-selected/re-dropped file resolves to the same
        library row instead of duplicating it and resetting its use_count/
        saved_text."""
        existing = await db.execute(
            text(
                "SELECT id FROM ranking_folder_clips WHERE folder_id = :folder_id "
                "AND content_hash = :content_hash"
            ),
            {"folder_id": folder_id, "content_hash": content_hash},
        )
        row = existing.fetchone()
        if row:
            return row.id

        clip_id = str(uuid4())
        await db.execute(
            text("""
                INSERT INTO ranking_folder_clips (
                    id, folder_id, file_path, original_filename, duration_seconds,
                    thumbnail_path, content_hash, created_at
                )
                VALUES (
                    :id, :folder_id, :file_path, :original_filename, :duration_seconds,
                    :thumbnail_path, :content_hash, NOW()
                )
            """),
            {
                "id": clip_id,
                "folder_id": folder_id,
                "file_path": file_path,
                "original_filename": original_filename,
                "duration_seconds": duration_seconds,
                "thumbnail_path": thumbnail_path,
                "content_hash": content_hash,
            },
        )
        await db.commit()
        return clip_id

    @staticmethod
    async def list_clips(db: AsyncSession, folder_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            text("""
                SELECT id, folder_id, file_path, original_filename, duration_seconds,
                       thumbnail_path, saved_text, use_count, last_used_at, created_at
                FROM ranking_folder_clips
                WHERE folder_id = :folder_id
                ORDER BY created_at ASC
            """),
            {"folder_id": folder_id},
        )
        return [dict(row._mapping) for row in result.fetchall()]

    @staticmethod
    async def select_clips(
        db: AsyncSession, folder_id: str, count: int = SELECTION_SIZE
    ) -> List[Dict[str, Any]]:
        """Random selection preferring least-used clips: group candidates by
        use_count ascending, fill from the lowest-use group first (shuffled
        within each group) until `count` is reached — "prefer clips with 0
        prior uses; if not enough, fall back to least-used" per spec."""
        clips = await RankingFolderRepository.list_clips(db, folder_id)
        clips_by_use: Dict[int, List[Dict[str, Any]]] = {}
        for clip in clips:
            clips_by_use.setdefault(clip["use_count"], []).append(clip)

        selected: List[Dict[str, Any]] = []
        for use_count in sorted(clips_by_use.keys()):
            group = clips_by_use[use_count]
            random.shuffle(group)
            remaining = count - len(selected)
            selected.extend(group[:remaining])
            if len(selected) >= count:
                break
        random.shuffle(selected)
        return selected

    @staticmethod
    async def save_text(db: AsyncSession, clip_id: str, text_value: Optional[str]) -> None:
        await db.execute(
            text("UPDATE ranking_folder_clips SET saved_text = :text_value WHERE id = :id"),
            {"text_value": text_value, "id": clip_id},
        )
        await db.commit()

    @staticmethod
    async def mark_used(db: AsyncSession, clip_ids: List[str]) -> None:
        if not clip_ids:
            return
        await db.execute(
            text(
                "UPDATE ranking_folder_clips SET use_count = use_count + 1, "
                "last_used_at = NOW() WHERE id = ANY(CAST(:clip_ids AS text[]))"
            ),
            {"clip_ids": clip_ids},
        )
        await db.commit()

    @staticmethod
    async def get_clip(db: AsyncSession, clip_id: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text("SELECT * FROM ranking_folder_clips WHERE id = :id"),
            {"id": clip_id},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None
