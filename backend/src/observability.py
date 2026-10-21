import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


TRACE_HEADER = "x-trace-id"
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")
_user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)

# "backend" or "worker"; set per container so logs and error reports say which
# process they came from.
SERVICE_NAME = os.getenv("SUPOCLIP_SERVICE", "backend")

_posthog_client: Any = None


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def set_trace_id(trace_id: str) -> None:
    _trace_id_ctx.set(trace_id)


def clear_trace_id() -> None:
    _trace_id_ctx.set("-")


def generate_trace_id() -> str:
    return uuid4().hex


def get_user_id() -> str | None:
    return _user_id_ctx.get()


def set_user_id(user_id: str | None) -> None:
    _user_id_ctx.set(user_id)


def bind_request_user(request: Any, user_id: str) -> None:
    """Attach the authenticated user to the request's logs and error reports.

    The request middleware runs outside the endpoint's context, so the id is
    also kept on ``request.state`` for it to pick up.
    """
    set_user_id(user_id)
    request.state.user_id = user_id


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        record.user_id = get_user_id()
        return True


class PostHogExceptionHandler(logging.Handler):
    """Sends logged exceptions (``logger.exception`` / ``exc_info=True``) to
    PostHog error tracking, tagged with the trace id that Axiom logs share."""

    def __init__(self, client: Any) -> None:
        super().__init__(level=logging.ERROR)
        self.client = client

    def emit(self, record: logging.LogRecord) -> None:
        # The PostHog client logs its own failures; never feed those back in.
        if not record.exc_info or record.name.startswith("posthog"):
            return
        exception = record.exc_info[1]
        if exception is None:
            return
        try:
            self.client.capture_exception(
                exception,
                distinct_id=getattr(record, "user_id", None),
                properties={
                    "service": SERVICE_NAME,
                    "trace_id": getattr(record, "trace_id", "-"),
                    "logger": record.name,
                    "log_message": record.getMessage(),
                },
            )
        except Exception:
            self.handleError(record)


def get_posthog_client() -> Any:
    """The shared PostHog client, or None when POSTHOG_PROJECT_TOKEN is unset."""
    global _posthog_client
    if _posthog_client is not None:
        return _posthog_client

    token = os.getenv("POSTHOG_PROJECT_TOKEN", "").strip()
    if not token:
        return None

    from posthog import Posthog

    _posthog_client = Posthog(
        token,
        host=os.getenv("POSTHOG_HOST", "https://eu.i.posthog.com"),
        enable_exception_autocapture=True,
        in_app_modules=["src"],
        super_properties={"platform": SERVICE_NAME},
    )
    return _posthog_client


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": SERVICE_NAME,
            "trace_id": getattr(record, "trace_id", "-"),
        }
        user_id = getattr(record, "user_id", None)
        if user_id:
            payload["user_id"] = user_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # YouTube download attempts. These names are stable so Axiom can sum
        # bytes per provider. Proxy URLs are never attached to the record.
        for key in (
            "youtube_provider",
            "youtube_video_id",
            "youtube_download_kind",
            "youtube_outcome",
            "youtube_bytes",
            "task_id",
        ):
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = value

        return json.dumps(payload, ensure_ascii=True)


def configure_logging() -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    formatter = JsonLogFormatter()
    trace_filter = TraceIdFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(trace_filter)

    app_file_handler = logging.FileHandler(logs_dir / "backend.log")
    app_file_handler.setLevel(log_level)
    app_file_handler.setFormatter(formatter)
    app_file_handler.addFilter(trace_filter)

    error_file_handler = logging.FileHandler(logs_dir / "backend-error.log")
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)
    error_file_handler.addFilter(trace_filter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(app_file_handler)
    root_logger.addHandler(error_file_handler)

    posthog_client = get_posthog_client()
    if posthog_client is not None:
        posthog_handler = PostHogExceptionHandler(posthog_client)
        posthog_handler.addFilter(trace_filter)
        root_logger.addHandler(posthog_handler)

    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
