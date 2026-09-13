"""Repository for content-policy word lists and per-project policy settings."""

import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..content_policy import CATEGORIES, DEFAULT_CATEGORIES_ENABLED, DEFAULT_WORD_LISTS


class ContentPolicyRepository:
    """Raw-SQL access to content_policy_word_lists / content_policy_project_settings."""

    @staticmethod
    async def get_word_lists_for_user(
        db: AsyncSession, user_id: str
    ) -> Dict[str, Dict[str, List[str]]]:
        """Every category's effective word lists: a user's saved override where
        one exists, else the built-in default."""
        result = await db.execute(
            text(
                """
                SELECT category, words, is_default_reset
                FROM content_policy_word_lists
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        overrides = {row.category: json.loads(row.words) for row in result.fetchall()}

        lists: Dict[str, Dict[str, List[str]]] = {}
        for category in CATEGORIES:
            lists[category] = overrides.get(category, DEFAULT_WORD_LISTS[category])
        return lists

    @staticmethod
    async def get_word_list_status_for_user(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        """Per category: word count + whether it's a user override or still default."""
        lists = await ContentPolicyRepository.get_word_lists_for_user(db, user_id)
        result = await db.execute(
            text(
                "SELECT category FROM content_policy_word_lists WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        overridden = {row.category for row in result.fetchall()}
        return [
            {
                "category": category,
                "words": lists[category],
                "word_count": sum(len(v) for v in lists[category].values()),
                "is_default": category not in overridden,
            }
            for category in CATEGORIES
        ]

    @staticmethod
    async def upsert_word_list(
        db: AsyncSession, user_id: str, category: str, words: Dict[str, List[str]]
    ) -> None:
        await db.execute(
            text(
                """
                INSERT INTO content_policy_word_lists (id, user_id, category, words, is_default_reset)
                VALUES (:id, :user_id, :category, :words, FALSE)
                ON CONFLICT (user_id, category) DO UPDATE SET
                    words = EXCLUDED.words,
                    is_default_reset = FALSE,
                    updated_at = NOW()
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "category": category,
                "words": json.dumps(words),
            },
        )
        await db.commit()

    @staticmethod
    async def reset_word_list_to_default(db: AsyncSession, user_id: str, category: str) -> None:
        """Delete the user's override row so DEFAULT_WORD_LISTS applies again —
        avoids drift if the built-in defaults change later."""
        await db.execute(
            text(
                "DELETE FROM content_policy_word_lists WHERE user_id = :user_id AND category = :category"
            ),
            {"user_id": user_id, "category": category},
        )
        await db.commit()

    @staticmethod
    async def get_project_settings(db: AsyncSession, task_id: str) -> Dict[str, Any]:
        result = await db.execute(
            text(
                """
                SELECT enabled, sensitivity, categories_enabled, ollama_borderline_check_enabled
                FROM content_policy_project_settings
                WHERE task_id = :task_id
                """
            ),
            {"task_id": task_id},
        )
        row = result.fetchone()
        if not row:
            return {
                "enabled": True,
                "sensitivity": "medium",
                "categories_enabled": DEFAULT_CATEGORIES_ENABLED,
                "ollama_borderline_check_enabled": False,
            }
        return {
            "enabled": row.enabled,
            "sensitivity": row.sensitivity,
            "categories_enabled": (
                json.loads(row.categories_enabled)
                if row.categories_enabled
                else DEFAULT_CATEGORIES_ENABLED
            ),
            "ollama_borderline_check_enabled": row.ollama_borderline_check_enabled,
        }

    @staticmethod
    async def upsert_project_settings(
        db: AsyncSession,
        task_id: str,
        *,
        enabled: bool = True,
        sensitivity: str = "medium",
        categories_enabled: Optional[Dict[str, bool]] = None,
        ollama_borderline_check_enabled: bool = False,
    ) -> Dict[str, Any]:
        await db.execute(
            text(
                """
                INSERT INTO content_policy_project_settings
                    (task_id, enabled, sensitivity, categories_enabled, ollama_borderline_check_enabled)
                VALUES (:task_id, :enabled, :sensitivity, :categories_enabled, :ollama_check)
                ON CONFLICT (task_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    sensitivity = EXCLUDED.sensitivity,
                    categories_enabled = EXCLUDED.categories_enabled,
                    ollama_borderline_check_enabled = EXCLUDED.ollama_borderline_check_enabled,
                    updated_at = NOW()
                """
            ),
            {
                "task_id": task_id,
                "enabled": enabled,
                "sensitivity": sensitivity,
                "categories_enabled": json.dumps(categories_enabled or DEFAULT_CATEGORIES_ENABLED),
                "ollama_check": ollama_borderline_check_enabled,
            },
        )
        await db.commit()
        return await ContentPolicyRepository.get_project_settings(db, task_id)
