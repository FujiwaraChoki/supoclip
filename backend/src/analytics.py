"""Product funnel events sent to PostHog.

The event catalog lives in ``docs/observability.md``. Everything here is off
unless ``POSTHOG_PROJECT_TOKEN`` is set, and nothing here may raise or block a
request: the PostHog client queues events and sends them from its own thread.

Properties never carry personal data or user content: no emails, names, source
URLs, transcripts, instructions or topics. The distinct id is the Better Auth
user id, the same id the web app identifies with.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Mapping, Optional

from .observability import get_posthog_client, get_trace_id

logger = logging.getLogger(__name__)

EventName = Literal[
    "task_created",
    "task_completed",
    "task_failed",
    "clip_exported",
    "social_post_published",
]

Client = Literal["web", "ios", "api_key", "mcp"]
SourceKind = Literal["youtube", "twitch", "kick", "upload", "video_url", "unknown"]

UPLOAD_URL_PREFIX = "upload://"
MCP_USER_AGENT_PREFIX = "supoclip-mcp"


def capture_event(
    event: EventName,
    distinct_id: Optional[str],
    properties: Optional[Mapping[str, Any]] = None,
    *,
    set_properties: Optional[Mapping[str, Any]] = None,
) -> None:
    """Send one product event. Never raises; a no-op without a token or a user."""
    if not distinct_id:
        return
    try:
        client = get_posthog_client()
        if client is None:
            return
        payload = {key: value for key, value in (properties or {}).items() if value is not None}
        trace_id = get_trace_id()
        if trace_id and trace_id != "-":
            payload.setdefault("trace_id", trace_id)
        if set_properties:
            payload["$set"] = dict(set_properties)
        client.capture(event, distinct_id=str(distinct_id), properties=payload)
    except Exception as exc:  # noqa: BLE001 - analytics must never break the caller
        # No traceback: an exception record would be reported to PostHog again.
        logger.warning("Could not capture %s: %s", event, type(exc).__name__)


def request_client(request: Any) -> Client:
    """Which client made the request: ``api_key``, ``mcp``, ``ios`` or ``web``.

    API keys win: the MCP server identifies itself by its user agent.
    """
    try:
        from .auth_headers import extract_api_key
        from .feature_flags import client_platform
        from .source_submission import client_label

        user_agent = (request.headers.get("user-agent") or "").strip()
        if extract_api_key(request):
            return "mcp" if user_agent.lower().startswith(MCP_USER_AGENT_PREFIX) else "api_key"
        if client_platform(request) == "ios" or client_label(user_agent) != "web":
            return "ios"
    except Exception:  # noqa: BLE001
        pass
    return "web"


def source_kind(source_type: Optional[str], url: Optional[str] = None) -> SourceKind:
    """A coarse label for the source. Uploads use the ``upload://`` scheme."""
    if isinstance(url, str) and url.strip().lower().startswith(UPLOAD_URL_PREFIX):
        return "upload"
    if source_type in ("youtube", "twitch", "kick", "video_url"):
        return source_type  # type: ignore[return-value]
    return "unknown"


def elapsed_seconds(started_at: Optional[datetime], ended_at: Optional[datetime] = None) -> Optional[float]:
    """Whole-ish seconds between two naive UTC datetimes, or None when unknown."""
    if started_at is None:
        return None
    try:
        end = ended_at or datetime.utcnow()
        return round(max(0.0, (end - started_at).total_seconds()), 1)
    except Exception:  # noqa: BLE001
        return None


def request_user_id(request: Any) -> Optional[str]:
    """The user the auth helpers bound to this request, if any."""
    user_id = getattr(getattr(request, "state", None), "user_id", None)
    return user_id if isinstance(user_id, str) and user_id else None


def capture_clip_export(
    request: Any,
    task: Any,
    *,
    surface: str,
    format: str,
    clip_count: Optional[int] = None,
) -> None:
    """``clip_exported`` when the task's owner downloads a file. Admin reads are skipped."""
    try:
        user_id = request_user_id(request)
        if not user_id or not isinstance(task, Mapping) or task.get("user_id") != user_id:
            return
        capture_event(
            "clip_exported",
            user_id,
            {
                "task_id": task.get("id"),
                "surface": surface,
                "format": format,
                "workflow": task.get("workflow") or "highlights",
                "clip_count": clip_count,
                "client": request_client(request),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not capture clip_exported: %s", type(exc).__name__)
