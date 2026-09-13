"""Live Ollama daemon/model status probing.

Mirrors `video_utils.detect_gpu_encoder()`'s probe-don't-assume approach: a
configured base URL or an installed CLI doesn't mean the daemon is actually
reachable, so this always makes a real request rather than trusting config.
Used by the Settings "Test connection" action and by admin.py's
`disabled_reason` computation for the OLLAMA_MODEL dropdown.
"""

import shutil
import subprocess
from typing import List, Optional

import httpx
from pydantic import BaseModel


class OllamaStatus(BaseModel):
    installed_locally: Optional[bool] = None  # None = unknown (e.g. checked from inside a container)
    reachable: bool = False
    version: Optional[str] = None
    models: List[str] = []
    error: Optional[str] = None


def _api_base_url(base_url: str) -> str:
    """Strip the `/v1` OpenAI-compat suffix used by pydantic-ai, if present.

    `Config.resolve_ollama_base_url()` returns an OpenAI-compatible endpoint
    (e.g. `http://localhost:11434/v1`) for the Pydantic AI provider; the
    native `/api/version` and `/api/tags` endpoints used here live one level
    up, at the bare host root.
    """
    return base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url


def _check_cli_installed() -> Optional[bool]:
    try:
        return shutil.which("ollama") is not None
    except Exception:
        return None


async def check_ollama_status(base_url: str, timeout: float = 5.0) -> OllamaStatus:
    """Probe the Ollama daemon at `base_url` for reachability, version, and installed models."""
    root = _api_base_url(base_url)
    installed_locally = _check_cli_installed()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            version_resp = await client.get(f"{root}/api/version")
            version_resp.raise_for_status()
            version = version_resp.json().get("version")

            tags_resp = await client.get(f"{root}/api/tags")
            tags_resp.raise_for_status()
            models = [m.get("name") for m in tags_resp.json().get("models", []) if m.get("name")]

        return OllamaStatus(
            installed_locally=installed_locally,
            reachable=True,
            version=version,
            models=models,
        )
    except Exception as exc:
        return OllamaStatus(
            installed_locally=installed_locally,
            reachable=False,
            error=str(exc),
        )


def check_ollama_cli_version() -> Optional[str]:
    """Best-effort `ollama --version` shellout, for environments where the CLI is the source of truth."""
    try:
        result = subprocess.run(
            ["ollama", "--version"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or result.stderr.strip() or None
    except Exception:
        return None
