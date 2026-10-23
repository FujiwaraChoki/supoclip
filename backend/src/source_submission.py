"""Structured logs for task-creation source URLs.

Every submitted source is logged once. Rejected attempts are also stored in
``rejected_source_attempts`` so they can be queried without reading the raw
request body. Query strings, fragments, and credentials are removed; only a
few public content identifiers (YouTube ``v`` / ``t`` / ``list``, a VOD
timestamp, a Kick clip id) are kept.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

logger = logging.getLogger(__name__)

_IOS_CLIENT = re.compile(r"supoclip-ios/(\d{1,8}(?:\.\d{1,8}){0,4})", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https?://\S+", re.IGNORECASE)
_MAX_URL_LENGTH = 2000
_MAX_REASON_LENGTH = 500


@dataclass(frozen=True)
class SanitizedSource:
    sanitized_url: str
    domain: str
    source_type: str


def client_label(user_agent: str | None) -> str:
    """Return ``web`` or the iOS client token (``supoclip-ios/N``)."""
    if not user_agent:
        return "web"
    match = _IOS_CLIENT.search(user_agent)
    if not match:
        return "web"
    return f"supoclip-ios/{match.group(1)}"


def is_task_create_request(method: str, path: str) -> bool:
    normalized = (path or "").rstrip("/") or "/"
    return method.upper() == "POST" and normalized == "/tasks"


def sanitize_source_url(raw: str | None) -> SanitizedSource:
    """Keep scheme, host, and path. Drop secrets, fragments, and credentials."""
    text = _strip_controls(str(raw or "").strip())
    if len(text) > 8000:
        text = text[:8000]
    if not text:
        return SanitizedSource("", "unknown", "unsupported")

    lower = text.lower()
    if lower.startswith("upload://"):
        return _sanitize_upload(text)
    if lower.startswith("data:"):
        return SanitizedSource("data:", "data", "unsupported")
    if "://" not in text:
        return SanitizedSource("local-path", "local", "unsupported")

    scheme = re.sub(r"[^a-z0-9+.-]", "", lower.split(":", 1)[0])[:32] or "invalid"
    if scheme not in {"http", "https"}:
        return SanitizedSource(f"{scheme}:", scheme, "unsupported")
    return _sanitize_http(text, scheme)


def record_fields(
    *,
    raw_url: str | None,
    user_id: str | None,
    user_agent: str | None,
    outcome: str,
    status_code: int,
    detail: Any = None,
    allow_empty_url: bool = False,
) -> dict[str, Any] | None:
    """Build the structured fields for one submission, or None when nothing was submitted."""
    url = raw_url.strip() if isinstance(raw_url, str) else ""
    if not url and not allow_empty_url:
        return None

    described = sanitize_source_url(url) if url else SanitizedSource("", "unknown", "unsupported")
    reason = _rejection_reason(status_code, detail) if outcome == "rejected" else None
    safe_user = user_id.strip()[:255] if isinstance(user_id, str) and user_id.strip() else None
    return {
        "user_id": safe_user,
        "client": client_label(user_agent),
        "source_type": described.source_type,
        "domain": described.domain or "unknown",
        "sanitized_url": described.sanitized_url,
        "outcome": outcome,
        "status_code": int(status_code),
        "reason": reason,
    }


def log_source_submission(fields: dict[str, Any]) -> None:
    """Emit one JSON log line. The message itself is single-line and sanitized."""
    reason = fields.get("reason") or "-"
    extra = {
        "source_user_id": fields.get("user_id") or "-",
        "source_client": fields["client"],
        "source_type": fields["source_type"],
        "source_domain": fields["domain"],
        "source_url": fields["sanitized_url"] or "-",
        "source_outcome": fields["outcome"],
        "source_status": fields["status_code"],
    }
    if fields["outcome"] == "rejected" and fields.get("reason"):
        extra["source_reason"] = fields["reason"]
    level = logging.WARNING if fields["outcome"] == "rejected" else logging.INFO
    logger.log(
        level,
        "source_submission outcome=%s status=%s user_id=%s client=%s source_type=%s domain=%s url=%s reason=%s",
        fields["outcome"],
        fields["status_code"],
        fields.get("user_id") or "-",
        fields["client"],
        fields["source_type"],
        fields["domain"],
        fields["sanitized_url"] or "-",
        reason,
        extra=extra,
    )


async def record_source_submission(
    *,
    raw_url: str | None,
    user_id: str | None,
    user_agent: str | None,
    outcome: str,
    status_code: int,
    detail: Any = None,
    persist: bool = False,
    allow_empty_url: bool = False,
) -> None:
    """Log one submission. Persist only rejected attempts, and only when asked."""
    try:
        fields = record_fields(
            raw_url=raw_url,
            user_id=user_id,
            user_agent=user_agent,
            outcome=outcome,
            status_code=status_code,
            detail=detail,
            allow_empty_url=allow_empty_url,
        )
        if fields is None:
            return
        log_source_submission(fields)
        if persist and outcome == "rejected":
            await _persist_rejection(fields)
    except Exception as exc:
        # The exception text can echo the submitted URL, so keep only the type.
        logger.warning("source_submission log failed: %s", type(exc).__name__)


async def log_task_create_validation_error(request: Any, exc: Any) -> None:
    """Log a FastAPI/Pydantic validation failure for ``POST /tasks``."""
    if not is_task_create_request(
        getattr(request, "method", ""),
        str(getattr(getattr(request, "url", None), "path", "")),
    ):
        return
    state = getattr(request, "state", None)
    user_id = getattr(state, "user_id", None)
    headers = getattr(request, "headers", None)
    user_agent = headers.get("user-agent") if headers is not None and hasattr(headers, "get") else None
    await record_source_submission(
        raw_url=_url_from_validation_body(getattr(exc, "body", None)),
        user_id=user_id if isinstance(user_id, str) else None,
        user_agent=user_agent if isinstance(user_agent, str) else None,
        outcome="rejected",
        status_code=422,
        detail=_validation_reason(exc),
        persist=True,
        allow_empty_url=True,
    )


class SourceAttempt:
    """Tracks the source URL seen while handling one create-task request."""

    def __init__(self, request: Any, db: Any):
        self.db = db
        self.user_agent = _header(request, "user-agent")
        self.raw_url: str | None = None
        self.user_id: str | None = None
        self._finished = False

    def note_url(self, raw_url: Any) -> None:
        if isinstance(raw_url, str) and raw_url.strip():
            self.raw_url = raw_url.strip()

    def note_user(self, user_id: str | None) -> None:
        if isinstance(user_id, str) and user_id.strip():
            self.user_id = user_id.strip()

    async def accepted(self) -> None:
        await self._finish("accepted", 200, None)

    async def rejected(self, status_code: int, detail: Any) -> None:
        await self._finish("rejected", status_code, detail)

    async def _finish(self, outcome: str, status_code: int, detail: Any) -> None:
        if self._finished:
            return
        self._finished = True
        await record_source_submission(
            raw_url=self.raw_url,
            user_id=self.user_id,
            user_agent=self.user_agent,
            outcome=outcome,
            status_code=status_code,
            detail=detail,
            persist=outcome == "rejected" and self.db is not None,
        )


def _header(request: Any, name: str) -> str | None:
    headers = getattr(request, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    return value if isinstance(value, str) else None


def _strip_controls(value: str) -> str:
    return "".join(ch for ch in value if ord(ch) >= 32 and ord(ch) != 127)


def _sanitize_upload(url: str) -> SanitizedSource:
    """Log the file name only. Object keys can embed the owner id; contents stay unread."""
    without_query = url.split("#", 1)[0].split("?", 1)[0]
    name = unquote(without_query.rstrip("/").rsplit("/", 1)[-1])
    name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)[:80]
    if not name or name in {".", ".."} or name.lower() == "upload:":
        name = "file"
    return SanitizedSource(f"upload://{name}", "upload", "video_url")


def _sanitize_http(text: str, scheme: str) -> SanitizedSource:
    invalid = SanitizedSource(f"{scheme}://invalid", "invalid", "unsupported")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return invalid
    host = (parts.hostname or "").rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        return invalid
    if not _safe_host(host):
        return invalid

    if ":" in host:
        netloc = f"[{host}]"
    else:
        netloc = host
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"

    sanitized = urlunsplit((scheme, netloc, _safe_path(parts.path), _safe_query(host, parts.query), ""))
    if len(sanitized) > _MAX_URL_LENGTH:
        sanitized = sanitized[:_MAX_URL_LENGTH]
    return SanitizedSource(sanitized, _domain(host), _classify_source(text))


def _safe_host(host: str) -> bool:
    if not host or len(host) > 253 or ".." in host or host.startswith(".") or host.endswith("."):
        return False
    return all(ch.isalnum() or ch in ".-:" for ch in host)


def _domain(host: str) -> str:
    if host.startswith("www."):
        host = host[4:]
    return host[:255] or "unknown"


def _safe_path(path: str) -> str:
    decoded = _strip_controls(unquote(path or ""))
    if len(decoded) > 1024:
        decoded = decoded[:1024]
    pieces = [quote(piece, safe="@:$&'*+,;=-._~") for piece in decoded.split("/")]
    safe = "/".join(pieces)
    if decoded.startswith("/") and not safe.startswith("/"):
        safe = "/" + safe
    return safe


def _safe_query(host: str, query: str) -> str:
    domain = _domain(host)
    allowed: set[str] = set()
    if domain == "youtu.be" or domain.endswith("youtube.com") or domain.endswith("youtube-nocookie.com"):
        allowed = {"v", "t", "list"}
    elif domain.endswith("twitch.tv"):
        allowed = {"t"}
    elif domain == "kick.com" or domain.endswith(".kick.com"):
        allowed = {"t", "clip"}
    if not allowed or not query:
        return ""

    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in parse_qsl(query, keep_blank_values=False):
        normalized = key.lower()
        if normalized not in allowed or normalized in seen:
            continue
        if not _harmless_query_value(normalized, value):
            continue
        seen.add(normalized)
        kept.append((normalized, value))
    return urlencode(kept)


def _harmless_query_value(key: str, value: str) -> bool:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    if key == "v":
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", value))
    if key == "list":
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", value))
    if key == "t":
        return bool(re.fullmatch(r"\d{1,6}|(?:\d{1,4}[hms]){1,3}", value))
    if key == "clip":
        return bool(re.fullmatch(r"clip_[A-Za-z0-9_-]{1,80}", value))
    return False


def _classify_source(url: str) -> str:
    stripped = url.strip()
    lower = stripped.lower()
    if lower.startswith("upload://"):
        return "video_url"
    try:
        from .services.video_service import VideoService

        return VideoService.determine_source_type(stripped)
    except Exception:
        host = ""
        try:
            host = (urlsplit(stripped).hostname or "").lower().removeprefix("www.")
        except ValueError:
            host = ""
        if host == "youtu.be" or host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
            return "youtube"
        if host.endswith("twitch.tv"):
            return "twitch"
        if host == "kick.com" or host.endswith(".kick.com"):
            return "kick"
        return "unsupported"


def _rejection_reason(status_code: int, detail: Any) -> str:
    text = _reason_text(detail)
    combined = f"{status_code}: {text}" if text else str(status_code)
    return combined[:_MAX_REASON_LENGTH]


def _reason_text(detail: Any) -> str:
    if isinstance(detail, dict):
        raw = detail.get("message") or detail.get("code") or ""
        text = str(raw)
    elif detail is None:
        text = ""
    else:
        text = str(detail)

    def replace(match: re.Match[str]) -> str:
        sanitized = sanitize_source_url(match.group(0)).sanitized_url
        return sanitized or "[url]"

    text = _URL_IN_TEXT.sub(replace, text)
    return " ".join(text.split())[:300]


def _validation_reason(exc: Any) -> str:
    try:
        errors = exc.errors()
    except Exception:
        return "request validation failed"
    parts: list[str] = []
    for error in list(errors)[:5]:
        loc = ".".join(str(item) for item in error.get("loc", ()) if item != "body")
        kind = str(error.get("type") or "invalid")
        parts.append(f"{loc or 'body'}: {kind}"[:120])
    return "; ".join(parts) or "request validation failed"


def _url_from_validation_body(body: Any) -> str | None:
    if isinstance(body, (bytes, bytearray)):
        try:
            import json

            body = json.loads(body.decode("utf-8", errors="ignore"))
        except Exception:
            return None
    if isinstance(body, str):
        try:
            import json

            body = json.loads(body)
        except Exception:
            return None
    if not isinstance(body, dict):
        return None
    source = body.get("source")
    if isinstance(source, dict) and isinstance(source.get("url"), str):
        return source["url"]
    url = body.get("url")
    return url if isinstance(url, str) else None


async def _persist_rejection(fields: dict[str, Any]) -> None:
    try:
        from sqlalchemy import text

        from .database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO rejected_source_attempts (
                        id, user_id, domain, sanitized_url, reason, client, created_at
                    )
                    VALUES (
                        :id, :user_id, :domain, :sanitized_url, :reason, :client, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "user_id": fields.get("user_id"),
                    "domain": (fields.get("domain") or "unknown")[:255],
                    "sanitized_url": (fields.get("sanitized_url") or "")[:_MAX_URL_LENGTH],
                    "reason": (fields.get("reason") or "")[:_MAX_REASON_LENGTH],
                    "client": (fields.get("client") or "web")[:128],
                },
            )
            await session.commit()
    except Exception as exc:
        # Database errors quote the bound URL. Log the type only.
        logger.warning("Could not persist rejected source attempt: %s", type(exc).__name__)
