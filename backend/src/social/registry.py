"""
Lookup of social providers by name.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from ..config import Config, get_config
from .base import SUPPORTED_PROVIDERS, SocialProvider
from .instagram import InstagramProvider
from .tiktok import TikTokProvider
from .youtube import YouTubeProvider

_PROVIDER_CLASSES: Dict[str, Type[SocialProvider]] = {
    "youtube": YouTubeProvider,
    "tiktok": TikTokProvider,
    "instagram": InstagramProvider,
}

PROVIDER_SETUP_HINTS = {
    "youtube": "Set YOUTUBE_OAUTH_CLIENT_ID and YOUTUBE_OAUTH_CLIENT_SECRET from a Google Cloud OAuth client.",
    "tiktok": "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET from the TikTok for Developers portal.",
    "instagram": "Set INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET from a Meta app with Instagram Login.",
}


def get_provider(name: str, config: Optional[Config] = None) -> SocialProvider:
    normalized = (name or "").strip().lower()
    provider_class = _PROVIDER_CLASSES.get(normalized)
    if provider_class is None:
        raise ValueError(f"Unsupported social provider: {name}")
    return provider_class(config or get_config())


def list_provider_status(config: Optional[Config] = None) -> List[Dict[str, Any]]:
    runtime_config = config or get_config()
    statuses = []
    for name in SUPPORTED_PROVIDERS:
        provider = get_provider(name, runtime_config)
        statuses.append(
            {
                "provider": name,
                "display_name": provider.display_name,
                "configured": provider.is_configured,
                "supported_privacy_levels": list(provider.supported_privacy_levels),
                "setup_hint": None if provider.is_configured else PROVIDER_SETUP_HINTS[name],
            }
        )
    return statuses
