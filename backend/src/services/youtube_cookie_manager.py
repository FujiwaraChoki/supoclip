from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config
from ..database import AsyncSessionLocal
from ..repositories.youtube_auth_repository import YouTubeAuthRepository
from ..youtube_auth_types import (
    AuthFailureClassification,
    ResolvedYouTubeCookieContext,
    YouTubeCookieAccountDTO,
)

logger = logging.getLogger(__name__)


class YouTubeAuthUnavailableError(RuntimeError):
    pass


class YouTubeAuthChallengeError(RuntimeError):
    pass


@dataclass
class BrowserSessionArtifacts:
    storage_state_path: str
    cookiefile_path: str


class YouTubeCookieManager:
    AUTH_FAILURE_PATTERNS = {
        "youtube_auth_challenge": [
            "sign in to confirm you're not a bot",
            "sign in to confirm you’re not a bot",
            "login required",
            "this video is unavailable. sign in",
            "cookies are required",
            "confirm you’re not a bot",
            "confirm you're not a bot",
            "http error 403",
        ],
    }

    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db
        self.config = Config()
        self.repo = YouTubeAuthRepository()
        configured_volume_dir = Path(self.config.youtube_auth_volume_dir)
        try:
            configured_volume_dir.mkdir(parents=True, exist_ok=True)
            self.volume_dir = configured_volume_dir
        except OSError:
            fallback_dir = Path.cwd() / "youtube-auth"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            self.volume_dir = fallback_dir
            logger.info(
                "Falling back to local YouTube auth volume at %s",
                self.volume_dir,
            )

    def _account_dir(self, account_id: str) -> Path:
        path = self.volume_dir / account_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def build_artifact_paths(self, account_id: str) -> BrowserSessionArtifacts:
        account_dir = self._account_dir(account_id)
        return BrowserSessionArtifacts(
            storage_state_path=str(account_dir / "storage_state.json"),
            cookiefile_path=str(account_dir / "cookies.txt"),
        )

    @staticmethod
    def _normalize_cookie_text(cookies_text: str) -> str:
        normalized = (
            (cookies_text or "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .strip()
        )
        if not normalized:
            raise ValueError("Cookies text is required")
        if "# Netscape HTTP Cookie File" not in normalized and "\t" not in normalized:
            raise ValueError(
                "Expected Netscape cookies.txt format from a browser cookie export"
            )
        return normalized + "\n"

    @staticmethod
    def _empty_storage_state() -> str:
        return json.dumps({"cookies": [], "origins": []})

    async def ensure_legacy_cookie_imported(self) -> None:
        if not self.config.youtube_auth_rotation_enabled:
            return
        if not self.config.youtube_cookies_file:
            return

        cookie_path = Path(self.config.youtube_cookies_file)
        if not cookie_path.is_file():
            return

        async with AsyncSessionLocal() as db:
            count = await self.repo.count_accounts(db)
            if count > 0:
                return

            artifacts = self.build_artifact_paths("legacy")
            legacy_cookie_path = Path(artifacts.cookiefile_path)
            legacy_storage_path = Path(artifacts.storage_state_path)
            if not legacy_cookie_path.exists():
                legacy_cookie_path.write_text(cookie_path.read_text())
            if not legacy_storage_path.exists():
                legacy_storage_path.write_text(json.dumps({"cookies": [], "origins": []}))

            account = await self.repo.create_account(
                db,
                label="Legacy cookie file",
                email_hint="legacy",
                status="legacy",
                priority=999,
                playwright_storage_state_path=str(legacy_storage_path),
                yt_dlp_cookiefile_path=str(legacy_cookie_path),
                created_by_user_id=None,
            )
            await self.repo.create_event(
                db,
                account_id=account.id,
                event_type="legacy_import",
                status="success",
                message=f"Imported legacy cookie file from {cookie_path}",
            )

    async def list_accounts(self) -> list[YouTubeCookieAccountDTO]:
        async with AsyncSessionLocal() as db:
            return await self.repo.list_accounts(db)

    async def get_account(self, account_id: str) -> Optional[YouTubeCookieAccountDTO]:
        async with AsyncSessionLocal() as db:
            return await self.repo.get_account(db, account_id)

    async def create_account(
        self, *, label: str, email_hint: Optional[str], priority: int, created_by_user_id: str
    ) -> YouTubeCookieAccountDTO:
        async with AsyncSessionLocal() as db:
            pending = await self.repo.create_account(
                db,
                label=label,
                email_hint=email_hint,
                status="pending_login",
                priority=priority,
                playwright_storage_state_path=None,
                yt_dlp_cookiefile_path=None,
                created_by_user_id=created_by_user_id,
            )
            artifacts = self.build_artifact_paths(pending.id)
            account = await self.repo.update_account(
                db,
                pending.id,
                playwright_storage_state_path=artifacts.storage_state_path,
                yt_dlp_cookiefile_path=artifacts.cookiefile_path,
            )
            await self.repo.create_event(
                db,
                account_id=pending.id,
                event_type="account_created",
                status="success",
                message="Created YouTube auth account",
            )
            if not account:
                raise RuntimeError("Failed to create YouTube auth account")
            return account

    async def update_account(
        self,
        *,
        account_id: str,
        label: Optional[str] = None,
        email_hint: Optional[str] = None,
        priority: Optional[int] = None,
        status: Optional[str] = None,
        action: Optional[str] = None,
    ) -> Optional[YouTubeCookieAccountDTO]:
        async with AsyncSessionLocal() as db:
            current = await self.repo.get_account(db, account_id)
            if not current:
                return None

            updates: dict[str, object] = {}
            if label is not None:
                updates["label"] = label
            if email_hint is not None:
                updates["email_hint"] = email_hint
            if priority is not None:
                updates["priority"] = priority
            if status is not None:
                updates["status"] = status

            if action == "promote_primary":
                updates["priority"] = 0
            elif action == "retire":
                updates["status"] = "disabled"
            elif action == "disable":
                updates["status"] = "disabled"
            elif action == "enable" and current.status == "disabled":
                updates["status"] = "healthy"
            elif action == "refresh_required":
                updates["status"] = "refresh_required"

            account = await self.repo.update_account(db, account_id, **updates)
            if account:
                await self.repo.create_event(
                    db,
                    account_id=account.id,
                    event_type="account_updated",
                    status="success",
                    message=f"Updated account ({action or 'metadata'})",
                    metadata_json=json.dumps(updates) if updates else None,
                )
            return account

    async def list_events(self, account_id: str):
        async with AsyncSessionLocal() as db:
            return await self.repo.list_events(db, account_id)

    async def upload_manual_cookies(
        self, *, account_id: str, cookies_text: str, uploaded_by_user_id: str
    ) -> YouTubeCookieAccountDTO:
        normalized_cookies = self._normalize_cookie_text(cookies_text)

        async with AsyncSessionLocal() as db:
            account = await self.repo.get_account(db, account_id)
            if not account:
                raise ValueError("Account not found")

            artifacts = self.build_artifact_paths(account.id)
            cookiefile_path = Path(artifacts.cookiefile_path)
            cookiefile_path.parent.mkdir(parents=True, exist_ok=True)
            cookiefile_path.write_text(normalized_cookies)

            storage_state_path = Path(artifacts.storage_state_path)
            if not storage_state_path.exists():
                storage_state_path.write_text(self._empty_storage_state())

            updated = await self.repo.update_account(
                db,
                account.id,
                status="healthy",
                consecutive_auth_failures=0,
                cooldown_until=None,
                last_error_code=None,
                last_error_message=None,
                last_refresh_completed_at=datetime.now(timezone.utc),
                playwright_storage_state_path=artifacts.storage_state_path,
                yt_dlp_cookiefile_path=artifacts.cookiefile_path,
            )
            await self.repo.create_event(
                db,
                account_id=account.id,
                event_type="manual_cookie_upload",
                status="success",
                message="Uploaded cookies.txt manually from admin",
                metadata_json=json.dumps({"uploaded_by_user_id": uploaded_by_user_id}),
            )

            if not updated:
                raise RuntimeError("Failed to update YouTube auth account")

        return updated

    async def get_manual_cookies_text(self, account_id: str) -> Optional[str]:
        async with AsyncSessionLocal() as db:
            account = await self.repo.get_account(db, account_id)
            if not account:
                raise ValueError("Account not found")

        cookiefile_path = account.yt_dlp_cookiefile_path
        if not cookiefile_path:
            return None

        path = Path(cookiefile_path)
        if not path.is_file():
            return None

        return path.read_text()

    async def acquire_download_contexts(self) -> list[ResolvedYouTubeCookieContext]:
        await self.ensure_legacy_cookie_imported()

        if not self.config.youtube_auth_rotation_enabled:
            return self._legacy_fallback_contexts()

        async with AsyncSessionLocal() as db:
            accounts = await self.repo.list_candidate_accounts(db)

        contexts = [
            ResolvedYouTubeCookieContext(
                account_id=account.id,
                label=account.label,
                status=account.status,
                cookiefile_path=account.yt_dlp_cookiefile_path,
                storage_state_path=account.playwright_storage_state_path,
                is_legacy_fallback=account.status == "legacy",
            )
            for account in accounts
            if account.yt_dlp_cookiefile_path
            and Path(account.yt_dlp_cookiefile_path).is_file()
        ]
        if contexts:
            return contexts
        return self._legacy_fallback_contexts()

    async def mark_account_success(
        self, account_id: Optional[str], *, task_id: Optional[str] = None
    ) -> None:
        if not account_id:
            return
        async with AsyncSessionLocal() as db:
            await self.repo.update_account(
                db,
                account_id,
                status="healthy",
                last_used_at=datetime.now(timezone.utc),
                last_verified_at=datetime.now(timezone.utc),
                consecutive_auth_failures=0,
                cooldown_until=None,
                last_error_code=None,
                last_error_message=None,
            )
            await self.repo.create_event(
                db,
                account_id=account_id,
                event_type="download_success",
                status="success",
                task_id=task_id,
                message="YouTube auth succeeded",
            )

    async def mark_account_auth_failure(
        self,
        account_id: Optional[str],
        *,
        classification: AuthFailureClassification,
        task_id: Optional[str] = None,
    ) -> None:
        if not account_id:
            return
        async with AsyncSessionLocal() as db:
            account = await self.repo.get_account(db, account_id)
            if not account:
                return

            next_count = int(account.consecutive_auth_failures or 0) + 1
            next_status = "cooldown"
            cooldown_until = datetime.now(timezone.utc) + timedelta(
                minutes=self.config.youtube_auth_cooldown_minutes
            )
            if next_count >= self.config.youtube_auth_failure_threshold:
                next_status = "refresh_required"
                cooldown_until = None

            await self.repo.update_account(
                db,
                account_id,
                status=next_status,
                consecutive_auth_failures=next_count,
                cooldown_until=cooldown_until,
                last_error_code=classification.code,
                last_error_message=classification.message[:1000],
            )
            await self.repo.create_event(
                db,
                account_id=account_id,
                event_type="auth_failure",
                status=next_status,
                task_id=task_id,
                message=classification.message[:1000],
                metadata_json=json.dumps({"code": classification.code}),
            )

    async def mark_account_transient_failure(
        self, account_id: Optional[str], message: str, *, task_id: Optional[str] = None
    ) -> None:
        if not account_id:
            return
        async with AsyncSessionLocal() as db:
            await self.repo.update_account(
                db,
                account_id,
                last_error_code="transient_error",
                last_error_message=message[:1000],
            )
            await self.repo.create_event(
                db,
                account_id=account_id,
                event_type="transient_failure",
                status="warning",
                task_id=task_id,
                message=message[:1000],
            )

    async def recheck_cooldown_accounts(self) -> int:
        async with AsyncSessionLocal() as db:
            accounts = await self.repo.list_accounts(db)
            recovered = 0
            now = datetime.now(timezone.utc)
            for account in accounts:
                if account.status == "cooldown" and account.cooldown_until and account.cooldown_until <= now:
                    await self.repo.update_account(
                        db,
                        account.id,
                        status="healthy",
                        cooldown_until=None,
                    )
                    await self.repo.create_event(
                        db,
                        account_id=account.id,
                        event_type="cooldown_recovered",
                        status="success",
                        message="Cooldown window elapsed; account returned to healthy",
                    )
                    recovered += 1
            return recovered

    async def verify_all_accounts(self) -> dict[str, str]:
        results: dict[str, str] = {}
        async with AsyncSessionLocal() as db:
            accounts = await self.repo.list_accounts(db)

        for account in accounts:
            if account.status == "disabled":
                continue
            try:
                verified = await self.verify_account(account.id)
                results[account.id] = "healthy" if verified else "failed"
            except Exception as exc:
                results[account.id] = f"error:{exc}"
        return results

    async def verify_account(self, account_id: str) -> bool:
        async with AsyncSessionLocal() as db:
            account = await self.repo.get_account(db, account_id)
        if not account or not account.yt_dlp_cookiefile_path:
            return False

        from ..youtube_utils import fetch_video_info_with_cookie_context

        context = ResolvedYouTubeCookieContext(
            account_id=account.id,
            label=account.label,
            status=account.status,
            cookiefile_path=account.yt_dlp_cookiefile_path,
            storage_state_path=account.playwright_storage_state_path,
            is_legacy_fallback=account.status == "legacy",
        )
        try:
            info = await asyncio.to_thread(
                fetch_video_info_with_cookie_context,
                self.config.youtube_auth_verify_url,
                context,
            )
        except Exception as exc:
            classification = self.classify_error(str(exc))
            if classification.is_auth_failure:
                await self.mark_account_auth_failure(
                    account.id,
                    classification=classification,
                )
            else:
                await self.mark_account_transient_failure(
                    account.id,
                    str(exc),
                )
            return False
        if info:
            await self.mark_account_success(account.id)
            return True

        classification = self.classify_error("Verification failed")
        await self.mark_account_auth_failure(
            account.id,
            classification=classification,
        )
        return False

    def verify_account_sync(self, account_id: str) -> bool:
        return asyncio.run(self.verify_account(account_id))

    def classify_error(self, message: str) -> AuthFailureClassification:
        normalized = (message or "").lower()
        for code, patterns in self.AUTH_FAILURE_PATTERNS.items():
            if any(pattern in normalized for pattern in patterns):
                return AuthFailureClassification(
                    is_auth_failure=True,
                    code=code,
                    message=message,
                )
        return AuthFailureClassification(
            is_auth_failure=False,
            code="transient_error",
            message=message,
        )

    def get_download_contexts_sync(self) -> list[ResolvedYouTubeCookieContext]:
        return asyncio.run(self.acquire_download_contexts())

    def mark_success_sync(
        self, account_id: Optional[str], *, task_id: Optional[str] = None
    ) -> None:
        asyncio.run(self.mark_account_success(account_id, task_id=task_id))

    def mark_auth_failure_sync(
        self,
        account_id: Optional[str],
        classification: AuthFailureClassification,
        *,
        task_id: Optional[str] = None,
    ) -> None:
        asyncio.run(
            self.mark_account_auth_failure(
                account_id, classification=classification, task_id=task_id
            )
        )

    def mark_transient_failure_sync(
        self, account_id: Optional[str], message: str, *, task_id: Optional[str] = None
    ) -> None:
        asyncio.run(
            self.mark_account_transient_failure(
                account_id, message=message, task_id=task_id
            )
        )

    def _legacy_fallback_contexts(self) -> list[ResolvedYouTubeCookieContext]:
        if (
            self.config.youtube_cookies_file
            and Path(self.config.youtube_cookies_file).is_file()
        ):
            return [
                ResolvedYouTubeCookieContext(
                    account_id=None,
                    label="legacy-env-cookiefile",
                    status="legacy",
                    cookiefile_path=self.config.youtube_cookies_file,
                    storage_state_path=None,
                    is_legacy_fallback=True,
                )
            ]
        return []
