import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from src.api.routes import tasks
from src.observability import JsonLogFormatter
from src.source_submission import (
    client_label,
    log_task_create_validation_error,
    record_fields,
    record_source_submission,
    sanitize_source_url,
)


SECRET = "deadbeefSECRET"
PRIVATE = "PRIVATEKEY"


def test_client_label_recognizes_ios_and_treats_everything_else_as_web():
    assert client_label(None) == "web"
    assert client_label("Mozilla/5.0") == "web"
    assert client_label("supoclip-ios/not-a-version") == "web"
    assert client_label("SupoClip-iOS/3.1.4 (iPhone; iOS 18.0)") == "supoclip-ios/3.1.4"
    assert client_label("supoclip-ios/42") == "supoclip-ios/42"


def test_sanitizer_keeps_youtube_identifiers_and_drops_the_rest():
    raw = (
        "https://user:hunter2@www.youtube.com/watch?v=abcdefghijk"
        "&list=PLabcdefghij&t=1m2s&feature=share&si=trackingtoken"
        f"&X-Amz-Signature={SECRET}#t=10"
    )

    sanitized = sanitize_source_url(raw)

    assert sanitized.sanitized_url == (
        "https://www.youtube.com/watch?v=abcdefghijk&list=PLabcdefghij&t=1m2s"
    )
    assert sanitized.domain == "youtube.com"
    assert sanitized.source_type == "youtube"
    assert "hunter2" not in sanitized.sanitized_url
    assert SECRET not in sanitized.sanitized_url
    assert "trackingtoken" not in sanitized.sanitized_url
    assert "feature" not in sanitized.sanitized_url


def test_sanitizer_drops_signatures_credentials_and_fragments():
    drive = (
        "https://user:pass@drive.google.com/file/d/abc123/view"
        f"?usp=sharing&resourcekey={PRIVATE}&X-Goog-Signature={SECRET}#token=frag"
    )
    s3 = (
        "https://bucket.s3.amazonaws.com/clips/a.mp4"
        f"?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE"
        f"&X-Amz-Signature={SECRET}&X-Amz-Security-Token=sessiontoken"
    )

    drive_url = sanitize_source_url(drive)
    s3_url = sanitize_source_url(s3)

    assert drive_url.sanitized_url == "https://drive.google.com/file/d/abc123/view"
    assert drive_url.domain == "drive.google.com"
    assert drive_url.source_type == "unsupported"
    assert s3_url.sanitized_url == "https://bucket.s3.amazonaws.com/clips/a.mp4"
    assert "user:pass" not in drive_url.sanitized_url
    assert "@" not in drive_url.sanitized_url
    for value in (drive_url.sanitized_url, s3_url.sanitized_url):
        assert SECRET not in value
        assert PRIVATE not in value
        assert "AKIAEXAMPLE" not in value
        assert "sessiontoken" not in value
        assert "frag" not in value


def test_sanitizer_keeps_public_stream_identifiers_only():
    twitch = sanitize_source_url(
        f"https://www.twitch.tv/videos/123456?t=1h2m3s&token={SECRET}"
    )
    kick = sanitize_source_url(
        f"https://kick.com/someone?clip=clip_abc123XYZ&token={SECRET}"
    )
    tiktok = sanitize_source_url(
        f"https://www.tiktok.com/@creator/video/123?t=1&sig={SECRET}"
    )

    assert twitch.sanitized_url == "https://www.twitch.tv/videos/123456?t=1h2m3s"
    assert twitch.domain == "twitch.tv"
    assert twitch.source_type == "twitch"
    assert kick.sanitized_url == "https://kick.com/someone?clip=clip_abc123XYZ"
    assert kick.source_type == "kick"
    assert tiktok.sanitized_url == "https://www.tiktok.com/@creator/video/123"
    assert tiktok.domain == "tiktok.com"
    assert tiktok.source_type == "unsupported"
    assert SECRET not in twitch.sanitized_url + kick.sanitized_url + tiktok.sanitized_url


def test_sanitizer_does_not_keep_file_contents_or_local_paths():
    upload = sanitize_source_url(
        f"upload://s3/owner-id/upload/clip.mp4?X-Amz-Signature={SECRET}"
    )
    data_url = sanitize_source_url("data:video/mp4;base64,AAAASECRETFILE")
    local = sanitize_source_url("/etc/passwd")
    file_url = sanitize_source_url("file:///home/user/secret.mp4")
    broken = sanitize_source_url(f"https://evil.example/watch\n?X-Amz-Signature={SECRET}")

    assert upload.sanitized_url == "upload://clip.mp4"
    assert upload.domain == "upload"
    assert upload.source_type == "video_url"
    assert "owner-id" not in upload.sanitized_url
    assert data_url.sanitized_url == "data:"
    assert "AAAASECRETFILE" not in data_url.sanitized_url
    assert local.sanitized_url == "local-path"
    assert "/etc/passwd" not in local.sanitized_url
    assert file_url.sanitized_url == "file:"
    assert "secret.mp4" not in file_url.sanitized_url
    assert broken.sanitized_url == "https://evil.example/watch"
    assert SECRET not in broken.sanitized_url
    assert "\n" not in broken.sanitized_url


def test_rejection_reason_keeps_the_message_and_drops_billing_payload():
    fields = record_fields(
        raw_url=f"https://www.youtube.com/watch?v=abcdefghijk&token={SECRET}",
        user_id="user-1",
        user_agent="Mozilla/5.0",
        outcome="rejected",
        status_code=402,
        detail={
            "code": "SUBSCRIPTION_REQUIRED",
            "message": "Choose a paid plan to process videos.",
            "billing": {"email": "hidden@example.com"},
        },
    )

    assert fields is not None
    assert fields["reason"] == "402: Choose a paid plan to process videos."
    assert fields["client"] == "web"
    assert fields["sanitized_url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert SECRET not in fields["sanitized_url"]
    assert "hidden@example.com" not in fields["reason"]
    assert record_fields(
        raw_url="  ",
        user_id="user-1",
        user_agent=None,
        outcome="rejected",
        status_code=400,
        detail="Source URL is required",
    ) is None


def test_rejected_source_migration_declares_the_query_columns():
    sql = (
        Path(__file__).parents[2]
        / "src/migrations/sql/20261023_0001_rejected_source_attempts.sql"
    ).read_text()

    assert "CREATE TABLE IF NOT EXISTS rejected_source_attempts" in sql
    for column in ("user_id", "domain", "sanitized_url", "reason", "client", "created_at"):
        assert column in sql


class _RecordingSession:
    def __init__(self):
        self.params = None
        self.committed = False

    async def execute(self, _statement, params=None):
        self.params = params

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _submission_record(caplog):
    return next(record for record in caplog.records if record.message.startswith("source_submission"))


@pytest.mark.asyncio
async def test_rejected_submission_is_logged_and_persisted(monkeypatch, caplog):
    session = _RecordingSession()
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: session)
    caplog.set_level(logging.INFO)
    raw = f"https://www.tiktok.com/@creator/video/123?sig={SECRET}"

    await record_source_submission(
        raw_url=raw,
        user_id="user-1",
        user_agent="supoclip-ios/42 extra",
        outcome="rejected",
        status_code=422,
        detail="Only YouTube, Twitch VOD/clip, Kick VOD/clip, or uploaded video sources are supported",
        persist=True,
    )

    record = _submission_record(caplog)
    assert record.levelno == logging.WARNING
    assert record.source_outcome == "rejected"
    assert record.source_status == 422
    assert record.source_client == "supoclip-ios/42"
    assert record.source_type == "unsupported"
    assert record.source_domain == "tiktok.com"
    assert record.source_user_id == "user-1"
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["source_url"] == "https://www.tiktok.com/@creator/video/123"
    assert payload["source_reason"].startswith("422: Only YouTube")
    assert SECRET not in caplog.text
    assert SECRET not in json.dumps(payload)
    assert session.committed is True
    assert session.params["domain"] == "tiktok.com"
    assert session.params["sanitized_url"] == payload["source_url"]
    assert session.params["client"] == "supoclip-ios/42"
    assert session.params["user_id"] == "user-1"
    assert SECRET not in session.params["sanitized_url"]
    assert SECRET not in session.params["reason"]


@pytest.mark.asyncio
async def test_accepted_submission_is_logged_without_persisting(monkeypatch, caplog):
    called = False

    async def fail_if_called(_fields):
        nonlocal called
        called = True

    monkeypatch.setattr("src.source_submission._persist_rejection", fail_if_called)
    caplog.set_level(logging.INFO)

    await record_source_submission(
        raw_url="https://youtu.be/abcdefghijk?si=trackingtoken",
        user_id="user-1",
        user_agent="Mozilla/5.0",
        outcome="accepted",
        status_code=200,
        persist=True,
    )

    record = _submission_record(caplog)
    assert record.levelno == logging.INFO
    assert record.source_outcome == "accepted"
    assert record.source_url == "https://youtu.be/abcdefghijk"
    assert not hasattr(record, "source_reason")
    assert "trackingtoken" not in caplog.text
    assert called is False


@pytest.mark.asyncio
async def test_persist_failure_does_not_raise(monkeypatch, caplog):
    def broken_session():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("src.database.AsyncSessionLocal", broken_session)
    caplog.set_level(logging.INFO)

    await record_source_submission(
        raw_url="https://instagram.com/reel/ABC123/",
        user_id="user-1",
        user_agent=None,
        outcome="rejected",
        status_code=422,
        detail="unsupported",
        persist=True,
    )

    assert "Could not persist rejected source attempt: RuntimeError" in caplog.text
    assert "database unavailable" not in caplog.text
    assert _submission_record(caplog).source_domain == "instagram.com"


def _request(body: dict, user_agent: str = "supoclip-ios/42"):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={"user-agent": user_agent},
    )


@pytest.mark.asyncio
async def test_create_task_logs_unsupported_source_rejection(monkeypatch, caplog):
    session = _RecordingSession()
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr(
        tasks.BillingService,
        "assert_can_create_task",
        AsyncMock(return_value={"max_youtube_duration_seconds": 5400, "max_upload_duration_seconds": 5400}),
    )
    caplog.set_level(logging.INFO)
    raw = f"https://www.tiktok.com/@creator/video/123?sig={SECRET}"

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(_request({"source": {"url": raw}}), db=object())

    assert error.value.status_code == 422
    assert "Only YouTube" in error.value.detail
    record = _submission_record(caplog)
    assert record.source_outcome == "rejected"
    assert record.source_status == 422
    assert record.source_client == "supoclip-ios/42"
    assert record.source_domain == "tiktok.com"
    assert record.source_url == "https://www.tiktok.com/@creator/video/123"
    assert SECRET not in caplog.text
    assert session.params["sanitized_url"] == record.source_url
    assert session.params["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_create_task_logs_local_path_rejection_without_the_path(monkeypatch, caplog):
    session = _RecordingSession()
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr(
        tasks.BillingService,
        "assert_can_create_task",
        AsyncMock(return_value={"max_youtube_duration_seconds": 5400, "max_upload_duration_seconds": 5400}),
    )
    caplog.set_level(logging.INFO)

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(_request({"source": {"url": "/etc/passwd"}}, user_agent="Mozilla/5.0"), db=object())

    assert error.value.status_code == 404
    record = _submission_record(caplog)
    assert record.source_url == "local-path"
    assert record.source_status == 404
    assert record.source_client == "web"
    assert "/etc/passwd" not in caplog.text
    assert "/etc/passwd" not in session.params["sanitized_url"]
    assert "/etc/passwd" not in session.params["reason"]


@pytest.mark.asyncio
async def test_create_task_without_a_url_does_not_log_a_submission(monkeypatch, caplog):
    session = _RecordingSession()
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: session)
    caplog.set_level(logging.INFO)

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(_request({"source": {}}), db=object())

    assert error.value.status_code == 400
    assert session.params is None
    assert not any(record.message.startswith("source_submission") for record in caplog.records)


def _starlette_request(path: str, user_agent: str = "supoclip-ios/7") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"user-agent", user_agent.encode())],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }
    )


@pytest.mark.asyncio
async def test_task_validation_error_logs_sanitized_url_not_the_raw_input(monkeypatch, caplog):
    session = _RecordingSession()
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: session)
    caplog.set_level(logging.INFO)
    raw = (
        "https://user:pass@drive.google.com/file/d/abc123/view"
        f"?resourcekey={PRIVATE}&X-Goog-Signature={SECRET}"
    )
    exc = RequestValidationError(
        [{"type": "string_type", "loc": ("body", "font_options"), "msg": "bad", "input": raw}],
        body={"source": {"url": raw}, "font_options": 1},
    )

    await log_task_create_validation_error(_starlette_request("/tasks/"), exc)

    record = _submission_record(caplog)
    assert record.source_status == 422
    assert record.source_domain == "drive.google.com"
    assert record.source_client == "supoclip-ios/7"
    assert record.source_url == "https://drive.google.com/file/d/abc123/view"
    assert record.source_reason == "422: font_options: string_type"
    assert SECRET not in caplog.text
    assert PRIVATE not in caplog.text
    assert "pass@" not in caplog.text
    assert "Validation error:" not in caplog.text
    assert session.params["sanitized_url"] == record.source_url

    caplog.clear()
    await log_task_create_validation_error(_starlette_request("/fonts"), exc)
    assert not any(item.message.startswith("source_submission") for item in caplog.records)


@pytest.mark.asyncio
async def test_task_validation_handler_does_not_log_the_raw_body(monkeypatch, caplog):
    import src.config as config_module
    from src.config import Config, set_config_override
    from src.main import create_app

    session = _RecordingSession()
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: session)
    caplog.set_level(logging.INFO)
    raw = f"https://drive.google.com/open?id=file&X-Goog-Signature={SECRET}"
    exc = RequestValidationError(
        [{"type": "missing", "loc": ("body", "source", "url"), "msg": "Field required", "input": raw}],
        body={"source": {"url": raw}},
    )
    previous = config_module._config_override
    app = create_app(config=Config())
    try:
        response = await app.exception_handlers[RequestValidationError](
            _starlette_request("/tasks"),
            exc,
        )
    finally:
        set_config_override(previous)

    assert response.status_code == 422
    assert _submission_record(caplog).source_url == "https://drive.google.com/open"
    assert SECRET not in caplog.text
    assert "Validation error:" not in caplog.text
