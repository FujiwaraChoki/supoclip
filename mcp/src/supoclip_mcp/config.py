"""
Configuration for the SupoClip MCP server.

All settings come from environment variables so the same server binary works
against the official hosted instance (the default) or any self-hosted backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# The official hosted SupoClip API. Override with SUPOCLIP_API_URL to point at
# a self-hosted backend (e.g. http://localhost:8000).
DEFAULT_API_URL = "https://api.supoclip.com"


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration."""

    api_url: str
    api_key: Optional[str]
    user_id: Optional[str]
    auth_secret: Optional[str]
    download_dir: str
    timeout: float

    @property
    def auth_mode(self) -> str:
        """Describe how requests will be authenticated."""
        if self.api_key:
            return "api_key"
        if self.user_id and self.auth_secret:
            return "signed_headers"
        if self.user_id:
            return "unsigned_user_id"
        return "none"

    @property
    def is_authenticated(self) -> bool:
        return self.auth_mode != "none"


def load_settings() -> Settings:
    """Build :class:`Settings` from the current environment."""
    api_url = (_clean(os.getenv("SUPOCLIP_API_URL")) or DEFAULT_API_URL).rstrip("/")
    download_dir = _clean(os.getenv("SUPOCLIP_DOWNLOAD_DIR")) or os.path.join(
        os.getcwd(), "supoclip-downloads"
    )

    raw_timeout = _clean(os.getenv("SUPOCLIP_TIMEOUT"))
    try:
        timeout = float(raw_timeout) if raw_timeout else 60.0
    except ValueError:
        timeout = 60.0

    return Settings(
        api_url=api_url,
        api_key=_clean(os.getenv("SUPOCLIP_API_KEY")),
        user_id=_clean(os.getenv("SUPOCLIP_USER_ID")),
        auth_secret=_clean(os.getenv("SUPOCLIP_AUTH_SECRET")),
        download_dir=download_dir,
        timeout=timeout,
    )
