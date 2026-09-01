"""
Instagram Reels publishing via the Instagram API with Instagram Login.

This is Meta's newer "business login" flow: it needs an Instagram
professional (Business or Creator) account but *not* a linked Facebook Page.

OAuth: ``instagram.com/oauth/authorize`` -> short-lived token ->
long-lived token (60 days, refreshable once it is older than 24 hours).
Upload: Instagram does not accept file uploads. It fetches the video from a
public URL, so the caller must supply ``PublishRequest.public_media_url``.
Metrics: ``/{media_id}?fields=like_count,comments_count`` plus
``/{media_id}/insights``.
"""

from __future__ import annotations

import logging
import time
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

API_VERSION = "v21.0"
AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
SHORT_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE = "https://graph.instagram.com"
GRAPH_API = f"{GRAPH_BASE}/{API_VERSION}"

SCOPES = (
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
)

CAPTION_MAX_LENGTH = 2200
CONTAINER_POLL_INTERVAL_SECONDS = 5
CONTAINER_POLL_TIMEOUT_SECONDS = 600

INSIGHT_METRIC_SETS = (
    "views,reach,shares,saved",
    "plays,reach,shares,saved",
)


class InstagramProvider(SocialProvider):
    name = "instagram"
    display_name = "Instagram Reels"
    supported_privacy_levels = ("public",)

    def __init__(self, config: Optional[Config] = None, session=None, sleep=time.sleep):
        super().__init__(session)
        self.config = config or get_config()
        self._sleep = sleep

    @property
    def is_configured(self) -> bool:
        return bool(self.config.instagram_app_id and self.config.instagram_app_secret)

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(
        self, *, state: str, redirect_uri: str, code_challenge: Optional[str] = None
    ) -> str:
        params = {
            "client_id": self.config.instagram_app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        # Instagram appends "#_" to the code in some redirects.
        code = code.split("#")[0]
        response = self.http.post(
            SHORT_TOKEN_URL,
            data={
                "client_id": self.config.instagram_app_id,
                "client_secret": self.config.instagram_app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if response.status_code >= 400:
            raise SocialProviderError(
                f"Instagram token exchange failed: {self._extract_error(response)}"
            )
        short_payload = response.json()
        short_token = short_payload.get("access_token")
        if not short_token:
            raise SocialProviderError("Instagram did not return an access token")

        long_response = self.http.get(
            f"{GRAPH_BASE}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.config.instagram_app_secret,
                "access_token": short_token,
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if long_response.status_code >= 400:
            raise SocialProviderError(
                "Instagram long-lived token exchange failed: "
                f"{self._extract_error(long_response)}"
            )
        long_payload = long_response.json()
        tokens = self._parse_long_lived(long_payload)
        tokens.raw["user_id"] = short_payload.get("user_id")
        tokens.scopes = ",".join(SCOPES)
        return tokens

    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        # Long-lived Instagram tokens refresh themselves; the "refresh token"
        # we store is simply the current long-lived token.
        response = self.http.get(
            f"{GRAPH_BASE}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": refresh_token},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        if response.status_code >= 400:
            raise SocialProviderError(
                f"Instagram token refresh failed: {self._extract_error(response)}",
                reauth=True,
            )
        return self._parse_long_lived(response.json())

    @staticmethod
    def _parse_long_lived(payload: Dict[str, Any]) -> OAuthTokens:
        token = payload.get("access_token")
        if not token:
            raise SocialProviderError("Instagram did not return a long-lived token")
        expires_in = payload.get("expires_in")
        expires_at = None
        if expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        return OAuthTokens(
            access_token=token,
            refresh_token=token,
            expires_at=expires_at,
            raw={"token_type": payload.get("token_type")},
        )

    def fetch_profile(self, access_token: str) -> AccountProfile:
        response = self.http.get(
            f"{GRAPH_API}/me",
            params={
                "fields": "id,user_id,username,name,profile_picture_url,account_type",
                "access_token": access_token,
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_for_status(response, "load the Instagram profile")
        payload = response.json()
        ig_id = str(payload.get("user_id") or payload.get("id") or "")
        if not ig_id:
            raise SocialProviderError("Instagram did not return an account id")
        account_type = payload.get("account_type")
        if account_type and account_type.upper() not in {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}:
            raise SocialProviderError(
                "Instagram publishing requires a professional (Business or Creator) "
                f"account; this account is '{account_type}'."
            )
        return AccountProfile(
            external_account_id=ig_id,
            display_name=payload.get("name") or payload.get("username"),
            username=payload.get("username"),
            avatar_url=payload.get("profile_picture_url"),
            metadata={
                "ig_user_id": ig_id,
                "app_scoped_id": payload.get("id"),
                "account_type": account_type,
            },
        )

    # -- publishing -----------------------------------------------------
    def publish(self, access_token: str, request: PublishRequest) -> PublishResult:
        if not request.public_media_url:
            raise SocialProviderError(
                "Instagram needs a publicly reachable video URL. Set NEXT_PUBLIC_APP_URL "
                "(or SOCIAL_PUBLIC_MEDIA_BASE_URL) to a URL Instagram can fetch."
            )
        caption = build_caption(request.caption or request.title, request.hashtags, CAPTION_MAX_LENGTH)

        create_response = self.http.post(
            f"{GRAPH_API}/me/media",
            data={
                "media_type": "REELS",
                "video_url": request.public_media_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": access_token,
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_for_status(create_response, "create the Instagram media container")
        container_id = create_response.json().get("id")
        if not container_id:
            raise SocialProviderError("Instagram did not return a media container id")

        self._wait_for_container(access_token, container_id)

        publish_response = self.http.post(
            f"{GRAPH_API}/me/media_publish",
            data={"creation_id": container_id, "access_token": access_token},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_for_status(publish_response, "publish the Instagram reel")
        media_id = publish_response.json().get("id")
        if not media_id:
            raise SocialProviderError("Instagram publish finished without a media id")

        permalink = None
        try:
            link_response = self.http.get(
                f"{GRAPH_API}/{media_id}",
                params={"fields": "permalink", "access_token": access_token},
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if link_response.status_code < 400:
                permalink = link_response.json().get("permalink")
        except Exception:  # pragma: no cover - best effort
            logger.debug("Could not load Instagram permalink", exc_info=True)

        return PublishResult(
            external_post_id=str(media_id),
            external_url=permalink,
            raw={"container_id": container_id},
        )

    def _wait_for_container(self, access_token: str, container_id: str) -> None:
        deadline = time.monotonic() + CONTAINER_POLL_TIMEOUT_SECONDS
        while True:
            response = self.http.get(
                f"{GRAPH_API}/{container_id}",
                params={"fields": "status_code,status", "access_token": access_token},
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            self._raise_for_status(response, "check the Instagram media container")
            payload = response.json()
            status_code = payload.get("status_code")
            if status_code == "FINISHED":
                return
            if status_code in {"ERROR", "EXPIRED"}:
                raise SocialProviderError(
                    f"Instagram could not process the video: {payload.get('status') or status_code}"
                )
            if time.monotonic() > deadline:
                raise SocialProviderError(
                    "Timed out waiting for Instagram to process the video", retryable=True
                )
            self._sleep(CONTAINER_POLL_INTERVAL_SECONDS)

    # -- analytics ------------------------------------------------------
    def fetch_metrics(
        self, access_token: str, external_post_id: str, account_metadata: Dict[str, Any]
    ) -> PostMetrics:
        response = self.http.get(
            f"{GRAPH_API}/{external_post_id}",
            params={
                "fields": "like_count,comments_count,permalink",
                "access_token": access_token,
            },
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        self._raise_for_status(response, "load Instagram media statistics")
        basic = response.json()
        metrics = PostMetrics(
            likes=_to_int(basic.get("like_count")),
            comments=_to_int(basic.get("comments_count")),
            raw={"basic": basic},
        )

        for metric_set in INSIGHT_METRIC_SETS:
            insight_response = self.http.get(
                f"{GRAPH_API}/{external_post_id}/insights",
                params={"metric": metric_set, "access_token": access_token},
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if insight_response.status_code >= 400:
                continue
            values: Dict[str, Optional[int]] = {}
            for entry in insight_response.json().get("data") or []:
                name = entry.get("name")
                entry_values = entry.get("values") or []
                value = entry_values[0].get("value") if entry_values else entry.get("value")
                values[name] = _to_int(value)
            metrics.views = values.get("views", values.get("plays"))
            metrics.shares = values.get("shares")
            metrics.saves = values.get("saved")
            metrics.raw["insights"] = values
            break
        return metrics

    # -- helpers --------------------------------------------------------
    def _raise_for_status(self, response, action: str) -> None:
        if response.status_code < 400:
            return
        message = self._extract_error(response)
        lowered = message.lower()
        if response.status_code in (401,) or "oauth" in lowered or "session has expired" in lowered or "(190)" in message:
            raise SocialProviderError(
                f"Instagram rejected the credentials while trying to {action}: {message}",
                reauth=True,
            )
        retryable = response.status_code >= 500 or "(4)" in message or "(17)" in message or "(32)" in message
        raise SocialProviderError(f"Failed to {action}: {message}", retryable=retryable)


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
