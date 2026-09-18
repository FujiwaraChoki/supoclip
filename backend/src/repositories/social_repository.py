"""
Social repository - database operations for connected social accounts,
OAuth handshakes, published posts and their performance metrics.

Tokens are stored encrypted (see ``runtime_settings.encrypt_setting_value``);
this layer never decrypts them. Serialized account rows never include token
columns so they are safe to return from API routes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

POST_STATUSES = (
    "scheduled",
    "queued",
    "publishing",
    "published",
    "failed",
    "cancelled",
)
TERMINAL_POST_STATUSES = {"published", "failed", "cancelled"}

_ACCOUNT_PUBLIC_COLUMNS = """
    id, user_id, provider, external_account_id, display_name, username,
    avatar_url, token_expires_at, scopes, metadata, status, last_error,
    created_at, updated_at, revoked_at
"""

_POST_COLUMNS = """
    p.id, p.user_id, p.task_id, p.clip_id, p.social_account_id, p.provider,
    p.status, p.scheduled_for, p.title, p.caption, p.hashtags, p.privacy_level,
    p.media_token, p.media_token_expires_at, p.source_file_path,
    p.pending_handle, p.external_post_id, p.external_url, p.error_message, p.attempts,
    p.hook_type, p.hook_title, p.virality_score, p.hook_score,
    p.engagement_score, p.value_score, p.shareability_score, p.clip_duration,
    p.published_at, p.last_metrics_at, p.metrics, p.created_at, p.updated_at,
    a.display_name AS account_display_name, a.username AS account_username,
    a.avatar_url AS account_avatar_url, a.status AS account_status
"""

_POST_UPDATABLE_COLUMNS = {
    "status",
    "scheduled_for",
    "title",
    "caption",
    "hashtags",
    "privacy_level",
    "media_token",
    "media_token_expires_at",
    "source_file_path",
    "pending_handle",
    "external_post_id",
    "external_url",
    "error_message",
    "attempts",
    "published_at",
    "last_metrics_at",
    "metrics",
    "social_account_id",
}


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


class SocialRepository:
    """Repository for social publishing tables."""

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------
    @staticmethod
    async def upsert_account(
        db: AsyncSession,
        *,
        user_id: str,
        provider: str,
        external_account_id: str,
        display_name: Optional[str],
        username: Optional[str],
        avatar_url: Optional[str],
        access_token_encrypted: str,
        refresh_token_encrypted: Optional[str],
        token_expires_at: Optional[datetime],
        scopes: Optional[str],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = await db.execute(
            text(
                f"""
                INSERT INTO social_accounts (
                    id, user_id, provider, external_account_id, display_name, username,
                    avatar_url, access_token_encrypted, refresh_token_encrypted,
                    token_expires_at, scopes, metadata, status, last_error,
                    created_at, updated_at, revoked_at
                )
                VALUES (
                    :id, :user_id, :provider, :external_account_id, :display_name, :username,
                    :avatar_url, :access_token_encrypted, :refresh_token_encrypted,
                    :token_expires_at, :scopes, CAST(:metadata AS jsonb), 'active', NULL,
                    NOW(), NOW(), NULL
                )
                ON CONFLICT (user_id, provider, external_account_id)
                DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    username = EXCLUDED.username,
                    avatar_url = EXCLUDED.avatar_url,
                    access_token_encrypted = EXCLUDED.access_token_encrypted,
                    refresh_token_encrypted = COALESCE(
                        EXCLUDED.refresh_token_encrypted, social_accounts.refresh_token_encrypted
                    ),
                    token_expires_at = EXCLUDED.token_expires_at,
                    scopes = EXCLUDED.scopes,
                    metadata = social_accounts.metadata || EXCLUDED.metadata,
                    status = 'active',
                    last_error = NULL,
                    revoked_at = NULL,
                    updated_at = NOW()
                RETURNING {_ACCOUNT_PUBLIC_COLUMNS}
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "provider": provider,
                "external_account_id": external_account_id,
                "display_name": display_name,
                "username": username,
                "avatar_url": avatar_url,
                "access_token_encrypted": access_token_encrypted,
                "refresh_token_encrypted": refresh_token_encrypted,
                "token_expires_at": token_expires_at,
                "scopes": scopes,
                "metadata": json.dumps(metadata or {}),
            },
        )
        row = result.fetchone()
        await db.commit()
        return SocialRepository.serialize_account(row)

    @staticmethod
    async def list_accounts(
        db: AsyncSession, user_id: str, include_revoked: bool = False
    ) -> List[Dict[str, Any]]:
        revoked_clause = "" if include_revoked else "AND revoked_at IS NULL"
        result = await db.execute(
            text(
                f"""
                SELECT {_ACCOUNT_PUBLIC_COLUMNS}
                FROM social_accounts
                WHERE user_id = :user_id {revoked_clause}
                ORDER BY provider ASC, created_at ASC
                """
            ),
            {"user_id": user_id},
        )
        return [SocialRepository.serialize_account(row) for row in result.fetchall()]

    @staticmethod
    async def get_account_with_tokens(
        db: AsyncSession, account_id: str
    ) -> Optional[Dict[str, Any]]:
        """Internal: returns the account including encrypted token columns."""
        result = await db.execute(
            text(
                f"""
                SELECT {_ACCOUNT_PUBLIC_COLUMNS},
                       access_token_encrypted, refresh_token_encrypted
                FROM social_accounts
                WHERE id = :id
                """
            ),
            {"id": account_id},
        )
        row = result.fetchone()
        if not row:
            return None
        account = SocialRepository.serialize_account(row)
        account["access_token_encrypted"] = row.access_token_encrypted
        account["refresh_token_encrypted"] = row.refresh_token_encrypted
        return account

    @staticmethod
    async def get_user_account(
        db: AsyncSession, user_id: str, account_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                f"""
                SELECT {_ACCOUNT_PUBLIC_COLUMNS}
                FROM social_accounts
                WHERE id = :id AND user_id = :user_id
                """
            ),
            {"id": account_id, "user_id": user_id},
        )
        row = result.fetchone()
        return SocialRepository.serialize_account(row) if row else None

    @staticmethod
    async def update_account_tokens(
        db: AsyncSession,
        account_id: str,
        *,
        access_token_encrypted: str,
        refresh_token_encrypted: Optional[str],
        token_expires_at: Optional[datetime],
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE social_accounts
                SET access_token_encrypted = :access_token_encrypted,
                    refresh_token_encrypted = COALESCE(:refresh_token_encrypted, refresh_token_encrypted),
                    token_expires_at = :token_expires_at,
                    status = 'active',
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": account_id,
                "access_token_encrypted": access_token_encrypted,
                "refresh_token_encrypted": refresh_token_encrypted,
                "token_expires_at": token_expires_at,
            },
        )
        await db.commit()

    @staticmethod
    async def set_account_status(
        db: AsyncSession, account_id: str, status: str, last_error: Optional[str]
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE social_accounts
                SET status = :status, last_error = :last_error, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": account_id, "status": status, "last_error": last_error},
        )
        await db.commit()

    @staticmethod
    async def revoke_account(db: AsyncSession, user_id: str, account_id: str) -> bool:
        result = await db.execute(
            text(
                """
                UPDATE social_accounts
                SET status = 'revoked',
                    revoked_at = NOW(),
                    access_token_encrypted = '',
                    refresh_token_encrypted = NULL,
                    updated_at = NOW()
                WHERE id = :id AND user_id = :user_id AND revoked_at IS NULL
                RETURNING id
                """
            ),
            {"id": account_id, "user_id": user_id},
        )
        row = result.fetchone()
        if row:
            # Scheduled posts can no longer be delivered without credentials.
            await db.execute(
                text(
                    """
                    UPDATE social_posts
                    SET status = 'cancelled',
                        error_message = 'Account disconnected before publishing',
                        updated_at = NOW()
                    WHERE social_account_id = :id AND status IN ('scheduled', 'queued')
                    """
                ),
                {"id": account_id},
            )
        await db.commit()
        return row is not None

    @staticmethod
    def serialize_account(row: Any) -> Dict[str, Any]:
        return {
            "id": row.id,
            "user_id": row.user_id,
            "provider": row.provider,
            "external_account_id": row.external_account_id,
            "display_name": row.display_name,
            "username": row.username,
            "avatar_url": row.avatar_url,
            "token_expires_at": _iso(row.token_expires_at),
            "scopes": row.scopes,
            "metadata": _json_value(row.metadata) or {},
            "status": row.status,
            "last_error": row.last_error,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "revoked_at": _iso(row.revoked_at),
        }

    # ------------------------------------------------------------------
    # OAuth state
    # ------------------------------------------------------------------
    @staticmethod
    async def create_oauth_state(
        db: AsyncSession,
        *,
        state: str,
        user_id: str,
        provider: str,
        code_verifier: Optional[str],
        redirect_uri: str,
        ttl_seconds: int = 600,
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        await db.execute(
            text(
                """
                INSERT INTO social_oauth_states (
                    state, user_id, provider, code_verifier, redirect_uri, created_at, expires_at
                )
                VALUES (:state, :user_id, :provider, :code_verifier, :redirect_uri, NOW(), :expires_at)
                """
            ),
            {
                "state": state,
                "user_id": user_id,
                "provider": provider,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "expires_at": expires_at,
            },
        )
        await db.commit()

    @staticmethod
    async def consume_oauth_state(
        db: AsyncSession, state: str
    ) -> Optional[Dict[str, Any]]:
        """Delete and return the state row; ``None`` when unknown or expired."""
        result = await db.execute(
            text(
                """
                DELETE FROM social_oauth_states
                WHERE state = :state
                RETURNING state, user_id, provider, code_verifier, redirect_uri, expires_at
                """
            ),
            {"state": state},
        )
        row = result.fetchone()
        await db.commit()
        if not row:
            return None
        expires_at = row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at < datetime.now(timezone.utc):
            return None
        return {
            "state": row.state,
            "user_id": row.user_id,
            "provider": row.provider,
            "code_verifier": row.code_verifier,
            "redirect_uri": row.redirect_uri,
        }

    @staticmethod
    async def purge_expired_oauth_states(db: AsyncSession) -> int:
        result = await db.execute(
            text("DELETE FROM social_oauth_states WHERE expires_at < NOW() RETURNING state")
        )
        rows = result.fetchall()
        await db.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------
    @staticmethod
    async def create_post(
        db: AsyncSession,
        *,
        user_id: str,
        task_id: Optional[str],
        clip_id: Optional[str],
        social_account_id: str,
        provider: str,
        status: str,
        scheduled_for: Optional[datetime],
        title: Optional[str],
        caption: Optional[str],
        hashtags: List[str],
        privacy_level: str,
        media_token: Optional[str],
        source_file_path: Optional[str],
        clip_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        post_id = str(uuid4())
        await db.execute(
            text(
                """
                INSERT INTO social_posts (
                    id, user_id, task_id, clip_id, social_account_id, provider, status,
                    scheduled_for, title, caption, hashtags, privacy_level, media_token,
                    source_file_path, hook_type, hook_title, virality_score, hook_score,
                    engagement_score, value_score, shareability_score, clip_duration,
                    created_at, updated_at
                )
                VALUES (
                    :id, :user_id, :task_id, :clip_id, :social_account_id, :provider, :status,
                    :scheduled_for, :title, :caption, :hashtags, :privacy_level, :media_token,
                    :source_file_path, :hook_type, :hook_title, :virality_score, :hook_score,
                    :engagement_score, :value_score, :shareability_score, :clip_duration,
                    NOW(), NOW()
                )
                """
            ),
            {
                "id": post_id,
                "user_id": user_id,
                "task_id": task_id,
                "clip_id": clip_id,
                "social_account_id": social_account_id,
                "provider": provider,
                "status": status,
                "scheduled_for": scheduled_for,
                "title": title,
                "caption": caption,
                "hashtags": list(hashtags or []),
                "privacy_level": privacy_level,
                "media_token": media_token,
                "source_file_path": source_file_path,
                "hook_type": clip_snapshot.get("hook_type"),
                "hook_title": clip_snapshot.get("hook_title"),
                "virality_score": clip_snapshot.get("virality_score"),
                "hook_score": clip_snapshot.get("hook_score"),
                "engagement_score": clip_snapshot.get("engagement_score"),
                "value_score": clip_snapshot.get("value_score"),
                "shareability_score": clip_snapshot.get("shareability_score"),
                "clip_duration": clip_snapshot.get("duration"),
            },
        )
        await db.commit()
        post = await SocialRepository.get_post(db, post_id)
        assert post is not None
        return post

    @staticmethod
    async def get_post(db: AsyncSession, post_id: str) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                f"""
                SELECT {_POST_COLUMNS}
                FROM social_posts p
                LEFT JOIN social_accounts a ON a.id = p.social_account_id
                WHERE p.id = :id
                """
            ),
            {"id": post_id},
        )
        row = result.fetchone()
        return SocialRepository.serialize_post(row) if row else None

    @staticmethod
    async def get_user_post(
        db: AsyncSession, user_id: str, post_id: str
    ) -> Optional[Dict[str, Any]]:
        post = await SocialRepository.get_post(db, post_id)
        if not post or post.get("user_id") != user_id:
            return None
        return post

    @staticmethod
    async def get_post_by_media_token(
        db: AsyncSession, media_token: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            text(
                f"""
                SELECT {_POST_COLUMNS}
                FROM social_posts p
                LEFT JOIN social_accounts a ON a.id = p.social_account_id
                WHERE p.media_token = :media_token
                """
            ),
            {"media_token": media_token},
        )
        row = result.fetchone()
        return SocialRepository.serialize_post(row) if row else None

    @staticmethod
    async def list_posts(
        db: AsyncSession,
        user_id: str,
        *,
        task_id: Optional[str] = None,
        clip_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["p.user_id = :user_id"]
        params: Dict[str, Any] = {"user_id": user_id, "limit": max(1, min(limit, 500))}
        if task_id:
            clauses.append("p.task_id = :task_id")
            params["task_id"] = task_id
        if clip_id:
            clauses.append("p.clip_id = :clip_id")
            params["clip_id"] = clip_id
        if status:
            clauses.append("p.status = :status")
            params["status"] = status
        result = await db.execute(
            text(
                f"""
                SELECT {_POST_COLUMNS}
                FROM social_posts p
                LEFT JOIN social_accounts a ON a.id = p.social_account_id
                WHERE {' AND '.join(clauses)}
                ORDER BY p.created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [SocialRepository.serialize_post(row) for row in result.fetchall()]

    @staticmethod
    async def update_post(db: AsyncSession, post_id: str, **fields: Any) -> None:
        unknown = set(fields) - _POST_UPDATABLE_COLUMNS
        if unknown:
            raise ValueError(f"Cannot update social post columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = []
        params: Dict[str, Any] = {"id": post_id}
        for column, value in fields.items():
            if column == "metrics":
                assignments.append("metrics = CAST(:metrics AS jsonb)")
                params["metrics"] = json.dumps(value) if value is not None else None
            elif column == "hashtags":
                assignments.append("hashtags = :hashtags")
                params["hashtags"] = list(value or [])
            else:
                assignments.append(f"{column} = :{column}")
                params[column] = value
        assignments.append("updated_at = NOW()")
        await db.execute(
            text(f"UPDATE social_posts SET {', '.join(assignments)} WHERE id = :id"),
            params,
        )
        await db.commit()

    @staticmethod
    async def delete_post(db: AsyncSession, user_id: str, post_id: str) -> bool:
        result = await db.execute(
            text(
                """
                DELETE FROM social_posts
                WHERE id = :id AND user_id = :user_id
                RETURNING id
                """
            ),
            {"id": post_id, "user_id": user_id},
        )
        row = result.fetchone()
        await db.commit()
        return row is not None

    @staticmethod
    async def claim_due_scheduled_posts(
        db: AsyncSession, limit: int = 50
    ) -> List[str]:
        """Atomically move due scheduled posts to ``queued`` and return their ids."""
        result = await db.execute(
            text(
                """
                UPDATE social_posts
                SET status = 'queued', updated_at = NOW()
                WHERE id IN (
                    SELECT id FROM social_posts
                    WHERE status = 'scheduled'
                      AND scheduled_for IS NOT NULL
                      AND scheduled_for <= NOW()
                    ORDER BY scheduled_for ASC
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id
                """
            ),
            {"limit": limit},
        )
        ids = [row.id for row in result.fetchall()]
        await db.commit()
        return ids

    @staticmethod
    async def list_posts_for_metrics_refresh(
        db: AsyncSession,
        *,
        refreshed_before: datetime,
        max_age_days: int,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                f"""
                SELECT {_POST_COLUMNS}
                FROM social_posts p
                LEFT JOIN social_accounts a ON a.id = p.social_account_id
                WHERE p.status = 'published'
                  AND p.external_post_id IS NOT NULL
                  AND p.social_account_id IS NOT NULL
                  AND a.revoked_at IS NULL
                  AND a.status = 'active'
                  AND p.published_at >= NOW() - (:max_age_days * INTERVAL '1 day')
                  AND (p.last_metrics_at IS NULL OR p.last_metrics_at <= :refreshed_before)
                ORDER BY p.last_metrics_at ASC NULLS FIRST
                LIMIT :limit
                """
            ),
            {
                "refreshed_before": refreshed_before,
                "max_age_days": max_age_days,
                "limit": limit,
            },
        )
        return [SocialRepository.serialize_post(row) for row in result.fetchall()]

    @staticmethod
    async def list_pending_publishes(
        db: AsyncSession, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Posts whose platform accepted the upload but has not surfaced a post id yet."""
        result = await db.execute(
            text(
                f"""
                SELECT {_POST_COLUMNS}
                FROM social_posts p
                LEFT JOIN social_accounts a ON a.id = p.social_account_id
                WHERE p.status = 'publishing'
                  AND p.external_post_id IS NULL
                  AND p.pending_handle IS NOT NULL
                ORDER BY p.updated_at ASC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        return [SocialRepository.serialize_post(row) for row in result.fetchall()]

    @staticmethod
    def serialize_post(row: Any) -> Dict[str, Any]:
        metrics = _json_value(row.metrics)
        return {
            "id": row.id,
            "user_id": row.user_id,
            "task_id": row.task_id,
            "clip_id": row.clip_id,
            "social_account_id": row.social_account_id,
            "provider": row.provider,
            "status": row.status,
            "scheduled_for": _iso(row.scheduled_for),
            "title": row.title,
            "caption": row.caption,
            "hashtags": list(row.hashtags or []),
            "privacy_level": row.privacy_level,
            "media_token": row.media_token,
            "media_token_expires_at": _iso(row.media_token_expires_at),
            "source_file_path": row.source_file_path,
            "pending_handle": row.pending_handle,
            "external_post_id": row.external_post_id,
            "external_url": row.external_url,
            "error_message": row.error_message,
            "attempts": row.attempts,
            "hook_type": row.hook_type,
            "hook_title": row.hook_title,
            "virality_score": row.virality_score,
            "hook_score": row.hook_score,
            "engagement_score": row.engagement_score,
            "value_score": row.value_score,
            "shareability_score": row.shareability_score,
            "clip_duration": row.clip_duration,
            "published_at": _iso(row.published_at),
            "last_metrics_at": _iso(row.last_metrics_at),
            "metrics": metrics,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "account": {
                "display_name": row.account_display_name,
                "username": row.account_username,
                "avatar_url": row.account_avatar_url,
                "status": row.account_status,
            },
        }

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    @staticmethod
    async def insert_metrics(
        db: AsyncSession, post_id: str, metrics: Dict[str, Any], raw: Optional[Dict[str, Any]]
    ) -> None:
        await db.execute(
            text(
                """
                INSERT INTO social_post_metrics (
                    id, post_id, captured_at, views, likes, comments, shares, saves, raw
                )
                VALUES (
                    :id, :post_id, NOW(), :views, :likes, :comments, :shares, :saves,
                    CAST(:raw AS jsonb)
                )
                """
            ),
            {
                "id": str(uuid4()),
                "post_id": post_id,
                "views": metrics.get("views"),
                "likes": metrics.get("likes"),
                "comments": metrics.get("comments"),
                "shares": metrics.get("shares"),
                "saves": metrics.get("saves"),
                "raw": json.dumps(raw or {}, default=str),
            },
        )
        await db.execute(
            text(
                """
                UPDATE social_posts
                SET metrics = CAST(:metrics AS jsonb), last_metrics_at = NOW(), updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": post_id, "metrics": json.dumps(metrics, default=str)},
        )
        await db.commit()

    @staticmethod
    async def list_metrics_history(
        db: AsyncSession, post_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT captured_at, views, likes, comments, shares, saves
                FROM social_post_metrics
                WHERE post_id = :post_id
                ORDER BY captured_at DESC
                LIMIT :limit
                """
            ),
            {"post_id": post_id, "limit": limit},
        )
        return [
            {
                "captured_at": _iso(row.captured_at),
                "views": row.views,
                "likes": row.likes,
                "comments": row.comments,
                "shares": row.shares,
                "saves": row.saves,
            }
            for row in result.fetchall()
        ]

    # ------------------------------------------------------------------
    # Performance aggregates (the feedback loop)
    # ------------------------------------------------------------------
    @staticmethod
    async def get_performance_by_hook_type(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT
                    COALESCE(NULLIF(p.hook_type, ''), 'none') AS hook_type,
                    COUNT(*) AS posts,
                    AVG((p.metrics->>'views')::numeric) AS avg_views,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY (p.metrics->>'views')::numeric
                    ) AS median_views,
                    AVG((p.metrics->>'likes')::numeric) AS avg_likes,
                    AVG((p.metrics->>'comments')::numeric) AS avg_comments,
                    AVG((p.metrics->>'shares')::numeric) AS avg_shares,
                    AVG(p.clip_duration) AS avg_duration,
                    AVG(p.virality_score) AS avg_predicted_score,
                    AVG(
                        CASE
                            WHEN COALESCE((p.metrics->>'views')::numeric, 0) > 0 THEN
                                (
                                    COALESCE((p.metrics->>'likes')::numeric, 0)
                                    + COALESCE((p.metrics->>'comments')::numeric, 0)
                                    + COALESCE((p.metrics->>'shares')::numeric, 0)
                                ) / (p.metrics->>'views')::numeric
                            ELSE NULL
                        END
                    ) AS avg_engagement_rate
                FROM social_posts p
                WHERE p.user_id = :user_id
                  AND p.status = 'published'
                  AND p.metrics IS NOT NULL
                  AND (p.metrics->>'views') IS NOT NULL
                GROUP BY COALESCE(NULLIF(p.hook_type, ''), 'none')
                ORDER BY avg_views DESC NULLS LAST
                """
            ),
            {"user_id": user_id},
        )
        return [
            {
                "hook_type": row.hook_type,
                "posts": int(row.posts or 0),
                "avg_views": _to_float(row.avg_views),
                "median_views": _to_float(row.median_views),
                "avg_likes": _to_float(row.avg_likes),
                "avg_comments": _to_float(row.avg_comments),
                "avg_shares": _to_float(row.avg_shares),
                "avg_duration": _to_float(row.avg_duration),
                "avg_predicted_score": _to_float(row.avg_predicted_score),
                "avg_engagement_rate": _to_float(row.avg_engagement_rate),
            }
            for row in result.fetchall()
        ]

    @staticmethod
    async def get_performance_by_provider(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                """
                SELECT
                    p.provider,
                    COUNT(*) AS posts,
                    COUNT(*) FILTER (WHERE p.metrics IS NOT NULL) AS measured_posts,
                    SUM((p.metrics->>'views')::numeric) AS total_views,
                    AVG((p.metrics->>'views')::numeric) AS avg_views,
                    SUM((p.metrics->>'likes')::numeric) AS total_likes,
                    SUM((p.metrics->>'comments')::numeric) AS total_comments,
                    SUM((p.metrics->>'shares')::numeric) AS total_shares
                FROM social_posts p
                WHERE p.user_id = :user_id AND p.status = 'published'
                GROUP BY p.provider
                ORDER BY total_views DESC NULLS LAST
                """
            ),
            {"user_id": user_id},
        )
        return [
            {
                "provider": row.provider,
                "posts": int(row.posts or 0),
                "measured_posts": int(row.measured_posts or 0),
                "total_views": _to_float(row.total_views),
                "avg_views": _to_float(row.avg_views),
                "total_likes": _to_float(row.total_likes),
                "total_comments": _to_float(row.total_comments),
                "total_shares": _to_float(row.total_shares),
            }
            for row in result.fetchall()
        ]

    @staticmethod
    async def get_duration_performance(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        """Average views by clip-length bucket, to learn what length lands."""
        result = await db.execute(
            text(
                """
                SELECT
                    CASE
                        WHEN p.clip_duration < 20 THEN 'under_20s'
                        WHEN p.clip_duration < 35 THEN '20_to_35s'
                        WHEN p.clip_duration < 50 THEN '35_to_50s'
                        ELSE 'over_50s'
                    END AS bucket,
                    COUNT(*) AS posts,
                    AVG((p.metrics->>'views')::numeric) AS avg_views
                FROM social_posts p
                WHERE p.user_id = :user_id
                  AND p.status = 'published'
                  AND p.clip_duration IS NOT NULL
                  AND (p.metrics->>'views') IS NOT NULL
                GROUP BY 1
                ORDER BY avg_views DESC NULLS LAST
                """
            ),
            {"user_id": user_id},
        )
        return [
            {
                "bucket": row.bucket,
                "posts": int(row.posts or 0),
                "avg_views": _to_float(row.avg_views),
            }
            for row in result.fetchall()
        ]

    @staticmethod
    async def get_top_posts(
        db: AsyncSession, user_id: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            text(
                f"""
                SELECT {_POST_COLUMNS}
                FROM social_posts p
                LEFT JOIN social_accounts a ON a.id = p.social_account_id
                WHERE p.user_id = :user_id
                  AND p.status = 'published'
                  AND (p.metrics->>'views') IS NOT NULL
                ORDER BY (p.metrics->>'views')::numeric DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": limit},
        )
        return [SocialRepository.serialize_post(row) for row in result.fetchall()]


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None
