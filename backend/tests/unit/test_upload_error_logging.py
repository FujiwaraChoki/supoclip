"""What the legacy /upload handler logs when an upload fails."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.routes import media

BOUNDARY = "testboundary"
PARTIAL_BODY = (
    f"--{BOUNDARY}\r\n"
    'Content-Disposition: form-data; name="video"; filename="private-name.mp4"\r\n'
    "Content-Type: video/mp4\r\n\r\n"
).encode() + b"x" * 4096
DECLARED_LENGTH = 10_000_000


def _upload_request(messages):
    async def receive():
        return messages.pop(0)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": [
            (b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode()),
            (b"content-length", str(DECLARED_LENGTH).encode()),
            (b"authorization", b"Bearer sk_secret_value"),
        ],
    }
    return Request(scope, receive)


@pytest.fixture
def upload_env(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "_get_authenticated_user_id", AsyncMock(return_value="owner"))
    config = SimpleNamespace(max_video_upload_bytes=DECLARED_LENGTH * 2, temp_dir=str(tmp_path))
    monkeypatch.setattr(media, "get_config", lambda: config)
    return tmp_path


async def test_disconnect_mid_body_is_logged_as_an_interrupted_upload(upload_env, caplog):
    request = _upload_request([
        {"type": "http.request", "body": PARTIAL_BODY, "more_body": True},
        {"type": "http.disconnect"},
    ])

    with caplog.at_level(logging.INFO, logger=media.logger.name):
        with pytest.raises(HTTPException) as raised:
            await media.upload_video(request, SimpleNamespace())

    assert raised.value.status_code == 499
    records = [r for r in caplog.records if r.name == media.logger.name]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    # No traceback: an interrupted upload is not a server error.
    assert record.exc_info is None
    message = record.getMessage()
    assert "connection closed" in message
    assert f"{len(PARTIAL_BODY)} of {DECLARED_LENGTH} bytes" in message
    assert "private-name" not in message
    assert "sk_secret_value" not in message


async def test_unexpected_error_logs_its_type_and_traceback(upload_env, monkeypatch, caplog):
    # An exception with no message used to log as "Error uploading video: ".
    monkeypatch.setattr(media, "publish_spooled_upload", AsyncMock(side_effect=OSError()))
    body = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="video"; filename="clip.mp4"\r\n'
        "Content-Type: video/mp4\r\n\r\n"
        "data\r\n"
        f"--{BOUNDARY}--\r\n"
    ).encode()
    request = _upload_request([{"type": "http.request", "body": body, "more_body": False}])
    request.scope["headers"][1] = (b"content-length", str(len(body)).encode())

    with caplog.at_level(logging.INFO, logger=media.logger.name):
        with pytest.raises(HTTPException) as raised:
            await media.upload_video(request, SimpleNamespace())

    assert raised.value.status_code == 500
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    record = errors[0]
    assert "OSError" in record.getMessage()
    assert record.exc_info is not None and isinstance(record.exc_info[1], OSError)
