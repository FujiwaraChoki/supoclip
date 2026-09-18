"""
YouTube Shorts publishing via the YouTube Data API v3.

OAuth: Google OAuth 2.0 (offline access so we receive a refresh token).
Upload: resumable upload to ``videos.insert``.
Metrics: ``videos.list?part=statistics``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

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
    build_caption,
)

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

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

        with open(request.file_path, "rb") as handle:
            upload_response = self.http.put(
                upload_url,
                data=handle,
                headers={
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
