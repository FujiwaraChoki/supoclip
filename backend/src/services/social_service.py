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
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config
from .billing_service import BillingService
from ..media_storage import STORAGE_ERRORS, MediaStorage, is_s3, media_work
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
from ..social.base import OUTCOME_UNKNOWN_MESSAGE, PRIVACY_LEVELS, redact, redacted_errors
from ..utils.async_helpers import run_in_thread
from ..workers.job_queue import DuplicateJobError, JobQueue

logger = logging.getLogger(__name__)

PUBLISH_JOB_NAME = "publish_social_post"
OAUTH_STATE_TTL_SECONDS = 600
MEDIA_TOKEN_TTL_HOURS = 3
# Instagram fetches the video while it processes the container (polled for up
# to 10 minutes), so presigned S3 URLs outlive one publish attempt comfortably.
PUBLIC_MEDIA_URL_TTL_SECONDS = MEDIA_TOKEN_TTL_HOURS * 3600
PENDING_PUBLISH_TIMEOUT_MINUTES = 45
# arq job timeout for one publish attempt (see WorkerSettings).
PUBLISH_JOB_TIMEOUT_SECONDS = 3600
# A post still ``publishing`` without an upload handle this long after its
# claim lost its worker (crash, deploy, timeout). Comfortably above the job
# timeout so a live upload is never swept.
STALE_PUBLISHING_SECONDS = 2 * PUBLISH_JOB_TIMEOUT_SECONDS
MAX_HASHTAGS = 30
# The views chart only appears once there are this many days of measurements.
VIEWS_CHART_MIN_DAYS = 3
# OAuth states started from the native app carry this prefix, so the web
# callback hands the code back to the app instead of finishing it in a browser
# session the app's web sheet doesn't have.
MOBILE_STATE_PREFIX = "m."
MOBILE_CLIENTS = frozenset({"ios"})
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


class SocialPlanRequired(SocialError):
    status_code = 402


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
        self.storage = MediaStorage(self.config)

    # ------------------------------------------------------------------
    # Providers & connections
    # ------------------------------------------------------------------
    def list_providers(self) -> List[Dict[str, Any]]:
        return list_provider_status(self.config)

    async def has_access(self, user_id: str) -> bool:
        """Whether this user may use social publishing under the rollout setting."""
        access = self.config.social_publishing_access
        if access == "all":
            return True
        if access == "off":
            return False
        row = (
            await self.db.execute(
                text("SELECT is_admin, email FROM users WHERE id = :user_id"),
                {"user_id": user_id},
            )
        ).fetchone()
        if not row:
            return False
        email = (row.email or "").strip().lower()
        return bool(row.is_admin) or email in self.config.social_publishing_allowed_emails

    async def can_schedule(self, user_id: str) -> bool:
        """Plan entitlement, plus admin testing during the restricted rollout."""
        if self.config.social_publishing_access == "admins":
            row = (
                await self.db.execute(
                    text("SELECT is_admin FROM users WHERE id = :user_id"),
                    {"user_id": user_id},
                )
            ).fetchone()
            if row and row.is_admin:
                return True
        summary = await BillingService(self.db, self.config).get_usage_summary(user_id)
        return bool(summary.get("can_schedule_posts"))

    def _provider(self, name: str) -> SocialProvider:
        normalized = (name or "").strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise SocialNotFound(f"Unknown provider '{name}'")
        if normalized not in self.config.social_publishing_providers:
            raise SocialProviderUnavailable(
                f"{normalized.capitalize()} publishing is disabled on this server."
            )
        provider = get_provider(normalized, self.config)
        if not provider.is_configured:
            raise SocialProviderUnavailable(
                f"{provider.display_name} publishing is not configured on this server."
            )
        return provider

    @staticmethod
    async def _call_provider(func, *args, **kwargs):
        """Run a blocking provider call; any failure comes back redacted."""
        return await run_in_thread(redacted_errors(func), *args, **kwargs)

    def redirect_uri_for(self, provider_name: str) -> str:
        return f"{self.config.social_oauth_redirect_base_url}/api/social/callback/{provider_name}"

    async def list_connections(self, user_id: str) -> List[Dict[str, Any]]:
        return await self.repo.list_accounts(self.db, user_id)

    async def start_connection(
        self, user_id: str, provider_name: str, *, client: Optional[str] = None
    ) -> Dict[str, Any]:
        provider = self._provider(provider_name)
        state = secrets.token_urlsafe(32)
        if client in MOBILE_CLIENTS:
            state = MOBILE_STATE_PREFIX + state
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
            tokens = await self._call_provider(
                provider.exchange_code,
                code=code,
                redirect_uri=state_row["redirect_uri"],
                code_verifier=state_row.get("code_verifier"),
            )
            profile = await self._call_provider(provider.fetch_profile, tokens.access_token)
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
        account = await self.repo.get_account_with_tokens(self.db, account_id)
        # Clear local credentials first: disconnecting must work even when the
        # platform is unreachable.
        removed = await self.repo.revoke_account(self.db, user_id, account_id)
        if removed and account and account.get("user_id") == user_id:
            await self._revoke_at_provider(
                account["provider"],
                account["id"],
                _decrypt_or_none(account.get("access_token_encrypted")),
                _decrypt_or_none(account.get("refresh_token_encrypted")),
            )
        return removed

    async def disconnect_all(self, user_id: str) -> int:
        """Disconnect every account of a user (account deletion)."""
        accounts = await self.repo.list_accounts(self.db, user_id)
        removed = 0
        for account in accounts:
            removed += int(await self.disconnect(user_id, account["id"]))
        return removed

    async def _revoke_at_provider(
        self,
        provider_name: str,
        account_id: str,
        access_token: Optional[str],
        refresh_token: Optional[str],
    ) -> None:
        """Best-effort revocation of a grant; failures are only logged."""
        if not access_token and not refresh_token:
            return
        try:
            provider = get_provider(provider_name, self.config)
            if not provider.is_configured:
                return
            await self._call_provider(provider.revoke_tokens, access_token, refresh_token)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not revoke %s tokens for social account %s (%s): %s",
                provider_name,
                account_id,
                type(exc).__name__,
                redact(exc),
            )

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
            tokens = await self._call_provider(provider.refresh_tokens, refresh_token)
        except SocialProviderError as exc:
            if exc.reauth:
                await self.repo.set_account_status(
                    self.db, account["id"], "reauth_required", str(exc)
                )
            raise
        stored = await self.repo.update_account_tokens(
            self.db,
            account["id"],
            access_token_encrypted=encrypt_setting_value(tokens.access_token),
            refresh_token_encrypted=(
                encrypt_setting_value(tokens.refresh_token) if tokens.refresh_token else None
            ),
            token_expires_at=tokens.expires_at,
        )
        if not stored:
            # Disconnected while refreshing: the fresh grant must not outlive it.
            await self._revoke_at_provider(
                provider.name, account["id"], tokens.access_token, tokens.refresh_token
            )
            raise SocialProviderError("This account was disconnected. Reconnect it to publish.")
        return tokens.access_token

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------
    async def generate_caption(
        self, user_id: str, *, task_id: str, clip_id: str, provider: str
    ) -> Dict[str, str]:
        from .social_caption_service import generate_social_caption

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task or task.get("user_id") != user_id:
            raise SocialNotFound("Task not found")
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise SocialNotFound("Clip not found")
        if provider not in SUPPORTED_PROVIDERS:
            raise SocialValidationError("Unsupported social platform")
        if not (clip.get("text") or "").strip():
            raise SocialValidationError("This clip has no transcript to draft a caption from.")
        if not self.config.anthropic_api_key:
            raise SocialProviderUnavailable("Caption generation requires ANTHROPIC_API_KEY on the server.")
        try:
            caption = await generate_social_caption(self.config.anthropic_api_key, clip, provider)
        except Exception as exc:
            logger.warning("Social caption generation failed (%s)", type(exc).__name__)
            raise SocialProviderUnavailable(
                "Couldn't generate a caption. Try again or write your own."
            ) from exc
        return {"caption": caption}

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
        clip_file = clip.get("file_path") or ""
        try:
            clip_exists = await self._media_exists(clip_file)
        except STORAGE_ERRORS as exc:
            raise SocialProviderUnavailable(
                "Clip storage is temporarily unavailable. Try again shortly."
            ) from exc
        if not clip_exists:
            raise SocialValidationError("The clip file is missing; regenerate it first.")

        account = await self.repo.get_user_account(self.db, user_id, social_account_id)
        if not account or account.get("revoked_at"):
            raise SocialNotFound("Connected account not found")
        if account.get("status") == "reauth_required":
            raise SocialValidationError(
                f"Reconnect your {account['provider']} account before publishing."
            )
        provider = self._provider(account["provider"])

        normalized_privacy = (privacy_level or provider.default_privacy_level).strip().lower()
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
        if status == "scheduled" and not await self.can_schedule(user_id):
            raise SocialPlanRequired(
                "Scheduling posts is available on the Creator plan. "
                "You can still publish right away."
            )

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
            source_file_path=clip_file,
            clip_snapshot=clip,
        )
        if status == "queued":
            await self._enqueue_or_fail(post["id"], int(post.get("attempts") or 0))
            post = await self.repo.get_post(self.db, post["id"]) or post
        logger.info(
            "Created social post %s (%s, %s) for clip %s", post["id"], provider.name, status, clip_id
        )
        return post

    async def _enqueue_publish(
        self, post_id: str, attempts: int, defer_seconds: int = 0
    ) -> None:
        # One job per (post, attempt): a double enqueue of the same queued
        # state collapses into one job. The claim in ``publish_post`` is what
        # actually guarantees a single upload.
        kwargs: Dict[str, Any] = {"_job_id": f"social:{post_id}:{attempts}"}
        if defer_seconds > 0:
            kwargs["_defer_by"] = timedelta(seconds=defer_seconds)
        try:
            await self.queue_adapter.enqueue_job(PUBLISH_JOB_NAME, post_id, **kwargs)
        except DuplicateJobError:
            # That job is still waiting and will claim the post itself.
            logger.info("Publish job for post %s attempt %s is already queued", post_id, attempts)

    async def _enqueue_or_fail(self, post_id: str, attempts: int) -> None:
        """Enqueue a freshly queued post; never leave it queued without a job."""
        try:
            await self._enqueue_publish(post_id, attempts)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to enqueue social post %s", post_id)
            await self.repo.update_post(
                self.db,
                post_id,
                status="failed",
                error_message="Could not queue the post. Try again.",
            )

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
        # Conditional update: a worker claim or a second click can't race it.
        if not await self.repo.cancel_post(self.db, user_id, post_id):
            post = await self.repo.get_user_post(self.db, user_id, post_id)
            if not post:
                raise SocialNotFound("Post not found")
            raise SocialValidationError(
                f"Only scheduled or queued posts can be cancelled (status is {post['status']})."
            )
        post = await self.repo.get_post(self.db, post_id)
        assert post is not None
        return post

    async def retry_post(self, user_id: str, post_id: str) -> Dict[str, Any]:
        post = await self.repo.get_user_post(self.db, user_id, post_id)
        if not post:
            raise SocialNotFound("Post not found")
        if post["status"] not in {"failed", "cancelled"}:
            raise SocialValidationError("Only failed or cancelled posts can be retried.")
        if not post.get("social_account_id"):
            raise SocialValidationError("The account for this post was disconnected.")
        self._provider(post["provider"])
        attempts = await self.repo.requeue_post(
            self.db, user_id, post_id, media_token=secrets.token_urlsafe(32)
        )
        if attempts is None:
            # A concurrent retry (double click) already queued it.
            raise SocialValidationError("This post is already being retried.")
        await self._enqueue_or_fail(post_id, attempts)
        return await self.repo.get_post(self.db, post_id) or post

    async def delete_post(self, user_id: str, post_id: str) -> bool:
        post = await self.repo.get_user_post(self.db, user_id, post_id)
        if not post:
            return False
        if post["status"] in {"queued", "publishing"} or not await self.repo.delete_post(
            self.db, user_id, post_id
        ):
            raise SocialValidationError("Wait for the post to finish before deleting it.")
        return True

    async def _media_exists(self, reference: str) -> bool:
        if not reference:
            return False
        if is_s3(reference):
            return await run_in_thread(self.storage.exists, reference)
        return Path(reference).exists()

    async def get_public_media_reference(self, media_token: str) -> Optional[str]:
        """
        Resolve a media token to the file being published while it is valid.

        ``source_file_path`` is the clip render the worker claimed, so the
        platform fetches exactly what the publish attempt resolved. Returns an
        ``s3://`` reference or an existing local path.
        """
        if not media_token or len(media_token) > 64:
            return None
        post = await self.repo.get_post_by_media_token(self.db, media_token)
        if not post:
            return None
        expires_at = _parse_datetime(post.get("media_token_expires_at"))
        if not expires_at or expires_at < datetime.now(timezone.utc):
            return None
        reference = post.get("source_file_path") or ""
        if is_s3(reference):
            return reference
        if not reference or not Path(reference).exists():
            return None
        return reference

    def public_media_redirect_url(self, reference: str) -> str:
        return self.storage.signed_url(
            reference, Path(reference).name, expires_in=PUBLIC_MEDIA_URL_TTL_SECONDS
        )

    async def _resolve_publish_media(
        self, post: Dict[str, Any], provider: SocialProvider
    ) -> tuple[str, Optional[str]]:
        """
        Return ``(local_file, public_url)`` for the claimed clip render.

        S3 renders are downloaded into a leased local copy (held until the
        surrounding ``media_work`` scope ends) only for platforms that take an
        upload. Instagram pulls a URL instead: a presigned S3 URL, or the
        token-gated media route for local storage.
        """
        reference = post.get("source_file_path") or ""
        if not post.get("clip_id") or not reference:
            raise SocialProviderError("The clip for this post was deleted.")
        missing = SocialProviderError("The clip file no longer exists; regenerate it first.")
        try:
            if is_s3(reference):
                if provider.needs_local_file:
                    local = await run_in_thread(self.storage.local_path, reference)
                    return str(local), None
                if not await run_in_thread(self.storage.exists, reference):
                    raise missing
                url = await run_in_thread(self.public_media_redirect_url, reference)
                return reference, url
        except SocialProviderError:
            raise
        except STORAGE_ERRORS as exc:
            code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise missing from exc
            raise SocialProviderError(
                f"Clip storage is temporarily unavailable ({type(exc).__name__}).",
                retryable=True,
            ) from exc
        if not Path(reference).exists():
            raise missing
        public_url = None
        if post.get("media_token"):
            public_url = (
                f"{self.config.social_public_media_base_url}/api/social/media/"
                f"{post['media_token']}"
            )
        return reference, public_url

    # ------------------------------------------------------------------
    # Worker entry points
    # ------------------------------------------------------------------
    @media_work
    async def publish_post(self, post_id: str) -> Dict[str, Any]:
        post = await self.repo.get_post(self.db, post_id)
        if not post:
            raise SocialNotFound(f"Post {post_id} not found")
        if post["status"] == "publishing" and post.get("pending_handle"):
            # The platform accepted the upload and processes it asynchronously.
            return await self.resolve_pending_post(post)
        if post["status"] != "queued":
            # ``publishing`` without a handle belongs to another worker, or its
            # outcome is unknown; uploading again could duplicate the post.
            logger.info("Skipping publish for post %s in status %s", post_id, post["status"])
            return post

        claimed = await self.repo.claim_post(
            self.db,
            post_id,
            media_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=MEDIA_TOKEN_TTL_HOURS),
        )
        if not claimed:
            logger.info("Post %s was claimed or left the queue; not uploading", post_id)
            return await self.repo.get_post(self.db, post_id) or post
        post = claimed
        attempts = int(post.get("attempts") or 1)

        # An accepted upload must only be polled, including after an explicit
        # retry. Uploading it again can create a duplicate on the platform.
        if post.get("pending_handle"):
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

        # Recheck the current rollout after claiming, before refreshing tokens
        # or sending any media. Jobs queued before a platform/user was disabled
        # must not publish through an old connection. Already accepted uploads
        # above can still have their status resolved without uploading again.
        try:
            provider = self._provider(post["provider"])
            if not await self.has_access(post["user_id"]):
                raise SocialProviderUnavailable(
                    "Social publishing isn't available on your account yet."
                )
        except SocialProviderUnavailable as exc:
            await self.repo.update_post(
                self.db, post_id, status="failed", error_message=str(exc),
                media_token_expires_at=None,
            )
            return await self.repo.get_post(self.db, post_id) or post

        try:
            file_path, public_url = await self._resolve_publish_media(post, provider)
            access_token = await self._get_valid_access_token(account)
            request = PublishRequest(
                file_path=file_path,
                title=post.get("title") or "",
                caption=post.get("caption") or "",
                hashtags=list(post.get("hashtags") or []),
                privacy_level=post.get("privacy_level") or "public",
                public_media_url=public_url,
                account_metadata=dict(account.get("metadata") or {}),
            )
            result = await self._call_provider(provider.publish, access_token, request)
        except SocialProviderError as exc:
            return await self._handle_publish_failure(post, account, attempts, exc)
        except Exception as exc:  # noqa: BLE001 - surface anything to the user
            # No traceback: provider exceptions can carry request URLs.
            logger.error(
                "Unexpected error publishing post %s (%s): %s",
                post_id,
                type(exc).__name__,
                redact(exc),
            )
            return await self._handle_publish_failure(
                post,
                account,
                attempts,
                SocialProviderError(f"Unexpected error ({type(exc).__name__}): {exc}", retryable=True),
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
        # A definitive platform failure kills the pending upload, so an explicit
        # retry uploads again. Otherwise the handle (if any) is kept.
        handle_fields: Dict[str, Any] = {"pending_handle": None} if exc.rejected else {}
        # An unknown outcome may already be live on the platform: only the
        # user, after checking their account, may upload it again.
        can_retry = (
            exc.retryable
            and not exc.reauth
            and not exc.outcome_unknown
            and attempts < max(1, self.config.social_publish_max_attempts)
        )
        if can_retry:
            delay = min(3600, 300 * attempts)
            await self.repo.update_post(
                self.db, post["id"], status="queued", error_message=message, **handle_fields
            )
            await self._enqueue_publish(post["id"], attempts, defer_seconds=delay)
            logger.warning(
                "Publish attempt %s for post %s failed, retrying in %ss: %s",
                attempts,
                post["id"],
                delay,
                message,
            )
        else:
            await self.repo.update_post(
                self.db, post["id"], status="failed", error_message=message, **handle_fields
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
            result = await self._call_provider(provider.resolve_pending, access_token, handle)
        except SocialProviderError as exc:
            if exc.retryable and not exc.reauth and not exc.rejected:
                logger.warning("Will check pending post %s again: %s", post["id"], exc)
                return post
            return await self._handle_publish_failure(
                post, account, int(post.get("attempts") or 1), exc
            )
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
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to resolve pending post %s (%s): %s",
                    post["id"],
                    type(exc).__name__,
                    redact(exc),
                )
        return len(posts)

    async def sweep_stale_publishing(self) -> List[str]:
        """Fail posts whose worker vanished mid-upload (never re-uploaded)."""
        post_ids = await self.repo.fail_stale_publishing(
            self.db,
            older_than_seconds=STALE_PUBLISHING_SECONDS,
            message=OUTCOME_UNKNOWN_MESSAGE,
        )
        for post_id in post_ids:
            logger.warning("Social post %s lost its worker mid-upload; marked failed", post_id)
        return post_ids

    async def dispatch_scheduled_posts(self, limit: int = 50) -> List[str]:
        post_ids = await self.repo.claim_due_scheduled_posts(self.db, limit=limit)
        for post_id in post_ids:
            try:
                # Scheduled posts have never been claimed (attempt 0).
                await self._enqueue_publish(post_id, 0)
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
            metrics = await self._call_provider(
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
        share_url = (metrics.raw or {}).get("share_url")
        if not post.get("external_url") and isinstance(share_url, str) and share_url.startswith("https://"):
            # TikTok post links need the creator's handle, which we can't read;
            # the video query returns a canonical share link instead.
            await self.repo.update_post(self.db, post["id"], external_url=share_url)
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
                logger.warning(
                    "Metrics refresh failed for post %s (%s): %s",
                    post["id"],
                    type(exc).__name__,
                    redact(exc),
                )
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

    async def get_views_timeseries(self, user_id: str, days: int = 30) -> Dict[str, Any]:
        """Daily new views for the analytics chart, with every day in the window present."""
        days = max(1, min(int(days), 365))
        rows = await self.repo.get_daily_new_views(self.db, user_id, days=days)
        today = datetime.now(timezone.utc).date()
        window = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
        by_day: Dict[Any, Dict[str, int]] = {day: {} for day in window}
        for row in rows:
            if row["day"] in by_day:
                by_day[row["day"]][row["provider"]] = row["views"]
        return {
            "days": [
                {
                    "date": day.isoformat(),
                    "views": sum(providers.values()),
                    "by_provider": providers,
                }
                for day, providers in by_day.items()
            ],
            "measured_days": len({row["day"] for row in rows if row["day"] in by_day}),
            "min_days": VIEWS_CHART_MIN_DAYS,
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
def _decrypt_or_none(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return decrypt_setting_value(value) or None
    except Exception:  # noqa: BLE001 - e.g. a rotated encryption key
        return None


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


def _prompt_safe(value: Any) -> str:
    """User-editable text for the AI prompt: one line, no control characters."""
    text = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in str(value or "")
    )
    return " ".join(text.split())


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
            title = _prompt_safe(post.get("hook_title") or post.get("title")) or "untitled"
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
