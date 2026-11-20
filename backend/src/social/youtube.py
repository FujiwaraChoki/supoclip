"""
YouTube Shorts publishing via the YouTube Data API v3.

OAuth: Google OAuth 2.0 (offline access so we receive a refresh token).
Upload: resumable upload to ``videos.insert``.
Metrics: ``videos.list?part=statistics``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Set
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..config import Config, get_config
from .base import (
    DEFAULT_HTTP_TIMEOUT,
    AccountProfile,
    OAuthTokens,
    PostMetrics,
    PublishRequest,
    PublishResult,
    SocialProvider,
    SocialProviderError,
    ambiguous_outcome,
    build_caption,
)

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

# YouTube's per-project quotas (including "Video Uploads per day") reset at
# midnight Pacific time.
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")
QUOTA_HOLD_REASON = "youtube_quota"
QUOTA_HOLD_MESSAGE = (
    "YouTube's daily upload limit is reached. This clip will publish automatically "
    "after the limit resets at midnight Pacific time."
)
QUOTA_GAVE_UP_MESSAGE = (
    "YouTube's daily upload limit stayed reached for several days, so this clip "
    "wasn't published. Try again to publish it."
)
# Shown for posts that failed on the quota before such posts were held.
QUOTA_FAILED_MESSAGE = (
    "YouTube's daily upload limit was reached. It resets at midnight Pacific time; "
    "try again after that."
)
CHANNEL_LIMIT_MESSAGE = (
    "Your YouTube channel has reached the upload limit YouTube sets for each channel. "
    "This limit belongs to your channel, not SupoClip. Try again later or tomorrow."
)
# ``quotaExceeded`` is the project's daily unit budget. Per-minute limits are
# not daily and keep the normal short retries.
_DAILY_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
_CHANNEL_LIMIT_REASONS = {"uploadLimitExceeded"}
_CHANNEL_LIMIT_TEXT = "exceeded the number of videos they may upload"


def quota_reset_after(now: Optional[datetime] = None) -> datetime:
    """The next midnight in Pacific time after ``now``, in UTC."""
    local = (now or datetime.now(timezone.utc)).astimezone(QUOTA_TIMEZONE)
    reset = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=QUOTA_TIMEZONE)
    return reset.astimezone(timezone.utc)


def is_daily_quota_error(message: str, reasons: Set[str]) -> bool:
    if reasons & _DAILY_QUOTA_REASONS:
        return True
    lowered = (message or "").lower()
    return "quota exceeded for quota metric" in lowered and "per day" in lowered


def is_channel_limit_error(message: str, reasons: Set[str]) -> bool:
    return bool(reasons & _CHANNEL_LIMIT_REASONS) or _CHANNEL_LIMIT_TEXT in (message or "").lower()


def _error_reasons(response) -> Set[str]:
    try:
        payload = response.json()
    except ValueError:
        return set()
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return set()
    reasons = set()
    for item in list(error.get("errors") or []) + list(error.get("details") or []):
        if isinstance(item, dict) and item.get("reason"):
            reasons.add(str(item["reason"]))
    return reasons

SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)

PRIVACY_MAP = {
    "public": "public",
    "unlisted": "unlisted",
    "private": "private",
}

TITLE_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 5000


class YouTubeProvider(SocialProvider):
    name = "youtube"
    display_name = "YouTube Shorts"

    def __init__(self, config: Optional[Config] = None, session=None):
        super().__init__(session)
        self.config = config or get_config()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.youtube_oauth_client_id and self.config.youtube_oauth_client_secret
        )

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(
        self, *, state: str, redirect_uri: str, code_challenge: Optional[str] = None
    ) -> str:
        params = {
            "client_id": self.config.youtube_oauth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        response = self.http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": self.config.youtube_oauth_client_id,
                "client_secret": self.config.youtube_oauth_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if response.status_code >= 400:
            raise SocialProviderError(
                f"YouTube token exchange failed: {self._extract_error(response)}"
            )
        return self._parse_tokens(response.json())

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        response = self.http.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": self.config.youtube_oauth_client_id,
                "client_secret": self.config.youtube_oauth_client_secret,
                "grant_type": "refresh_token",
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if response.status_code >= 400:
            raise SocialProviderError(
                f"YouTube token refresh failed: {self._extract_error(response)}",
                reauth=True,
            )
        tokens = self._parse_tokens(response.json())
        # Google only returns the refresh token on the first exchange.
        tokens.refresh_token = tokens.refresh_token or refresh_token
        return tokens

    @staticmethod
    def _parse_tokens(payload: Dict[str, Any]) -> OAuthTokens:
        access_token = payload.get("access_token")
        if not access_token:
            raise SocialProviderError("YouTube did not return an access token")
        expires_in = payload.get("expires_in")
        expires_at = None
        if expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        return OAuthTokens(
            access_token=access_token,
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
            scopes=payload.get("scope"),
            raw=payload,
        )

    def fetch_profile(self, access_token: str) -> AccountProfile:
        response = self.http.get(
            f"{API_BASE}/channels",
            params={"part": "snippet", "mine": "true"},
            headers=self._auth_headers(access_token),
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_for_status(response, "load the YouTube channel")
        items = response.json().get("items") or []
        if not items:
            raise SocialProviderError(
                "This Google account has no YouTube channel. Create a channel first."
            )
        channel = items[0]
        snippet = channel.get("snippet", {})
        thumbnails = snippet.get("thumbnails", {})
        avatar = (thumbnails.get("default") or thumbnails.get("medium") or {}).get("url")
        custom_url = snippet.get("customUrl")
        return AccountProfile(
            external_account_id=channel["id"],
            display_name=snippet.get("title"),
            username=custom_url,
            avatar_url=avatar,
            metadata={"channel_id": channel["id"], "custom_url": custom_url},
        )

    def revoke_tokens(
        self, access_token: Optional[str], refresh_token: Optional[str]
    ) -> bool:
        # Revoking the refresh token ends the whole grant, access tokens included.
        token = refresh_token or access_token
        if not token:
            return False
        response = self.http.post(
            REVOKE_URL,
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if response.status_code >= 400:
            raise SocialProviderError(
                f"YouTube token revocation failed: {self._extract_error(response)}"
            )
        return True

    # -- publishing -----------------------------------------------------
    def publish(self, access_token: str, request: PublishRequest) -> PublishResult:
        privacy = PRIVACY_MAP.get(request.privacy_level, "private")
        title = (request.title or "").strip()[:TITLE_MAX_LENGTH] or "Untitled clip"
        hashtags = list(request.hashtags)
        if not any(tag.lower().lstrip("#") == "shorts" for tag in hashtags):
            hashtags.append("Shorts")
        description = build_caption(request.caption, hashtags, DESCRIPTION_MAX_LENGTH)
        tags = [tag.lstrip("#") for tag in hashtags if tag.strip("# ")][:30]

        file_size = os.path.getsize(request.file_path)
        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        init_response = self.http.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                **self._auth_headers(access_token),
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(file_size),
            },
            json=metadata,
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_for_status(init_response, "start the YouTube upload")
        upload_url = init_response.headers.get("Location")
        if not upload_url:
            raise SocialProviderError("YouTube did not return an upload session URL")

        # Once the bytes are sent YouTube may create the video even if the
        # response never arrives.
        with ambiguous_outcome("upload the video to YouTube"):
            with open(request.file_path, "rb") as handle:
                upload_response = self.http.put(
                    upload_url,
                    data=handle,
                    headers={
                        **self._auth_headers(access_token),
                        "Content-Type": "video/mp4",
                        "Content-Length": str(file_size),
                    },
                    timeout=max(DEFAULT_HTTP_TIMEOUT, 900),
                )
            self._raise_for_status(upload_response, "upload the video to YouTube")
            payload = upload_response.json()
        video_id = payload.get("id")
        if not video_id:
            raise SocialProviderError("YouTube upload finished without a video id")
        return PublishResult(
            external_post_id=video_id,
            external_url=f"https://www.youtube.com/shorts/{video_id}",
            raw=payload,
        )

    # -- analytics ------------------------------------------------------
    def fetch_metrics(
        self, access_token: str, external_post_id: str, account_metadata: Dict[str, Any]
    ) -> PostMetrics:
        response = self.http.get(
            f"{API_BASE}/videos",
            params={"part": "statistics", "id": external_post_id},
            headers=self._auth_headers(access_token),
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_for_status(response, "load YouTube statistics")
        items = response.json().get("items") or []
        if not items:
            raise SocialProviderError("Video not found on YouTube (it may have been deleted)")
        stats = items[0].get("statistics", {})
        return PostMetrics(
            views=_to_int(stats.get("viewCount")),
            likes=_to_int(stats.get("likeCount")),
            comments=_to_int(stats.get("commentCount")),
            shares=None,
            saves=None,
            raw=stats,
        )

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _auth_headers(access_token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    def _raise_for_status(self, response, action: str) -> None:
        if response.status_code < 400:
            return
        message = self._extract_error(response)
        if response.status_code == 401:
            raise SocialProviderError(
                f"YouTube rejected the credentials while trying to {action}: {message}",
                reauth=True,
            )
        reasons = _error_reasons(response)
        if response.status_code in (403, 429) and is_daily_quota_error(message, reasons):
            # SupoClip's shared daily allowance, not the user's channel: the
            # post waits for the reset instead of failing.
            raise SocialProviderError(
                QUOTA_HOLD_MESSAGE,
                hold_until=quota_reset_after(),
                hold_reason=QUOTA_HOLD_REASON,
            )
        if is_channel_limit_error(message, reasons):
            raise SocialProviderError(CHANNEL_LIMIT_MESSAGE)
        retryable = response.status_code in (403, 429, 500, 502, 503, 504) and (
            "quota" in message.lower()
            or "rate" in message.lower()
            or response.status_code >= 500
        )
        raise SocialProviderError(
            f"Failed to {action}: {message}", retryable=retryable
        )


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
