"""The render worker ships its own logs when Axiom env vars are set."""

import json
import logging

import pytest

from src import observability


def _record(message, *, name="src.worker"):
    record = logging.LogRecord(name, logging.INFO, __file__, 1, message, None, None)
    observability.TraceIdFilter().filter(record)
    return record


def test_axiom_handler_is_off_without_a_token(monkeypatch):
    monkeypatch.delenv("AXIOM_TOKEN", raising=False)
    monkeypatch.setattr(observability, "_axiom_handler", None)
    assert observability.axiom_log_handler() is None


def test_axiom_batch_scrubs_proxy_secrets_and_tags_host(monkeypatch):
    monkeypatch.setenv("SUPOCLIP_LOG_HOST", "render-1")
    sent = []
    handler = observability.AxiomLogHandler(
        dataset="supoclip-prod",
        token="xaat-secret-token",
        url="https://api.axiom.co",
        host="render-1",
        sender=sent.append,
    )
    handler.setFormatter(observability.JsonLogFormatter())
    handler.emit(_record(
        "download failed proxy=http://user:super-secret-password@gw.example:823 "
        "url=https://rr.googlevideo.com/videoplayback?sig=secret&X-Amz-Signature=abc"
    ))
    handler.drain()

    [batch] = sent
    body = json.dumps(batch)
    assert batch[0]["host"] == "render-1"
    assert batch[0]["service"] == observability.SERVICE_NAME
    assert batch[0]["_time"] == batch[0]["timestamp"]
    assert "super-secret-password" not in body
    assert "sig=secret" not in body
    assert "X-Amz-Signature=abc" not in body
    assert "xaat-secret-token" not in body


def test_axiom_request_keeps_the_token_out_of_the_body(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b""

    def urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["body"] = request.data.decode()
        return Response()

    monkeypatch.setattr(observability.urllib.request, "urlopen", urlopen)
    observability.post_axiom_batch(
        "https://api.axiom.co",
        "supoclip-prod",
        "xaat-secret-token",
        [{"message": "worker ready", "service": "worker", "host": "render-1"}],
    )
    assert captured["url"] == "https://api.axiom.co/v1/datasets/supoclip-prod/ingest"
    assert captured["authorization"] == "Bearer xaat-secret-token"
    assert "xaat-secret-token" not in captured["body"]
    assert json.loads(captured["body"])[0]["host"] == "render-1"


def test_axiom_http_failure_does_not_include_the_response_body(monkeypatch):
    import urllib.error

    def urlopen(_request, timeout=0):
        raise urllib.error.HTTPError(
            "https://api.axiom.co/v1/datasets/supoclip-prod/ingest",
            401,
            "Unauthorized",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(observability.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="HTTP 401") as raised:
        observability.post_axiom_batch("https://api.axiom.co", "supoclip-prod", "xaat-secret-token", [])
    assert "xaat-secret-token" not in str(raised.value)
    assert "Unauthorized" not in str(raised.value)


def test_log_host_is_omitted_until_configured(monkeypatch):
    monkeypatch.delenv("SUPOCLIP_LOG_HOST", raising=False)
    payload = json.loads(observability.JsonLogFormatter().format(_record("ready")))
    assert "host" not in payload
    monkeypatch.setenv("SUPOCLIP_LOG_HOST", "render-1")
    payload = json.loads(observability.JsonLogFormatter().format(_record("ready")))
    assert payload["host"] == "render-1"
    assert payload["service"] == observability.SERVICE_NAME


def test_full_queue_drops_instead_of_blocking():
    handler = observability.AxiomLogHandler(
        dataset="supoclip-prod",
        token="xaat-secret-token",
        url="https://api.axiom.co",
        host="render-1",
        sender=lambda _batch: None,
        queue_size=1,
    )
    handler.setFormatter(observability.JsonLogFormatter())
    handler.emit(_record("one"))
    handler.emit(_record("two"))
    assert handler._queue.qsize() == 1
