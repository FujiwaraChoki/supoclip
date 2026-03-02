"""
Processing cache repository for reusable pipeline artifacts.
"""

from typing import Optional, Dict, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CacheRepository:
    @staticmethod
    async def get_cache(db: AsyncSession, cache_key: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT cache_key, source_url, source_type, video_path, transcript_text, analysis_json
                FROM processing_cache
                WHERE cache_key = :cache_key
                """
            ),
            {"cache_key": cache_key},
        )
        row = result.fetchone()
        if not row:
            return None

        return {
            "cache_key": row.cache_key,
            "source_url": row.source_url,
            "source_type": row.source_type,
            "video_path": row.video_path,
            "transcript_text": row.transcript_text,
            "analysis_json": row.analysis_json,
        }

    @staticmethod
    async def upsert_cache(
        db: AsyncSession,
        cache_key: str,
        source_url: str,
        source_type: str,
        video_path: Optional[str] = None,
        transcript_text: Optional[str] = None,
        analysis_json: Optional[str] = None,
    ) -> None:
        await db.execute(
            text(
                """
                INSERT INTO processing_cache (
                    cache_key, source_url, source_type, video_path, transcript_text, analysis_json, created_at, updated_at
                )
                VALUES (
                    :cache_key, :source_url, :source_type, :video_path, :transcript_text, :analysis_json, NOW(), NOW()
                )
                ON CONFLICT (cache_key)
                DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    source_type = EXCLUDED.source_type,
                    video_path = COALESCE(EXCLUDED.video_path, processing_cache.video_path),
                    transcript_text = COALESCE(EXCLUDED.transcript_text, processing_cache.transcript_text),
                    analysis_json = COALESCE(EXCLUDED.analysis_json, processing_cache.analysis_json),
                    updated_at = NOW()
                """
            ),
            {
                "cache_key": cache_key,
                "source_url": source_url,
                "source_type": source_type,
                "video_path": video_path,
                "transcript_text": transcript_text,
                "analysis_json": analysis_json,
            },
        )
        await db.commit()
