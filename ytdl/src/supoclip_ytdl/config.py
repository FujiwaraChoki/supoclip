"""Environment configuration for the download service."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Route:
    """One way out to YouTube: direct, or a proxy URL template.

    ``proxy`` may contain ``{session}``; every attempt substitutes a fresh random
    token so providers with sticky sessions hand out a new exit IP per attempt
    while keeping extraction and media requests on the same IP (YouTube media
    URLs are bound to the IP that extracted them).
    """

    name: str
    proxy: str | None = None
    attempts: int = 2


def _routes_from_env() -> tuple[Route, ...]:
    raw = os.getenv("YTDL_ROUTES", "").strip()
    if raw:
        routes = []
        for item in json.loads(raw):
            name = str(item["name"]).strip()
            if not name:
                raise ValueError("Every YTDL_ROUTES entry needs a name")
            routes.append(Route(name=name, proxy=item.get("proxy") or None,
                                attempts=max(1, int(item.get("attempts", 2)))))
    else:
        routes = []
        if _bool("YTDL_TRY_DIRECT", False):
            routes.append(Route(name="direct", attempts=1))
        if os.getenv("YTDL_PROXY_URL"):
            routes.append(Route(name="proxy", proxy=os.environ["YTDL_PROXY_URL"],
                                attempts=max(1, int(os.getenv("YTDL_PROXY_ATTEMPTS", "4")))))
        if not routes:
            routes.append(Route(name="direct", attempts=2))
    if len({r.name for r in routes}) != len(routes):
        raise ValueError("YTDL_ROUTES names must be unique")
    return tuple(routes)


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _client_groups() -> tuple[str | None, ...]:
    # Semicolon-separated groups, tried in order across attempts. "default" keeps
    # yt-dlp's own choice, which tracks what currently works best.
    raw = os.getenv("YTDL_PLAYER_CLIENTS", "default;mweb;tv_simply,web_safari")
    groups = []
    for group in raw.split(";"):
        group = group.strip()
        if group:
            groups.append(None if group == "default" else group)
    return tuple(groups) or (None,)


@dataclass(frozen=True)
class Settings:
    api_token: str = field(default_factory=lambda: os.getenv("YTDL_API_TOKEN", ""))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("YTDL_DATA_DIR", "/data")))
    routes: tuple[Route, ...] = field(default_factory=_routes_from_env)
    player_clients: tuple[str | None, ...] = field(default_factory=_client_groups)
    pot_base_url: str | None = field(default_factory=lambda: os.getenv("YTDL_POT_BASE_URL") or None)
    max_concurrent: int = field(default_factory=lambda: max(1, int(os.getenv("YTDL_MAX_CONCURRENT", "3"))))
    max_attempts: int = field(default_factory=lambda: max(1, int(os.getenv("YTDL_MAX_ATTEMPTS", "6"))))
    default_max_height: int = field(default_factory=lambda: int(os.getenv("YTDL_DEFAULT_MAX_HEIGHT", "1080")))
    max_duration_seconds: int = field(default_factory=lambda: int(os.getenv("YTDL_MAX_DURATION_SECONDS", "14400")))
    max_bytes: int = field(default_factory=lambda: int(os.getenv("YTDL_MAX_BYTES", "12000000000")))
    job_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("YTDL_JOB_TIMEOUT_SECONDS", "3600")))
    # A stalled proxy can trickle bytes forever; rotate when throughput stays below this.
    min_speed_bytes: int = field(default_factory=lambda: int(os.getenv("YTDL_MIN_SPEED_BYTES", "150000")))
    stall_seconds: int = field(default_factory=lambda: int(os.getenv("YTDL_STALL_SECONDS", "60")))
    cache_ttl_seconds: int = field(default_factory=lambda: int(float(os.getenv("YTDL_CACHE_TTL_HOURS", "24")) * 3600))
    cache_max_bytes: int = field(default_factory=lambda: int(float(os.getenv("YTDL_CACHE_MAX_GB", "100")) * 1024**3))
    circuit_threshold: int = field(default_factory=lambda: max(1, int(os.getenv("YTDL_CIRCUIT_THRESHOLD", "3"))))
    circuit_cooldown_seconds: int = field(default_factory=lambda: int(os.getenv("YTDL_CIRCUIT_COOLDOWN_SECONDS", "600")))
    # Exit (for a restart into a fresh yt-dlp) when PyPI has a newer build. 0 disables.
    update_check_hours: float = field(default_factory=lambda: float(os.getenv("YTDL_UPDATE_CHECK_HOURS", "6")))
    update_channel: str = field(default_factory=lambda: os.getenv("YTDL_UPDATE_CHANNEL", "nightly").strip().lower())

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def ytdlp_cache_dir(self) -> Path:
        # Player JS and signature solutions: persisting them saves a few MB of
        # proxy traffic per extraction.
        return self.data_dir / "yt-dlp-cache"
