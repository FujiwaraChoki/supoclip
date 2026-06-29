#!/usr/bin/env python3
"""
MCP server for SupoClip — an AI tool that turns long-form videos into short,
vertical, subtitled viral clips.

The server talks to the SupoClip REST API. By default it targets the official
hosted instance at ``https://api.supoclip.com``; set ``SUPOCLIP_API_URL`` to
use a self-hosted backend. Authenticate by creating an API key in your SupoClip
account and exposing it as ``SUPOCLIP_API_KEY``.

Typical workflow:
    1. ``supoclip_create_clip_task`` with a YouTube URL  -> returns a task_id
    2. ``supoclip_wait_for_task`` (or poll ``supoclip_get_task``) until done
    3. ``supoclip_list_clips`` / ``supoclip_download_clip`` to retrieve results
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import re
import time
from dataclasses import replace
from typing import Annotated, Awaitable, Callable, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import Field

try:  # Python 3.10 lacks typing.Literal niceties only in edge cases; import is fine.
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal  # type: ignore

from .client import AuthNotConfiguredError, SupoClipClient, SupoClipError
from .config import load_settings

SETTINGS = load_settings()
CLIENT = SupoClipClient(SETTINGS)


class SupoClipApiKeyVerifier:
    """Validate MCP Bearer tokens against the configured SupoClip backend."""

    def __init__(self, api_url: str, timeout: float) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._cache: dict[str, tuple[float, bool]] = {}

    async def verify_token(self, token: str) -> AccessToken | None:
        raw_token = token.strip()
        if not raw_token.startswith("sk_"):
            return None

        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = time.time()
        cached = self._cache.get(token_hash)
        if cached and cached[0] > now:
            return self._access_token(raw_token) if cached[1] else None

        valid = await self._is_valid_key(raw_token)
        ttl = 60 if valid else 10
        self._cache[token_hash] = (now + ttl, valid)
        return self._access_token(raw_token) if valid else None

    async def _is_valid_key(self, token: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.api_url}/tasks/billing/summary",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError:
            return False

        return response.status_code < 400

    @staticmethod
    def _access_token(token: str) -> AccessToken:
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return AccessToken(
            token=token,
            client_id=f"supoclip-api-key-{fingerprint}",
            scopes=["supoclip"],
        )


def _build_mcp() -> FastMCP:
    auth_settings = None
    token_verifier = None
    if SETTINGS.mcp_require_bearer_auth:
        public_url = SETTINGS.mcp_public_url or f"http://{SETTINGS.mcp_host}:{SETTINGS.mcp_port}"
        auth_settings = AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url,
            required_scopes=["supoclip"],
        )
        token_verifier = SupoClipApiKeyVerifier(SETTINGS.api_url, SETTINGS.timeout)

    return FastMCP(
        "supoclip_mcp",
        host=SETTINGS.mcp_host,
        port=SETTINGS.mcp_port,
        auth=auth_settings,
        token_verifier=token_verifier,
    )


mcp = _build_mcp()

ProcessingMode = Literal["fast", "balanced", "quality"]
OutputFormat = Literal["vertical", "vertical_pan", "vertical_split", "original"]
ExportPreset = Literal["tiktok", "reels", "shorts"]
TERMINAL_STATES = {"completed", "error", "cancelled"}
VALID_OUTPUT_FORMATS = {"vertical", "vertical_pan", "vertical_split", "original"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _json(data: object) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _client() -> SupoClipClient:
    access_token = get_access_token()
    if access_token and access_token.token:
        return SupoClipClient(
            replace(
                SETTINGS,
                api_key=access_token.token,
                user_id=None,
                auth_secret=None,
            )
        )
    return CLIENT


def tool_errors(func: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Wrap a tool so backend/auth errors come back as readable text, not stack traces."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> str:
        try:
            return await func(*args, **kwargs)
        except AuthNotConfiguredError as exc:
            return f"Error: {exc}"
        except SupoClipError as exc:
            return f"Error: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            return f"Error: unexpected {type(exc).__name__}: {exc}"

    return wrapper


def _safe_filename(name: str, default: str) -> str:
    """Reduce an arbitrary string to a safe ``.mp4`` basename (no path traversal)."""
    base = os.path.basename((name or "").strip()) or default
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not base.lower().endswith(".mp4"):
        base = f"{base}.mp4"
    return base


# --------------------------------------------------------------------------- #
# Public, unauthenticated tools
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="supoclip_health",
    annotations={
        "title": "SupoClip Health & Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_health() -> str:
    """Report SupoClip API status and how this MCP server is configured.

    Use this first to confirm connectivity and that credentials are set up. It
    requires no authentication and reveals no secrets.

    Returns:
        str: JSON with the configured ``api_url``, ``auth_mode``
        (one of ``api_key``/``signed_headers``/``unsigned_user_id``/``none``),
        whether the server is ``authenticated``, the download directory, and the
        backend's reported version/status.
    """
    root = await _client().request("GET", "/", authenticated=False)
    health = await _client().request("GET", "/health", authenticated=False)
    return _json(
        {
            "configured": {
                "api_url": SETTINGS.api_url,
                "auth_mode": "bearer_token" if get_access_token() else SETTINGS.auth_mode,
                "authenticated": bool(get_access_token()) or SETTINGS.is_authenticated,
                "download_dir": SETTINGS.download_dir,
                "mcp_transport": SETTINGS.mcp_transport,
            },
            "backend": root,
            "health": health,
        }
    )


@mcp.tool(
    name="supoclip_list_caption_templates",
    annotations={
        "title": "List Caption Templates",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_list_caption_templates() -> str:
    """List the caption/subtitle templates available for clip generation.

    Each template id (e.g. ``default``, ``hormozi``, ``mrbeast``) can be passed
    as ``caption_template`` to ``supoclip_create_clip_task``. No auth required.

    Returns:
        str: JSON ``{"templates": [{"id", "name", "description", "animation",
        "font_family", "font_size", "font_color", ...}]}``.
    """
    data = await _client().request("GET", "/caption-templates", authenticated=False)
    return _json(data)


@mcp.tool(
    name="supoclip_list_transitions",
    annotations={
        "title": "List Transition Effects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_list_transitions() -> str:
    """List available video transition effects. No auth required.

    Returns:
        str: JSON ``{"transitions": [{"name", "display_name", "file_path"}]}``.
    """
    data = await _client().request("GET", "/transitions", authenticated=False)
    return _json(data)


@mcp.tool(
    name="supoclip_broll_status",
    annotations={
        "title": "B-roll Availability",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_broll_status() -> str:
    """Report whether automatic B-roll overlays are configured on the backend.

    If ``configured`` is false, passing ``include_broll=true`` when creating a
    task has no effect. No auth required.

    Returns:
        str: JSON ``{"configured": bool, "provider": str | null}``.
    """
    data = await _client().request("GET", "/broll/status", authenticated=False)
    return _json(data)


# --------------------------------------------------------------------------- #
# Authenticated tools — discovery & account
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="supoclip_list_fonts",
    annotations={
        "title": "List Fonts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_list_fonts() -> str:
    """List subtitle fonts available to the authenticated account.

    Any returned ``name`` can be used as ``font_family`` in
    ``supoclip_create_clip_task``. Requires authentication.

    Returns:
        str: JSON ``{"fonts": [{"name", "display_name", ...}]}``.
    """
    data = await _client().request("GET", "/fonts")
    return _json(data)


@mcp.tool(
    name="supoclip_billing_summary",
    annotations={
        "title": "Billing & Usage Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_billing_summary() -> str:
    """Get the authenticated account's plan, usage and remaining quota.

    Useful before creating tasks to check whether a paid plan / remaining quota
    allows another video. Requires authentication.

    Returns:
        str: JSON with ``plan``, ``subscription_status``, ``usage_count``,
        ``usage_limit``, ``remaining``, ``upgrade_required`` and
        ``monetization_enabled``.
    """
    data = await _client().request("GET", "/tasks/billing/summary")
    return _json(data)


# --------------------------------------------------------------------------- #
# Authenticated tools — task lifecycle
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="supoclip_create_clip_task",
    annotations={
        "title": "Create Clipping Task",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_create_clip_task(
    url: str,
    title: str = "",
    processing_mode: str = "fast",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    caption_template: str = "default",
    include_broll: bool = False,
    font_family: str = "",
    font_size: int = 0,
    font_color: str = "",
    cut_long_pauses: bool = False,
    remove_filler_words: bool = False,
) -> str:
    """Create a SupoClip task that downloads a video and generates viral short clips.

    Processing is asynchronous: this returns immediately with a ``task_id``.
    Track progress with ``supoclip_wait_for_task`` or ``supoclip_get_task``, then
    fetch results with ``supoclip_list_clips`` / ``supoclip_download_clip``.

    Requires authentication. On the hosted service a paid plan / available quota
    may be required (a 402 error indicates this — see ``supoclip_billing_summary``).

    Args:
        url: YouTube or direct video URL to clip.
        title: Optional task title.
        processing_mode: 'fast' | 'balanced' | 'quality'.
        output_format: 'vertical' | 'vertical_pan' | 'vertical_split' | 'original'.
        add_subtitles: Whether to burn in subtitles.
        caption_template: Caption template id.
        include_broll: Whether to add B-roll overlays.
        font_family / font_size / font_color: Optional subtitle styling overrides.
        cut_long_pauses / remove_filler_words: Optional cleanup toggles.

    Returns:
        str: JSON ``{"task_id": str, "job_id": str, "message": str}`` on success.
    """
    cleaned_url = (url or "").strip()
    if len(cleaned_url) < 4:
        raise SupoClipError("url is required. Pass a YouTube URL or direct video URL.")

    normalized_mode = processing_mode if processing_mode in {"fast", "balanced", "quality"} else "fast"
    normalized_format = output_format if output_format in VALID_OUTPUT_FORMATS else "vertical"

    source: dict = {"url": cleaned_url[:2000]}
    cleaned_title = title.strip()
    if cleaned_title:
        source["title"] = cleaned_title[:300]

    body: dict = {
        "source": source,
        "processing_mode": normalized_mode,
        "output_format": normalized_format,
        "add_subtitles": add_subtitles,
        "caption_template": (caption_template or "default").strip()[:50] or "default",
        "include_broll": include_broll,
    }

    font_options: dict = {}
    cleaned_font_family = font_family.strip()
    if cleaned_font_family:
        font_options["font_family"] = cleaned_font_family[:100]
    if font_size:
        font_options["font_size"] = max(12, min(72, int(font_size)))
    cleaned_font_color = font_color.strip()
    if re.match(r"^#[0-9A-Fa-f]{6}$", cleaned_font_color):
        font_options["font_color"] = cleaned_font_color
    if font_options:
        body["font_options"] = font_options

    if cut_long_pauses:
        body["cut_long_pauses"] = cut_long_pauses
    if remove_filler_words:
        body["remove_filler_words"] = remove_filler_words

    data = await _client().request("POST", "/tasks/", json_body=body)
    return _json(data)


@mcp.tool(
    name="supoclip_list_tasks",
    annotations={
        "title": "List Tasks",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_list_tasks(
    limit: Annotated[
        int,
        Field(default=50, description="Maximum number of tasks to return.", ge=1, le=200),
    ] = 50,
) -> str:
    """List the authenticated user's clipping tasks, newest first.

    Requires authentication.

    Args:
        limit: Maximum tasks to return (1-200, default 50).

    Returns:
        str: JSON ``{"tasks": [{"id", "status", "progress", "title", ...}], "total": int}``.
    """
    data = await _client().request("GET", "/tasks/", params={"limit": limit})
    return _json(data)


@mcp.tool(
    name="supoclip_get_task",
    annotations={
        "title": "Get Task",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_get_task(
    task_id: Annotated[str, Field(description="The task id returned by create_clip_task.", min_length=1)],
) -> str:
    """Get a task's status, progress and generated clips.

    Requires authentication and ownership of the task.

    Args:
        task_id: The task id.

    Returns:
        str: JSON of the task including ``status`` (queued/processing/completed/
        error/cancelled), ``progress`` (0-100), ``progress_message`` and a
        ``clips`` array (each with ``id``, ``filename``, timing and scores).
    """
    data = await _client().request("GET", f"/tasks/{task_id}")
    return _json(data)


@mcp.tool(
    name="supoclip_wait_for_task",
    annotations={
        "title": "Wait For Task",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_wait_for_task(
    task_id: Annotated[str, Field(description="The task id to wait on.", min_length=1)],
    timeout_seconds: Annotated[
        int,
        Field(default=600, description="Give up after this many seconds.", ge=5, le=3600),
    ] = 600,
    poll_interval_seconds: Annotated[
        int,
        Field(default=5, description="Seconds between status checks.", ge=2, le=60),
    ] = 5,
    ctx: Optional[Context] = None,
) -> str:
    """Poll a task until it finishes (completed/error/cancelled) or times out.

    Convenience wrapper around ``supoclip_get_task`` for the asynchronous
    pipeline. Reports progress while waiting. Requires authentication.

    Args:
        task_id: The task id to wait on.
        timeout_seconds: Max seconds to wait (5-3600, default 600).
        poll_interval_seconds: Seconds between polls (2-60, default 5).

    Returns:
        str: JSON ``{"status", "progress", "timed_out": bool, "task": {...}}``.
        When ``status`` is ``completed`` the ``task.clips`` array holds results.
    """
    deadline = time.monotonic() + timeout_seconds
    last: dict = {}
    while True:
        last = await _client().request("GET", f"/tasks/{task_id}")
        status = str(last.get("status", "unknown"))
        progress = last.get("progress", 0)

        if ctx is not None:
            try:
                await ctx.report_progress(
                    progress=float(progress or 0) / 100.0,
                    message=f"{status}: {last.get('progress_message', '')}",
                )
            except Exception:
                pass

        if status in TERMINAL_STATES:
            return _json(
                {"status": status, "progress": progress, "timed_out": False, "task": last}
            )

        if time.monotonic() >= deadline:
            return _json(
                {
                    "status": status,
                    "progress": progress,
                    "timed_out": True,
                    "message": f"Still '{status}' after {timeout_seconds}s; poll again later.",
                    "task": last,
                }
            )

        await asyncio.sleep(poll_interval_seconds)


@mcp.tool(
    name="supoclip_list_clips",
    annotations={
        "title": "List Clips",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_list_clips(
    task_id: Annotated[str, Field(description="The task id whose clips to list.", min_length=1)],
) -> str:
    """List the generated clips for a task. Requires authentication.

    Args:
        task_id: The task id.

    Returns:
        str: JSON ``{"task_id", "clips": [{"id", "filename", "start_time",
        "end_time", "virality_score", ...}], "total_clips": int}``.
    """
    data = await _client().request("GET", f"/tasks/{task_id}/clips")
    return _json(data)


# --------------------------------------------------------------------------- #
# Authenticated tools — retrieval (download to disk)
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="supoclip_download_clip",
    annotations={
        "title": "Download Clip",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_download_clip(
    task_id: Annotated[str, Field(description="The task id that owns the clip.", min_length=1)],
    clip_id: Annotated[str, Field(description="The clip id (from supoclip_list_clips).", min_length=1)],
    filename: Annotated[
        Optional[str],
        Field(default=None, description="Optional output filename; '.mp4' is enforced.", max_length=200),
    ] = None,
) -> str:
    """Download a generated clip's MP4 to the local download directory.

    The file is saved under ``SUPOCLIP_DOWNLOAD_DIR`` (default
    ``./supoclip-downloads``). Requires authentication and task ownership.

    Args:
        task_id: The owning task id.
        clip_id: The clip id to download.
        filename: Optional output filename.

    Returns:
        str: JSON ``{"path": str, "filename": str, "bytes": int}`` — the absolute
        local path of the saved MP4.
    """
    out_name = _safe_filename(filename or "", default=f"clip_{clip_id}.mp4")
    result = await _client().download(
        f"/tasks/{task_id}/clips/{clip_id}/file", SETTINGS.download_dir, out_name
    )
    return _json(result)


@mcp.tool(
    name="supoclip_export_clip",
    annotations={
        "title": "Export Clip (Platform Preset)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_export_clip(
    task_id: Annotated[str, Field(description="The task id that owns the clip.", min_length=1)],
    clip_id: Annotated[str, Field(description="The clip id to export.", min_length=1)],
    preset: Annotated[
        ExportPreset,
        Field(default="tiktok", description="Export preset: 'tiktok', 'reels' or 'shorts'."),
    ] = "tiktok",
    filename: Annotated[
        Optional[str],
        Field(default=None, description="Optional output filename; '.mp4' is enforced.", max_length=200),
    ] = None,
) -> str:
    """Export a clip re-encoded for a social platform and save the MP4 locally.

    Presets (tiktok/reels/shorts) produce 1080x1920 H.264 with platform-tuned
    bitrates. Saved under ``SUPOCLIP_DOWNLOAD_DIR``. Requires authentication.

    Args:
        task_id: The owning task id.
        clip_id: The clip id to export.
        preset: 'tiktok' | 'reels' | 'shorts'.
        filename: Optional output filename.

    Returns:
        str: JSON ``{"path": str, "filename": str, "bytes": int}``.
    """
    out_name = _safe_filename(filename or "", default=f"clip_{clip_id}_{preset}.mp4")
    result = await _client().download(
        f"/tasks/{task_id}/clips/{clip_id}/export",
        SETTINGS.download_dir,
        out_name,
        params={"preset": preset},
    )
    return _json(result)


# --------------------------------------------------------------------------- #
# Authenticated tools — management
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="supoclip_cancel_task",
    annotations={
        "title": "Cancel Task",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_cancel_task(
    task_id: Annotated[str, Field(description="The task id to cancel.", min_length=1)],
) -> str:
    """Cancel a queued or processing task. Requires authentication.

    Args:
        task_id: The task id to cancel.

    Returns:
        str: JSON ``{"message": str}``.
    """
    data = await _client().request("POST", f"/tasks/{task_id}/cancel")
    return _json(data)


@mcp.tool(
    name="supoclip_resume_task",
    annotations={
        "title": "Resume Task",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_resume_task(
    task_id: Annotated[str, Field(description="The task id to resume.", min_length=1)],
) -> str:
    """Re-queue a cancelled or errored task for processing. Requires authentication.

    Args:
        task_id: The task id to resume.

    Returns:
        str: JSON ``{"message": str, "job_id": str}``.
    """
    data = await _client().request("POST", f"/tasks/{task_id}/resume")
    return _json(data)


@mcp.tool(
    name="supoclip_delete_task",
    annotations={
        "title": "Delete Task",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
@tool_errors
async def supoclip_delete_task(
    task_id: Annotated[str, Field(description="The task id to delete.", min_length=1)],
) -> str:
    """Permanently delete a task and all of its generated clips.

    This cannot be undone. Requires authentication and task ownership.

    Args:
        task_id: The task id to delete.

    Returns:
        str: JSON ``{"message": str}``.
    """
    data = await _client().request("DELETE", f"/tasks/{task_id}")
    return _json(data)


def main() -> None:
    """Console-script entry point."""
    mcp.run(transport=SETTINGS.mcp_transport, mount_path=SETTINGS.mcp_mount_path)


if __name__ == "__main__":
    main()
