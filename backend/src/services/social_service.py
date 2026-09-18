"""
Social publishing service.

Owns the OAuth handshake for connecting accounts, publishing clips through a
provider, pulling engagement metrics back, and turning those metrics into the
"performance context" that is fed back into AI clip selection.

Provider HTTP calls are blocking (``requests``) and always run through
``run_in_thread`` so the event loop stays free.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config
from ..repositories.clip_repository import ClipRepository
from ..repositories.social_repository import SocialRepository, TERMINAL_POST_STATUSES
from ..repositories.task_repository import TaskRepository
from ..runtime_settings import decrypt_setting_value, encrypt_setting_value
from ..social import (
    PublishRequest,
    SocialProvider,
    SocialProviderError,
    SUPPORTED_PROVIDERS,
    get_provider,
    list_provider_status,
)
from ..social.base import PRIVACY_LEVELS
from ..utils.async_helpers import run_in_thread
from ..workers.job_queue import JobQueue

logger = logging.getLogger(__name__)

PUBLISH_JOB_NAME = "publish_social_post"
OAUTH_STATE_TTL_SECONDS = 600
MEDIA_TOKEN_TTL_HOURS = 3
PENDING_PUBLISH_TIMEOUT_MINUTES = 45
MAX_HASHTAGS = 30
MAX_TITLE_LENGTH = 255
MAX_CAPTION_LENGTH = 2200
_HASHTAG_RE = re.compile(r"[^0-9A-Za-z_À-￿]+")


class SocialError(Exception):
    """Base class for user-facing social publishing errors."""

    status_code = 400


class SocialNotFound(SocialError):
    status_code = 404


class SocialProviderUnavailable(SocialError):
    status_code = 503


class SocialValidationError(SocialError):
    status_code = 400


class SocialService:
    """Business logic for social connections, posts and the performance loop."""

    def __init__(
        self,
        db: AsyncSession,
        config: Optional[Config] = None,
        queue_adapter=None,
    ):
        self.db = db
        self.config = config or get_config()
        self.queue_adapter = queue_adapter or JobQueue
        self.repo = SocialRepository
        self.clip_repo = ClipRepository
        self.task_repo = TaskRepository

    # ------------------------------------------------------------------
    # Providers & connections
    # ------------------------------------------------------------------
    def list_providers(self) -> List[Dict[str, Any]]:
        return list_provider_status(self.config)

    def _provider(self, name: str) -> SocialProvider:
        normalized = (name or "").strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise SocialNotFound(f"Unknown provider '{name}'")
        provider = get_provider(normalized, self.config)
        if not provider.is_configured:
            raise SocialProviderUnavailable(
                f"{provider.display_name} publishing is not configured on this server."
            )
        return provider

    def redirect_uri_for(self, provider_name: str) -> str:
        return f"{self.config.social_oauth_redirect_base_url}/api/social/callback/{provider_name}"

    async def list_connections(self, user_id: str) -> List[Dict[str, Any]]:
        return await self.repo.list_accounts(self.db, user_id)

    async def start_connection(self, user_id: str, provider_name: str) -> Dict[str, Any]:
        provider = self._provider(provider_name)
        state = secrets.token_urlsafe(32)
        code_verifier = None
        code_challenge = None
        if provider.uses_pkce:
            code_verifier = secrets.token_urlsafe(64)[:128]
            digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
            code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        redirect_uri = self.redirect_uri_for(provider.name)
        await self.repo.create_oauth_state(
            self.db,
            state=state,
            user_id=user_id,
            provider=provider.name,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            ttl_seconds=OAUTH_STATE_TTL_SECONDS,
        )
        authorize_url = provider.build_authorize_url(
            state=state, redirect_uri=redirect_uri, code_challenge=code_challenge
        )
        return {"provider": provider.name, "authorize_url": authorize_url, "state": state}

    async def complete_connection(
        self, user_id: str, provider_name: str, *, code: str, state: str
    ) -> Dict[str, Any]:
        provider = self._provider(provider_name)
        if not code or not state:
            raise SocialValidationError("Missing OAuth code or state")
        state_row = await self.repo.consume_oauth_state(self.db, state)
        if (
            not state_row
            or state_row["user_id"] != user_id
            or state_row["provider"] != provider.name
        ):
            raise SocialValidationError(
                "This sign-in link has expired or does not belong to you. Try connecting again."
            )
        try:
            tokens = await run_in_thread(
                provider.exchange_code,
                code=code,
                redirect_uri=state_row["redirect_uri"],
                code_verifier=state_row.get("code_verifier"),
            )
            profile = await run_in_thread(provider.fetch_profile, tokens.access_token)
        except SocialProviderError as exc:
            raise SocialValidationError(str(exc)) from exc

        account = await self.repo.upsert_account(
            self.db,
            user_id=user_id,
            provider=provider.name,
            external_account_id=profile.external_account_id,
            display_name=profile.display_name,
            username=profile.username,
            avatar_url=profile.avatar_url,
            access_token_encrypted=encrypt_setting_value(tokens.access_token),
            refresh_token_encrypted=(
                encrypt_setting_value(tokens.refresh_token) if tokens.refresh_token else None
            ),
            token_expires_at=tokens.expires_at,
            scopes=tokens.scopes,
            metadata=profile.metadata,
        )
        logger.info(
            "Connected %s account %s for user %s", provider.name, account["id"], user_id
        )
        return account

    async def disconnect(self, user_id: str, account_id: str) -> bool:
        return await self.repo.revoke_account(self.db, user_id, account_id)

    async def _get_valid_access_token(self, account: Dict[str, Any]) -> str:
        """Decrypt the stored token, refreshing it first when it is about to expire."""
        provider = get_provider(account["provider"], self.config)
        access_token = (
            decrypt_setting_value(account["access_token_encrypted"])
            if account.get("access_token_encrypted")
            else ""
        )
        refresh_encrypted = account.get("refresh_token_encrypted")
        refresh_token = decrypt_setting_value(refresh_encrypted) if refresh_encrypted else None
        expires_at = _parse_datetime(account.get("token_expires_at"))

        # Instagram long-lived tokens last 60 days and may only be refreshed
        # once they are older than a day, so refresh well ahead of expiry.
        window = timedelta(days=7) if provider.name == "instagram" else timedelta(minutes=5)
        needs_refresh = bool(
            expires_at and refresh_token and expires_at - datetime.now(timezone.utc) < window
        )
        if not access_token and refresh_token:
            needs_refresh = True
        if not needs_refresh:
            if not access_token:
                raise SocialProviderError(
                    "Stored credentials are missing; reconnect the account.", reauth=True
                )
            return access_token

        try:
            tokens = await run_in_thread(provider.refresh_tokens, refresh_token)
        except SocialProviderError as exc:
            if exc.reauth:
                await self.repo.set_account_status(
                    self.db, account["id"], "reauth_required", str(exc)
                )
            raise
        await self.repo.update_account_tokens(
            self.db,
            account["id"],
            access_token_encrypted=encrypt_setting_value(tokens.access_token),
            refresh_token_encrypted=(
                encrypt_setting_value(tokens.refresh_token) if tokens.refresh_token else None
            ),
            token_expires_at=tokens.expires_at,
        )
        return tokens.access_token

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------
    async def create_post(
        self,
        user_id: str,
        *,
        task_id: str,
        clip_id: str,
        social_account_id: str,
        title: Optional[str],
        caption: Optional[str],
        hashtags: Optional[List[str]],
        privacy_level: Optional[str],
        scheduled_for: Optional[str],
    ) -> Dict[str, Any]:
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task or task.get("user_id") != user_id:
            raise SocialNotFound("Task not found")
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise SocialNotFound("Clip not found")
        clip_path = Path(clip.get("file_path") or "")
        if not clip_path.exists():
            raise SocialValidationError("The clip file is missing on disk; regenerate it first.")

        account = await self.repo.get_user_account(self.db, user_id, social_account_id)
        if not account or account.get("revoked_at"):
            raise SocialNotFound("Connected account not found")
        if account.get("status") == "reauth_required":
            raise SocialValidationError(
                f"Reconnect your {account['provider']} account before publishing."
            )
        provider = self._provider(account["provider"])

        normalized_privacy = (privacy_level or "public").strip().lower()
        if normalized_privacy not in PRIVACY_LEVELS:
            raise SocialValidationError(
                f"privacy_level must be one of: {', '.join(PRIVACY_LEVELS)}"
            )
        if normalized_privacy not in provider.supported_privacy_levels:
            raise SocialValidationError(
                f"{provider.display_name} only supports: "
                f"{', '.join(provider.supported_privacy_levels)}"
            )

        clean_title = (title or clip.get("hook_title") or f"Clip {clip.get('clip_order', '')}").strip()
        clean_title = clean_title[:MAX_TITLE_LENGTH]
        clean_caption = (caption if caption is not None else (clip.get("text") or "")).strip()
        clean_caption = clean_caption[:MAX_CAPTION_LENGTH]
        clean_hashtags = normalize_hashtags(hashtags)

        when = _parse_schedule(scheduled_for)
        now = datetime.now(timezone.utc)
        status = "scheduled" if when and when > now + timedelta(seconds=30) else "queued"

        post = await self.repo.create_post(
            self.db,
            user_id=user_id,
            task_id=task_id,
            clip_id=clip_id,
            social_account_id=account["id"],
            provider=provider.name,
            status=status,
            scheduled_for=when if status == "scheduled" else None,
            title=clean_title,
            caption=clean_caption,
            hashtags=clean_hashtags,
            privacy_level=normalized_privacy,
            media_token=secrets.token_urlsafe(32),
            source_file_path=str(clip_path),
            clip_snapshot=clip,
        )
        if status == "queued":
            await self._enqueue_publish(post["id"])
        logger.info(
            "Created social post %s (%s, %s) for clip %s", post["id"], provider.name, status, clip_id
        )
        return post

    async def _enqueue_publish(self, post_id: str, defer_seconds: int = 0) -> None:
        kwargs: Dict[str, Any] = {}
        if defer_seconds > 0:
            kwargs["_defer_by"] = timedelta(seconds=defer_seconds)
        await self.queue_adapter.enqueue_job(PUBLISH_JOB_NAME, post_id, **kwargs)

    async def list_posts(
        self,
        user_id: str,
        *,
        task_id: Optional[str] = None,
        clip_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return await self.repo.list_posts(
            self.db, user_id, task_id=task_id, clip_id=clip_id, status=status, limit=limit
        )

    async def get_post(self, user_id: str, post_id: str) -> Dict[str, Any]:
        post = await self.repo.get_user_post(self.db, user_id, post_id)
        if not post:
            raise SocialNotFound("Post not found")
        post["metrics_history"] = await self.repo.list_metrics_history(self.db, post_id)
        return post

    async def cancel_post(self, user_id: str, post_id: str) -> Dict[str, Any]:
        post = await self.repo.get_user_post(self.db, user_id, post_id)
        if not post:
            raise SocialNotFound("Post not found")
        if post["status"] not in {"scheduled", "queued"}:
            raise SocialValidationError(
                f"Only scheduled or queued posts can be cancelled (status is {post['status']})."
            )
        await self.repo.update_post(self.db, post_id, status="cancelled")
        return await self.repo.get_post(self.db, post_id) or post

    async def retry_post(self, user_id: str, post_id: str) -> Dict[str, Any]:
        post = await self.repo.get_user_post(self.db, user_id, post_id)
        if not post:
            raise SocialNotFound("Post not found")
        if post["status"] not in {"failed", "cancelled"}:
            raise SocialValidationError("Only failed or cancelled posts can be retried.")
        if not post.get("social_account_id"):
            raise SocialValidationError("The account for this post was disconnected.")
        await self.repo.update_post(
            self.db,
            post_id,
            status="queued",
            error_message=None,
            scheduled_for=None,
            media_token=post.get("media_token") or secrets.token_urlsafe(32),
        )
        await self._enqueue_publish(post_id)
        return await self.repo.get_post(self.db, post_id) or post

    async def delete_post(self, user_id: str, post_id: str) -> bool:
        post = await self.repo.get_user_post(self.db, user_id, post_id)
        if not post:
            return False
        if post["status"] in {"queued", "publishing"}:
            raise SocialValidationError("Wait for the post to finish before deleting it.")
        return await self.repo.delete_post(self.db, user_id, post_id)

    async def get_public_media_path(self, media_token: str) -> Optional[Path]:
        """Resolve a media token to a clip path while the token is still valid."""
        if not media_token or len(media_token) > 64:
            return None
        post = await self.repo.get_post_by_media_token(self.db, media_token)
        if not post:
            return None
        expires_at = _parse_datetime(post.get("media_token_expires_at"))
        if not expires_at or expires_at < datetime.now(timezone.utc):
            return None
        path = Path(post.get("source_file_path") or "")
        if not path.exists():
            return None
        return path

    # ------------------------------------------------------------------
    # Worker entry points
    # ------------------------------------------------------------------
    async def publish_post(self, post_id: str) -> Dict[str, Any]:
        post = await self.repo.get_post(self.db, post_id)
        if not post:
            raise SocialNotFound(f"Post {post_id} not found")
        if post["status"] not in {"queued", "publishing"}:
            logger.info("Skipping publish for post %s in status %s", post_id, post["status"])
            return post
        # An accepted upload must only be polled, including after an explicit
        # retry. Uploading it again can create a duplicate on the platform.
        if post.get("pending_handle"):
            if post["status"] == "queued":
                await self.repo.update_post(self.db, post_id, status="publishing")
                post = await self.repo.get_post(self.db, post_id) or post
            return await self.resolve_pending_post(post)

        account_id = post.get("social_account_id")
        account = (
            await self.repo.get_account_with_tokens(self.db, account_id) if account_id else None
        )
        if not account or account.get("revoked_at"):
            await self.repo.update_post(
                self.db,
                post_id,
                status="failed",
                error_message="The connected account was removed before publishing.",
            )
            return await self.repo.get_post(self.db, post_id) or post

        attempts = int(post.get("attempts") or 0) + 1
        await self.repo.update_post(
            self.db,
            post_id,
            status="publishing",
            attempts=attempts,
            error_message=None,
            media_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=MEDIA_TOKEN_TTL_HOURS),
        )

        try:
            provider = get_provider(post["provider"], self.config)
            file_path = Path(post.get("source_file_path") or "")
            if not file_path.exists():
                raise SocialProviderError("The clip file no longer exists on disk.")
            access_token = await self._get_valid_access_token(account)
            public_url = None
            if post.get("media_token"):
                public_url = (
                    f"{self.config.social_public_media_base_url}/api/social/media/"
                    f"{post['media_token']}"
                )
            request = PublishRequest(
                file_path=str(file_path),
                title=post.get("title") or "",
                caption=post.get("caption") or "",
                hashtags=list(post.get("hashtags") or []),
                privacy_level=post.get("privacy_level") or "public",
                public_media_url=public_url,
                account_metadata=dict(account.get("metadata") or {}),
            )
            result = await run_in_thread(provider.publish, access_token, request)
        except SocialProviderError as exc:
            return await self._handle_publish_failure(post, account, attempts, exc)
        except Exception as exc:  # noqa: BLE001 - surface anything to the user
            logger.exception("Unexpected error publishing post %s", post_id)
            return await self._handle_publish_failure(
                post, account, attempts, SocialProviderError(str(exc), retryable=True)
            )

        if not result.pending_handle:
            await self._mark_published(post, account, result.external_post_id, result.external_url)
        else:
            await self.repo.update_post(
                self.db,
                post_id,
                status="publishing",
                pending_handle=result.pending_handle,
            )
            logger.info(
                "Post %s accepted by %s, awaiting processing (%s)",
                post_id,
                post["provider"],
                result.pending_handle,
            )
        return await self.repo.get_post(self.db, post_id) or post

    async def _mark_published(
        self,
        post: Dict[str, Any],
        account: Dict[str, Any],
        external_post_id: Optional[str],
        external_url: Optional[str],
    ) -> None:
        url = external_url or (
            _fallback_post_url(post["provider"], account, external_post_id)
            if external_post_id else None
        )
        await self.repo.update_post(
            self.db,
            post["id"],
            status="published",
            external_post_id=external_post_id,
            external_url=url,
            pending_handle=None,
            error_message=None,
            published_at=datetime.now(timezone.utc),
            media_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        logger.info("Published post %s to %s: %s", post["id"], post["provider"], url)

    async def _handle_publish_failure(
        self,
        post: Dict[str, Any],
        account: Dict[str, Any],
        attempts: int,
        exc: SocialProviderError,
    ) -> Dict[str, Any]:
        message = str(exc)[:2000]
        if exc.reauth:
            await self.repo.set_account_status(self.db, account["id"], "reauth_required", message)
        can_retry = (
            exc.retryable
            and not exc.reauth
            and attempts < max(1, self.config.social_publish_max_attempts)
        )
        if can_retry:
            delay = min(3600, 300 * attempts)
            await self.repo.update_post(
                self.db, post["id"], status="queued", error_message=message
            )
            await self._enqueue_publish(post["id"], defer_seconds=delay)
            logger.warning(
                "Publish attempt %s for post %s failed, retrying in %ss: %s",
                attempts,
                post["id"],
                delay,
                message,
            )
        else:
            await self.repo.update_post(
                self.db, post["id"], status="failed", error_message=message
            )
            logger.error("Publish failed for post %s: %s", post["id"], message)
        return await self.repo.get_post(self.db, post["id"]) or post

    async def resolve_pending_post(self, post: Dict[str, Any]) -> Dict[str, Any]:
        """Poll a platform that publishes asynchronously (TikTok)."""
        account_id = post.get("social_account_id")
        account = (
            await self.repo.get_account_with_tokens(self.db, account_id) if account_id else None
        )
        handle = post.get("pending_handle")
        if not account or account.get("revoked_at") or not handle:
            await self.repo.update_post(
                self.db,
                post["id"],
                status="failed",
                error_message="Lost track of the pending upload.",
            )
            return await self.repo.get_post(self.db, post["id"]) or post
        # Do not reset updated_at on transient polling failures: it bounds the
        # time spent waiting even if every status request fails.
        updated_at = _parse_datetime(post.get("updated_at")) or datetime.now(timezone.utc)
        if datetime.now(timezone.utc) - updated_at > timedelta(
            minutes=PENDING_PUBLISH_TIMEOUT_MINUTES
        ):
            await self.repo.update_post(
                self.db, post["id"], status="failed",
                error_message="Could not confirm the upload within 45 minutes. Retry to check its status again.",
            )
            return await self.repo.get_post(self.db, post["id"]) or post
        try:
            provider = get_provider(post["provider"], self.config)
            access_token = await self._get_valid_access_token(account)
            result = await run_in_thread(provider.resolve_pending, access_token, handle)
        except SocialProviderError as exc:
            if exc.retryable and not exc.reauth:
                logger.warning("Will check pending post %s again: %s", post["id"], exc)
                return post
            return await self._handle_publish_failure(
                post, account, int(post.get("attempts") or 1), exc
            )
        except requests.RequestException as exc:
            logger.warning("Will check pending post %s after network failure: %s", post["id"], exc)
            return post
        if result is not None:
            # Private TikTok posts may complete without a public post ID.
            await self._mark_published(
                post, account, result.external_post_id, result.external_url
            )
            return await self.repo.get_post(self.db, post["id"]) or post
        return await self.repo.get_post(self.db, post["id"]) or post

    async def resolve_pending_posts(self, limit: int = 100) -> int:
        posts = await self.repo.list_pending_publishes(self.db, limit=limit)
        for post in posts:
            try:
                await self.resolve_pending_post(post)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to resolve pending post %s", post["id"])
        return len(posts)

    async def dispatch_scheduled_posts(self, limit: int = 50) -> List[str]:
        post_ids = await self.repo.claim_due_scheduled_posts(self.db, limit=limit)
        for post_id in post_ids:
            try:
                await self._enqueue_publish(post_id)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to enqueue scheduled post %s", post_id)
                await self.repo.update_post(
                    self.db,
                    post_id,
                    status="failed",
                    error_message="Could not queue the scheduled post.",
                )
        if post_ids:
            logger.info("Dispatched %s scheduled social posts", len(post_ids))
        return post_ids

    async def refresh_post_metrics(
        self, post: Dict[str, Any], *, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if user_id is not None and post.get("user_id") != user_id:
            raise SocialNotFound("Post not found")
        if post["status"] != "published" or not post.get("external_post_id"):
            raise SocialValidationError("Metrics are only available for published posts.")
        account_id = post.get("social_account_id")
        account = (
            await self.repo.get_account_with_tokens(self.db, account_id) if account_id else None
        )
        if not account or account.get("revoked_at"):
            raise SocialValidationError("The account for this post is no longer connected.")
        provider = get_provider(post["provider"], self.config)
        try:
            access_token = await self._get_valid_access_token(account)
            metrics = await run_in_thread(
                provider.fetch_metrics,
                access_token,
                post["external_post_id"],
                dict(account.get("metadata") or {}),
            )
        except SocialProviderError as exc:
            if exc.reauth:
                await self.repo.set_account_status(
                    self.db, account["id"], "reauth_required", str(exc)
                )
            raise SocialValidationError(str(exc)) from exc
        await self.repo.insert_metrics(self.db, post["id"], metrics.as_dict(), metrics.raw)
        refreshed = await self.repo.get_post(self.db, post["id"]) or post
        return refreshed

    async def refresh_due_metrics(self, limit: int = 200) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=max(0.25, self.config.social_metrics_refresh_hours)
        )
        posts = await self.repo.list_posts_for_metrics_refresh(
            self.db,
            refreshed_before=cutoff,
            max_age_days=self.config.social_metrics_max_post_age_days,
            limit=limit,
        )
        refreshed = 0
        for post in posts:
            try:
                await self.refresh_post_metrics(post)
                refreshed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Metrics refresh failed for post %s: %s", post["id"], exc)
        if posts:
            logger.info("Refreshed metrics for %s/%s social posts", refreshed, len(posts))
        return refreshed

    # ------------------------------------------------------------------
    # Performance loop
    # ------------------------------------------------------------------
    async def get_performance_summary(self, user_id: str) -> Dict[str, Any]:
        by_hook = await self.repo.get_performance_by_hook_type(self.db, user_id)
        by_provider = await self.repo.get_performance_by_provider(self.db, user_id)
        by_duration = await self.repo.get_duration_performance(self.db, user_id)
        top_posts = await self.repo.get_top_posts(self.db, user_id, limit=5)
        measured = sum(row["posts"] for row in by_hook)
        total = sum(row["posts"] for row in by_provider)
        min_posts = max(1, self.config.social_performance_min_posts)
        return {
            "total_published": total,
            "measured_posts": measured,
            "by_hook_type": by_hook,
            "by_provider": by_provider,
            "by_duration": by_duration,
            "top_posts": top_posts,
            "min_posts_for_personalization": min_posts,
            "personalization_active": measured >= min_posts,
        }

    async def build_performance_context(self, user_id: str) -> Optional[str]:
        """Compact, prompt-ready summary of what has worked for this creator."""
        by_hook = await self.repo.get_performance_by_hook_type(self.db, user_id)
        measured = sum(row["posts"] for row in by_hook)
        if measured < max(1, self.config.social_performance_min_posts):
            return None
        by_duration = await self.repo.get_duration_performance(self.db, user_id)
        top_posts = await self.repo.get_top_posts(self.db, user_id, limit=3)
        return format_performance_context(by_hook, by_duration, top_posts, measured)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def normalize_hashtags(raw: Optional[List[str]]) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = re.split(r"[\s,]+", raw)
    seen = set()
    result: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = _HASHTAG_RE.sub("", item.strip().lstrip("#"))
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag[:100])
        if len(result) >= MAX_HASHTAGS:
            break
    return result


def _parse_schedule(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = _parse_datetime(value)
    if parsed is None:
        raise SocialValidationError("scheduled_for must be an ISO-8601 timestamp")
    return parsed


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fallback_post_url(
    provider: str, account: Dict[str, Any], external_post_id: str
) -> Optional[str]:
    if provider == "youtube":
        return f"https://www.youtube.com/shorts/{external_post_id}"
    if provider == "tiktok":
        username = account.get("username")
        if username:
            return f"https://www.tiktok.com/@{username}/video/{external_post_id}"
        return None
    if provider == "instagram":
        return None
    return None


def _fmt_count(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{int(round(value)):,}"


def format_performance_context(
    by_hook: List[Dict[str, Any]],
    by_duration: List[Dict[str, Any]],
    top_posts: List[Dict[str, Any]],
    measured: int,
) -> str:
    lines = [f"Based on {measured} published clips with audience metrics."]
    if by_hook:
        lines.append("Performance by hook_type (best first):")
        for row in by_hook:
            rate = row.get("avg_engagement_rate")
            rate_text = f", {rate * 100:.1f}% engagement" if rate is not None else ""
            lines.append(
                f"- {row['hook_type']}: {row['posts']} posts, median {_fmt_count(row.get('median_views'))} views"
                f"{rate_text}"
            )
    if by_duration:
        lines.append("Performance by clip length (best first):")
        for row in by_duration:
            label = row["bucket"].replace("_", " ")
            lines.append(
                f"- {label}: {row['posts']} posts, avg {_fmt_count(row.get('avg_views'))} views"
            )
    if top_posts:
        lines.append("Top performing clips:")
        for post in top_posts:
            title = (post.get("hook_title") or post.get("title") or "untitled").strip()
            views = (post.get("metrics") or {}).get("views")
            lines.append(
                f"- \"{title[:80]}\" (hook_type={post.get('hook_type') or 'none'}, "
                f"{post.get('provider')}, {_fmt_count(views)} views)"
            )
    context = "\n".join(lines)
    return context[:2000]


__all__ = [
    "SocialService",
    "SocialError",
    "SocialNotFound",
    "SocialValidationError",
    "SocialProviderUnavailable",
    "normalize_hashtags",
    "format_performance_context",
    "TERMINAL_POST_STATUSES",
]
