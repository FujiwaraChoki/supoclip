"""
TikTok publishing via the Content Posting API (Direct Post).

OAuth: TikTok Login Kit v2 with PKCE.
Upload: ``/v2/post/publish/video/init/`` + chunked PUT to the returned upload
URL, then ``/v2/post/publish/status/fetch/`` until the post exists.
Metrics: ``/v2/video/query/`` (requires the ``video.list`` scope).

Unaudited TikTok apps can only post with ``SELF_ONLY`` visibility and to a
handful of accounts per day; the creator-info query tells us which privacy
levels the connected account is allowed to use.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
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

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API_BASE = "https://open.tiktokapis.com/v2"

SCOPES = ("user.info.basic", "video.publish", "video.list")

PRIVACY_MAP = {
    "public": "PUBLIC_TO_EVERYONE",
    "unlisted": "MUTUAL_FOLLOW_FRIENDS",
    "private": "SELF_ONLY",
}
PRIVACY_LABELS = {value: key for key, value in PRIVACY_MAP.items()}

TITLE_MAX_LENGTH = 2200
# TikTok accepts 5 MB - 64 MB chunks (the final chunk may be larger). A single
# chunk is allowed when the whole file fits in 64 MB.
SINGLE_CHUNK_LIMIT = 64 * 1024 * 1024
CHUNK_SIZE = 50 * 1024 * 1024

TERMINAL_FAILURE_STATUSES = {"FAILED"}


class TikTokProvider(SocialProvider):
    name = "tiktok"
    display_name = "TikTok"

    def __init__(self, config: Optional[Config] = None, session=None):
        super().__init__(session)
        self.config = config or get_config()

    @property
    def is_configured(self) -> bool:
        return bool(self.config.tiktok_client_key and self.config.tiktok_client_secret)

    @property
    def uses_pkce(self) -> bool:
        return True

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(
        self, *, state: str, redirect_uri: str, code_challenge: Optional[str] = None
    ) -> str:
        params = {
            "client_key": self.config.tiktok_client_key,
            "scope": ",".join(SCOPES),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        data = {
            "client_key": self.config.tiktok_client_key,
            "client_secret": self.config.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        response = self.http.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        payload = self._json(response)
        if response.status_code >= 400 or payload.get("error"):
            raise SocialProviderError(
                f"TikTok token exchange failed: {self._describe_error(payload, response)}"
            )
        return self._parse_tokens(payload)

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        response = self.http.post(
            TOKEN_URL,
            data={
                "client_key": self.config.tiktok_client_key,
                "client_secret": self.config.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        payload = self._json(response)
        if response.status_code >= 400 or payload.get("error"):
            raise SocialProviderError(
                f"TikTok token refresh failed: {self._describe_error(payload, response)}",
                reauth=True,
            )
        tokens = self._parse_tokens(payload)
        tokens.refresh_token = tokens.refresh_token or refresh_token
        return tokens

    @staticmethod
    def _parse_tokens(payload: Dict[str, Any]) -> OAuthTokens:
        access_token = payload.get("access_token")
        if not access_token:
            raise SocialProviderError("TikTok did not return an access token")
        expires_in = payload.get("expires_in")
        expires_at = None
        if expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        return OAuthTokens(
            access_token=access_token,
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
            scopes=payload.get("scope"),
            raw={key: value for key, value in payload.items() if "token" not in key},
        )

    def fetch_profile(self, access_token: str) -> AccountProfile:
        response = self.http.get(
            f"{API_BASE}/user/info/",
            params={"fields": "open_id,union_id,avatar_url,display_name,username"},
            headers=self._auth_headers(access_token),
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        payload = self._json(response)
        self._raise_for_error(payload, response, "load the TikTok profile")
        user = (payload.get("data") or {}).get("user") or {}
        open_id = user.get("open_id")
        if not open_id:
            raise SocialProviderError("TikTok did not return an account id")
        return AccountProfile(
            external_account_id=open_id,
            display_name=user.get("display_name"),
            username=user.get("username"),
            avatar_url=user.get("avatar_url"),
            metadata={"open_id": open_id, "union_id": user.get("union_id")},
        )

    # -- publishing -----------------------------------------------------
    def query_creator_info(self, access_token: str) -> Dict[str, Any]:
        response = self.http.post(
            f"{API_BASE}/post/publish/creator_info/query/",
            headers={
                **self._auth_headers(access_token),
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        payload = self._json(response)
        self._raise_for_error(payload, response, "query TikTok posting permissions")
        return payload.get("data") or {}

    def publish(self, access_token: str, request: PublishRequest) -> PublishResult:
        creator_info = self.query_creator_info(access_token)
        allowed_levels: List[str] = creator_info.get("privacy_level_options") or []
        privacy = PRIVACY_MAP.get(request.privacy_level, "SELF_ONLY")
        if allowed_levels and privacy not in allowed_levels:
            readable = ", ".join(
                PRIVACY_LABELS.get(level, level.lower()) for level in allowed_levels
            )
            raise SocialProviderError(
                f"TikTok does not allow '{request.privacy_level}' posts for this account "
                f"right now. Allowed: {readable}. (Unaudited TikTok apps can only post "
                "privately until TikTok approves the app.)"
            )
        max_duration = creator_info.get("max_video_post_duration_sec")

        title = build_caption(
            request.title if request.caption.strip() == "" else request.caption,
            request.hashtags,
            TITLE_MAX_LENGTH,
        )
        file_size = os.path.getsize(request.file_path)
        if file_size <= SINGLE_CHUNK_LIMIT:
            chunk_size = file_size
            total_chunks = 1
        else:
            chunk_size = CHUNK_SIZE
            total_chunks = max(1, math.floor(file_size / chunk_size))

        init_body = {
            "post_info": {
                "title": title,
                "privacy_level": privacy,
                "disable_duet": False,
                "disable_comment": bool(creator_info.get("comment_disabled", False)),
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }
        response = self.http.post(
            f"{API_BASE}/post/publish/video/init/",
            json=init_body,
            headers={
                **self._auth_headers(access_token),
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        payload = self._json(response)
        self._raise_for_error(payload, response, "start the TikTok upload")
        data = payload.get("data") or {}
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise SocialProviderError("TikTok did not return an upload session")

        self._upload_chunks(upload_url, request.file_path, file_size, chunk_size, total_chunks)

        # TikTok processes the post asynchronously; try a quick resolve so the
        # common fast path returns a post id immediately.
        result = self.resolve_pending(access_token, publish_id)
        if result is not None:
            return result
        return PublishResult(
            external_post_id=None,
            external_url=None,
            pending_handle=publish_id,
            raw={"publish_id": publish_id, "max_video_post_duration_sec": max_duration},
        )

    def _upload_chunks(
        self,
        upload_url: str,
        file_path: str,
        file_size: int,
        chunk_size: int,
        total_chunks: int,
    ) -> None:
        with open(file_path, "rb") as handle:
            for index in range(total_chunks):
                start = index * chunk_size
                is_last = index == total_chunks - 1
                end = file_size - 1 if is_last else start + chunk_size - 1
                handle.seek(start)
                blob = handle.read(end - start + 1)
                response = self.http.put(
                    upload_url,
                    data=blob,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(blob)),
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                    },
                    timeout=max(DEFAULT_HTTP_TIMEOUT, 900),
                )
                if response.status_code not in (200, 201, 206):
                    raise SocialProviderError(
                        f"TikTok chunk upload failed (HTTP {response.status_code}): "
                        f"{(response.text or '')[:300]}",
                        retryable=response.status_code >= 500,
                    )

    def resolve_pending(
        self, access_token: str, pending_handle: str
    ) -> Optional[PublishResult]:
        response = self.http.post(
            f"{API_BASE}/post/publish/status/fetch/",
            json={"publish_id": pending_handle},
            headers={
                **self._auth_headers(access_token),
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        payload = self._json(response)
        self._raise_for_error(payload, response, "check the TikTok post status")
        data = payload.get("data") or {}
        status = data.get("status")
        if status in TERMINAL_FAILURE_STATUSES:
            raise SocialProviderError(
                f"TikTok rejected the post: {data.get('fail_reason') or 'unknown reason'}"
            )
        if status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get(
                "publicly_available_post_id"
            )
            post_id = str(ids[0]) if isinstance(ids, list) and ids else None
            return PublishResult(
                external_post_id=post_id,
                external_url=None,
                raw=data,
            )
        return None

    # -- analytics ------------------------------------------------------
    def fetch_metrics(
        self, access_token: str, external_post_id: str, account_metadata: Dict[str, Any]
    ) -> PostMetrics:
        response = self.http.post(
            f"{API_BASE}/video/query/",
            params={
                "fields": "id,like_count,comment_count,share_count,view_count,share_url"
            },
            json={"filters": {"video_ids": [external_post_id]}},
            headers={
                **self._auth_headers(access_token),
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        payload = self._json(response)
        self._raise_for_error(payload, response, "load TikTok video statistics")
        videos = (payload.get("data") or {}).get("videos") or []
        if not videos:
            raise SocialProviderError("Video not found on TikTok (it may have been deleted)")
        video = videos[0]
        return PostMetrics(
            views=_to_int(video.get("view_count")),
            likes=_to_int(video.get("like_count")),
            comments=_to_int(video.get("comment_count")),
            shares=_to_int(video.get("share_count")),
            saves=None,
            raw=video,
        )

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _auth_headers(access_token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    @staticmethod
    def _json(response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _describe_error(payload: Dict[str, Any], response) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            if code and code != "ok":
                return f"{message or 'request failed'} ({code})"
        if isinstance(error, str):
            description = payload.get("error_description")
            return f"{error}: {description}" if description else error
        return f"HTTP {response.status_code}"

    def _raise_for_error(self, payload: Dict[str, Any], response, action: str) -> None:
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else error
        if response.status_code < 400 and (not code or code == "ok"):
            return
        message = self._describe_error(payload, response)
        code_text = str(code or "").lower()
        if response.status_code == 401 or code_text in {
            "access_token_invalid",
            "scope_not_authorized",
            "invalid_grant",
        }:
            raise SocialProviderError(
                f"TikTok rejected the credentials while trying to {action}: {message}",
                reauth=True,
            )
        retryable = response.status_code >= 500 or code_text in {
            "rate_limit_exceeded",
            "spam_risk_too_many_posts",
            "internal_error",
        }
        raise SocialProviderError(f"Failed to {action}: {message}", retryable=retryable)


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
