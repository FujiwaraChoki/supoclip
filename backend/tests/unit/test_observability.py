import json
import logging

from src import observability


class FakePostHog:
    def __init__(self):
        self.calls = []

    def capture_exception(self, exception, **kwargs):
        self.calls.append((exception, kwargs))


def _record(name="src.test", exc_info=None):
    record = logging.LogRecord(name, logging.ERROR, __file__, 1, "boom", None, exc_info)
    observability.TraceIdFilter().filter(record)
    return record


def test_logs_carry_service_trace_and_user():
    observability.set_trace_id("trace-1")
    observability.set_user_id("user-1")
    try:
        payload = json.loads(observability.JsonLogFormatter().format(_record()))
    finally:
        observability.clear_trace_id()
        observability.set_user_id(None)

    assert payload["trace_id"] == "trace-1"
    assert payload["user_id"] == "user-1"
    assert payload["service"] == observability.SERVICE_NAME


def test_logs_omit_user_when_unauthenticated():
    payload = json.loads(observability.JsonLogFormatter().format(_record()))
    assert "user_id" not in payload


def test_logged_exceptions_go_to_posthog_with_trace_and_user():
    client = FakePostHog()
    handler = observability.PostHogExceptionHandler(client)
    error = ValueError("bad")
    observability.set_trace_id("trace-2")
    observability.set_user_id("user-2")
    try:
        handler.emit(_record(exc_info=(ValueError, error, None)))
    finally:
        observability.clear_trace_id()
        observability.set_user_id(None)

    [(captured, kwargs)] = client.calls
    assert captured is error
    assert kwargs["distinct_id"] == "user-2"
    assert kwargs["properties"]["trace_id"] == "trace-2"
    assert kwargs["properties"]["logger"] == "src.test"


def test_plain_errors_and_posthog_own_logs_are_not_captured():
    client = FakePostHog()
    handler = observability.PostHogExceptionHandler(client)
    error = ValueError("bad")

    handler.emit(_record())
    handler.emit(_record(name="posthog.client", exc_info=(ValueError, error, None)))

    assert client.calls == []


def test_posthog_disabled_without_token(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.setattr(observability, "_posthog_client", None)
    assert observability.get_posthog_client() is None
