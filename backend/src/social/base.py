"""
Shared types and the provider interface for social publishing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

SUPPORTED_PROVIDERS = ("youtube", "tiktok", "instagram")

# Privacy levels are normalized across providers. Each provider maps these to
# its own vocabulary and may reject some (e.g. unaudited TikTok apps can only
# post "private").
PRIVACY_LEVELS = ("public", "unlisted", "private")

DEFAULT_HTTP_TIMEOUT = 60


class SocialProviderError(Exception):
    """Raised when a provider call fails in a way the caller should surface."""

    def __init__(self, message: str, *, retryable: bool = False, reauth: bool = False):
        super().__init__(message)
        self.retryable = retryable
        # ``reauth`` means the stored credentials are no longer usable and the
        # user must reconnect the account.
        self.reauth = reauth


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    scopes: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountProfile:
    external_account_id: str
    display_name: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishRequest:
    file_path: str
    title: str
    caption: str
    hashtags: List[str]
    privacy_level: str
    # Publicly fetchable URL for the media file. Required by Instagram, which
    # pulls the video instead of accepting an upload.
    public_media_url: Optional[str] = None
    account_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    """An accepted upload; no pending handle means publication is complete.

    A completed private post can have neither a public ID nor a public URL.
    """

    external_post_id: Optional[str]
    external_url: Optional[str]
    # Some platforms (TikTok) finish processing asynchronously; when the final
    # post id is not yet known we keep a handle to poll later.
    pending_handle: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PostMetrics:
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "saves": self.saves,
        }


class SocialProvider(ABC):
    """Interface implemented by each platform integration."""

    name: str = ""
    display_name: str = ""
    # Which normalized privacy levels the platform supports at all.
    supported_privacy_levels: tuple[str, ...] = PRIVACY_LEVELS

    def __init__(self, session: Optional[requests.Session] = None):
        self.http = session or requests.Session()

    # -- configuration -------------------------------------------------
    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True when the operator has supplied OAuth app credentials."""

    @property
    def uses_pkce(self) -> bool:
        return False

    # -- OAuth ----------------------------------------------------------
    @abstractmethod
    def build_authorize_url(
        self, *, state: str, redirect_uri: str, code_challenge: Optional[str] = None
    ) -> str:
        ...

    @abstractmethod
    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        ...

    @abstractmethod
    def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        ...

    @abstractmethod
    def fetch_profile(self, access_token: str) -> AccountProfile:
        ...

    # -- publishing -----------------------------------------------------
    @abstractmethod
    def publish(self, access_token: str, request: PublishRequest) -> PublishResult:
        ...

    def resolve_pending(
        self, access_token: str, pending_handle: str
    ) -> Optional[PublishResult]:
        """
        Poll a platform that publishes asynchronously. Returns a result once the
        post exists, ``None`` while still processing. Providers that publish
        synchronously never need this.
        """
        return None

    # -- analytics ------------------------------------------------------
    @abstractmethod
    def fetch_metrics(
        self, access_token: str, external_post_id: str, account_metadata: Dict[str, Any]
    ) -> PostMetrics:
        ...

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _extract_error(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "").strip()[:500] or f"HTTP {response.status_code}"
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("error_description")
                code = error.get("code") or error.get("type")
                return f"{message or 'Request failed'} ({code})" if code else str(message)
            if isinstance(error, str):
                description = payload.get("error_description") or payload.get("message")
                return f"{error}: {description}" if description else error
            if payload.get("message"):
                return str(payload["message"])
        return str(payload)[:500]


def build_caption(caption: str, hashtags: List[str], max_length: int) -> str:
    """Append hashtags to a caption, trimming the caption to fit the limit."""
    tags = " ".join(
        f"#{tag.lstrip('#').strip()}" for tag in hashtags if tag and tag.strip("# ")
    )
    text = (caption or "").strip()
    if tags:
        text = f"{text}\n\n{tags}".strip() if text else tags
    if len(text) > max_length:
        if tags and len(tags) + 1 < max_length:
            room = max_length - len(tags) - 2
            text = f"{(caption or '').strip()[:room].rstrip()}\n\n{tags}"
        else:
            text = text[:max_length].rstrip()
    return text
