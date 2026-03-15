from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..youtube_auth_types import (
    YouTubeCookieAccountDTO,
    YouTubeCookieEventDTO,
)


class YouTubeAuthRepository:
    @staticmethod
    def _account_from_row(row: Any) -> YouTubeCookieAccountDTO:
        return YouTubeCookieAccountDTO.model_validate(dict(row._mapping))

    @staticmethod
    def _event_from_row(row: Any) -> YouTubeCookieEventDTO:
        return YouTubeCookieEventDTO.model_validate(dict(row._mapping))

    async def list_accounts(self, db: AsyncSession) -> list[YouTubeCookieAccountDTO]:
        result = await db.execute(
            text(
                """
                SELECT *
                FROM youtube_cookie_accounts
                ORDER BY priority ASC, COALESCE(last_used_at, created_at) ASC, created_at ASC
                """
            )
        )
        return [self._account_from_row(row) for row in result.fetchall()]

    async def count_accounts(self, db: AsyncSession) -> int:
        result = await db.execute(text("SELECT COUNT(*) FROM youtube_cookie_accounts"))
        return int(result.scalar() or 0)

    async def get_account(
        self, db: AsyncSession, account_id: str
    ) -> Optional[YouTubeCookieAccountDTO]:
        result = await db.execute(
            text("SELECT * FROM youtube_cookie_accounts WHERE id = :account_id"),
            {"account_id": account_id},
        )
        row = result.fetchone()
        return self._account_from_row(row) if row else None

    async def list_candidate_accounts(
        self, db: AsyncSession
    ) -> list[YouTubeCookieAccountDTO]:
        result = await db.execute(
            text(
                """
                SELECT *
                FROM youtube_cookie_accounts
                WHERE status IN ('healthy', 'legacy')
                  AND (cooldown_until IS NULL OR cooldown_until <= NOW())
                ORDER BY priority ASC, COALESCE(last_used_at, created_at) ASC, created_at ASC
                """
            )
        )
        return [self._account_from_row(row) for row in result.fetchall()]

    async def create_account(
        self,
        db: AsyncSession,
        *,
        label: str,
        email_hint: Optional[str],
        status: str,
        priority: int,
        playwright_storage_state_path: Optional[str],
        yt_dlp_cookiefile_path: Optional[str],
        created_by_user_id: Optional[str],
    ) -> YouTubeCookieAccountDTO:
        result = await db.execute(
            text(
                """
                INSERT INTO youtube_cookie_accounts (
                    label,
                    email_hint,
                    status,
                    priority,
                    playwright_storage_state_path,
                    yt_dlp_cookiefile_path,
                    created_by_user_id,
                    created_at,
                    updated_at
                )
                VALUES (
                    :label,
                    :email_hint,
                    :status,
                    :priority,
                    :playwright_storage_state_path,
                    :yt_dlp_cookiefile_path,
                    :created_by_user_id,
                    NOW(),
                    NOW()
                )
                RETURNING *
                """
            ),
            {
                "label": label,
                "email_hint": email_hint,
                "status": status,
                "priority": priority,
                "playwright_storage_state_path": playwright_storage_state_path,
                "yt_dlp_cookiefile_path": yt_dlp_cookiefile_path,
                "created_by_user_id": created_by_user_id,
            },
        )
        await db.commit()
        return self._account_from_row(result.fetchone())

    async def update_account(
        self, db: AsyncSession, account_id: str, **fields: Any
    ) -> Optional[YouTubeCookieAccountDTO]:
        allowed = {
            "label",
            "email_hint",
            "status",
            "priority",
            "playwright_storage_state_path",
            "yt_dlp_cookiefile_path",
            "last_used_at",
            "last_verified_at",
            "last_refresh_started_at",
            "last_refresh_completed_at",
            "consecutive_auth_failures",
            "cooldown_until",
            "last_error_code",
            "last_error_message",
        }
        params: dict[str, Any] = {"account_id": account_id}
        set_parts = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            set_parts.append(f"{key} = :{key}")
            params[key] = value

        if not set_parts:
            return await self.get_account(db, account_id)

        set_parts.append("updated_at = NOW()")
        result = await db.execute(
            text(
                f"""
                UPDATE youtube_cookie_accounts
                SET {", ".join(set_parts)}
                WHERE id = :account_id
                RETURNING *
                """
            ),
            params,
        )
        await db.commit()
        row = result.fetchone()
        return self._account_from_row(row) if row else None

    async def create_event(
        self,
        db: AsyncSession,
        *,
        account_id: str,
        event_type: str,
        status: str,
        task_id: Optional[str] = None,
        message: Optional[str] = None,
        metadata_json: Optional[str] = None,
    ) -> YouTubeCookieEventDTO:
        result = await db.execute(
            text(
                """
                INSERT INTO youtube_cookie_events (
                    account_id,
                    event_type,
                    status,
                    task_id,
                    message,
                    metadata_json,
                    created_at
                )
                VALUES (
                    :account_id,
                    :event_type,
                    :status,
                    :task_id,
                    :message,
                    :metadata_json,
                    NOW()
                )
                RETURNING *
                """
            ),
            {
                "account_id": account_id,
                "event_type": event_type,
                "status": status,
                "task_id": task_id,
                "message": message,
                "metadata_json": metadata_json,
            },
        )
        await db.commit()
        return self._event_from_row(result.fetchone())

    async def list_events(
        self, db: AsyncSession, account_id: str
    ) -> list[YouTubeCookieEventDTO]:
        result = await db.execute(
            text(
                """
                SELECT *
                FROM youtube_cookie_events
                WHERE account_id = :account_id
                ORDER BY created_at DESC
                LIMIT 100
                """
            ),
            {"account_id": account_id},
        )
        return [self._event_from_row(row) for row in result.fetchall()]
