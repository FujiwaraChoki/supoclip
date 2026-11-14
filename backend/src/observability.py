import json
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
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


_PROXY_USERINFO = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@")
_SIGNED_QUERY = re.compile(
    r"(?i)([?&](?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|Signature|sig))=[^&\s]+"
)


def scrub_log_text(text: str) -> str:
    """Remove proxy userinfo and signed query values from a log string."""
    text = _PROXY_USERINFO.sub(r"\1***:***@", text or "")
    return _SIGNED_QUERY.sub(r"\1=[redacted]", text)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_log_text(record.getMessage()),
            "service": SERVICE_NAME,
            "trace_id": getattr(record, "trace_id", "-"),
        }
        host = os.getenv("SUPOCLIP_LOG_HOST", "").strip()
        if host:
            payload["host"] = host
        user_id = getattr(record, "user_id", None)
        if user_id:
            payload["user_id"] = user_id

        if record.exc_info:
            payload["exception"] = scrub_log_text(self.formatException(record.exc_info))

        # YouTube download attempts. These names are stable so Axiom can sum
        # bytes per provider. Proxy URLs are never attached to the record.
        for key in (
            "youtube_provider",
            "youtube_video_id",
            "youtube_download_kind",
            "youtube_outcome",
            "youtube_bytes",
            "youtube_error_type",
            "youtube_fallback",
            "youtube_provider_fallback",
            "youtube_retry_attempt",
            "youtube_proxy_session_rotated",
            "task_id",
            # Task-creation source submissions. Values are sanitized; the raw
            # query string, fragment, and credentials are never attached.
            "source_user_id",
            "source_client",
            "source_type",
            "source_domain",
            "source_url",
            "source_outcome",
            "source_status",
            "source_reason",
            # Twitch/Kick stream downloads. Host only; no signed URL or proxy secret.
            "stream_stage",
            "stream_provider",
            "stream_source",
            "stream_kind",
            "stream_host",
            "stream_http_status",
            "stream_exc_type",
            "stream_bytes",
            "stream_elapsed_ms",
            "stream_attempt",
            "stream_fallback",
        ):
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = scrub_log_text(value) if isinstance(value, str) else value

        return json.dumps(payload, ensure_ascii=True)


_axiom_handler: "AxiomLogHandler | None" = None


class AxiomLogHandler(logging.Handler):
    """Ships JSON log lines to Axiom from a process that has no Vector collector.

    The main host keeps Vector and leaves ``AXIOM_TOKEN`` unset on the app, so
    this handler stays off there. A render worker sets the token and a host
    name. Delivery is batched on a daemon thread and never raises into the
    caller. The token is not written to the payload or to failure logs.
    """

    def __init__(
        self,
        *,
        dataset: str,
        token: str,
        url: str,
        host: str,
        sender: Callable[[list[dict[str, Any]]], None] | None = None,
        queue_size: int = 1000,
    ) -> None:
        super().__init__()
        self.dataset = dataset
        self._token = token
        self.url = url.rstrip("/")
        self.host = host
        self._sender = sender
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if sender is None:
            self._thread = threading.Thread(target=self._run, name="axiom-logs", daemon=True)
            self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        # Delivery failures are logged on this name. Never ship them back.
        if record.name == "src.observability.axiom":
            return
        try:
            payload = json.loads(self.format(record))
        except Exception:
            payload = {
                "message": scrub_log_text(record.getMessage()),
                "level": record.levelname,
                "service": SERVICE_NAME,
                "logger": record.name,
            }
        if not isinstance(payload, dict):
            return
        payload["host"] = payload.get("host") or self.host
        payload.setdefault("service", SERVICE_NAME)
        if payload.get("timestamp") and "_time" not in payload:
            payload["_time"] = payload["timestamp"]
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            return

    def drain(self) -> None:
        """Send everything currently queued. Tests use this instead of the thread."""
        batch: list[dict[str, Any]] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._deliver(batch)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            batch = [first]
            deadline = time.monotonic() + 1
            while len(batch) < 50 and time.monotonic() < deadline:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._deliver(batch)

    def _deliver(self, batch: list[dict[str, Any]]) -> None:
        try:
            if self._sender is not None:
                self._sender(batch)
            else:
                post_axiom_batch(self.url, self.dataset, self._token, batch)
        except Exception:
            logging.getLogger("src.observability.axiom").warning("Axiom log delivery failed")


def post_axiom_batch(url: str, dataset: str, token: str, batch: list[dict[str, Any]]) -> None:
    """POST one batch to Axiom's dataset ingest API. The body never contains the token."""
    endpoint = f"{url.rstrip('/')}/v1/datasets/{quote(dataset, safe='')}/ingest"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(batch).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        # The status is enough. Response bodies are not logged: they can echo
        # request details, and the token must not land in a log line.
        raise RuntimeError(f"Axiom ingest failed with HTTP {exc.code}") from None


def axiom_log_handler() -> AxiomLogHandler | None:
    """The process-wide Axiom handler, or None when ``AXIOM_TOKEN`` is unset."""
    global _axiom_handler
    token = os.getenv("AXIOM_TOKEN", "").strip()
    if not token:
        return None
    if _axiom_handler is not None:
        return _axiom_handler
    dataset = os.getenv("AXIOM_DATASET", "").strip() or "supoclip-prod"
    url = os.getenv("AXIOM_URL", "").strip() or "https://api.axiom.co"
    host = os.getenv("SUPOCLIP_LOG_HOST", "").strip() or socket.gethostname()
    _axiom_handler = AxiomLogHandler(dataset=dataset, token=token, url=url, host=host)
    _axiom_handler.setFormatter(JsonLogFormatter())
    return _axiom_handler


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

    app_file_handler = RotatingFileHandler(logs_dir / "backend.log", maxBytes=20 * 1024 * 1024, backupCount=10)
    app_file_handler.setLevel(log_level)
    app_file_handler.setFormatter(formatter)
    app_file_handler.addFilter(trace_filter)

    error_file_handler = RotatingFileHandler(logs_dir / "backend-error.log", maxBytes=10 * 1024 * 1024, backupCount=10)
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

    # Off unless AXIOM_TOKEN is set on this process. The main host ships logs
    # with Vector and does not set the token here.
    axiom_handler = axiom_log_handler()
    if axiom_handler is not None:
        axiom_handler.addFilter(trace_filter)
        root_logger.addHandler(axiom_handler)

    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
