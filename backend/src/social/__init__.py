"""
Social publishing providers.

Each provider wraps one platform's OAuth flow, upload API and analytics API
behind the :class:`~src.social.base.SocialProvider` interface. All provider
methods are synchronous (they use ``requests``) and are expected to be run via
``run_in_thread`` from async code.
"""

from .base import (
    AccountProfile,
    OAuthTokens,
    PostMetrics,
    PublishRequest,
    PublishResult,
    SocialProvider,
    SocialProviderError,
    SUPPORTED_PROVIDERS,
)
from .registry import get_provider, list_provider_status

__all__ = [
    "AccountProfile",
    "OAuthTokens",
    "PostMetrics",
    "PublishRequest",
    "PublishResult",
    "SocialProvider",
    "SocialProviderError",
    "SUPPORTED_PROVIDERS",
    "get_provider",
    "list_provider_status",
]
