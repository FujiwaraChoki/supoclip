"""Transient AssemblyAI failures are resubmitted; permanent ones are not."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from src.media import transcription


SERVER_ERROR = "Internal server error, please retry or contact support@assemblyai.com"
WORD = {"text": "Hello", "start": 0, "end": 400, "confidence": 0.99, "speaker": "A"}
UTTERANCE = {
    "text": "Hello there.",
    "start": 0,
    "end": 800,
    "confidence": 0.99,
    "speaker": "A",
    "words": [WORD],
}


def runtime(**overrides):
    values = {
        "assembly_ai_api_key": "test-key",
        "assembly_ai_http_timeout_seconds": 900,
        "assembly_ai_request_timeout_seconds": 30,
        "transcription_language": "",
        "transcription_provider": "assemblyai",
        "whisper_model": "base",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def job_body(job_id, status, error=None):
    body = {
        "id": job_id,
        "status": status,
        "audio_url": "https://cdn.assemblyai.com/upload/test",
    }
    if error is not None:
        body["error"] = error
    if status == "completed":
        body.update(
            text="Hello there.",
            language_code="en",
            words=[WORD],
            utterances=[UTTERANCE],
        )
    return body


def install_transport(monkeypatch, handler, tmp_path):
    media = tmp_path / "audio.mp3"
    media.write_bytes(b"audio-bytes")
    sleeps = []
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: media)
    monkeypatch.setattr(transcription.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(transcription.random, "uniform", lambda _start, _end: 0.0)
    actual = transcription.aai.Client

    def factory(**kwargs):
        client = actual(**kwargs)
        client.http_client.close()
        client._http_client = httpx.Client(
            base_url="https://api.assemblyai.com",
            transport=httpx.MockTransport(handler),
        )
        return client

    monkeypatch.setattr(transcription.aai, "Client", factory)
    return sleeps


def upload_ok():
    return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/test"})


def test_submit_5xx_then_success_resubmits_once(monkeypatch, tmp_path, caplog):
    posts = []

    def handler(request):
        if request.url.path == "/v2/upload":
            return upload_ok()
        if request.method == "POST":
            posts.append(request)
            if len(posts) == 1:
                return httpx.Response(500, json={"error": "upstream failure"})
            return httpx.Response(200, json=job_body("job-ok", "queued"))
        return httpx.Response(200, json=job_body("job-ok", "completed"))

    sleeps = install_transport(monkeypatch, handler, tmp_path)
    monkeypatch.setattr(transcription, "get_config", lambda: runtime())
    whisper = Mock(side_effect=AssertionError("whisper"))
    monkeypatch.setattr(transcription, "_get_transcript_with_whisper", whisper)

    with caplog.at_level(logging.WARNING, logger="src.media.common"):
        result = transcription.get_video_transcript(tmp_path / "video.mp4", "best")

    assert "Hello there." in result
    assert len(posts) == 2
    assert sleeps == [2.0]
    whisper.assert_not_called()
    assert "resubmitting" in caplog.text
    assert "https://" not in caplog.text


def test_internal_server_error_status_then_success(monkeypatch, tmp_path, caplog):
    posts = []

    def handler(request):
        if request.url.path == "/v2/upload":
            return upload_ok()
        if request.method == "POST":
            posts.append(request.url.path)
            job_id = f"job-{len(posts)}"
            return httpx.Response(200, json=job_body(job_id, "queued"))
        job_id = request.url.path.rsplit("/", 1)[-1]
        if job_id == "job-1":
            return httpx.Response(200, json=job_body(job_id, "error", SERVER_ERROR))
        return httpx.Response(200, json=job_body(job_id, "completed"))

    sleeps = install_transport(monkeypatch, handler, tmp_path)
    with caplog.at_level(logging.WARNING, logger="src.media.common"):
        result = transcription._get_transcript_with_assemblyai(
            tmp_path / "video.mp4", "universal", runtime()
        )

    assert "Hello there." in result
    assert posts == ["/v2/transcript", "/v2/transcript"]
    assert sleeps == [2.0]
    assert "resubmitting" in caplog.text
    assert "support@assemblyai.com" not in caplog.text
    assert "[email]" in caplog.text
    cache = (tmp_path / "video.transcript_cache.json").read_text()
    assert '"Hello"' in cache


def test_retries_exhausted_fall_back_to_whisper_word_timings(monkeypatch, tmp_path, caplog):
    posts = []

    def handler(request):
        if request.url.path == "/v2/upload":
            return upload_ok()
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(200, json=job_body(f"job-{len(posts)}", "queued"))
        return httpx.Response(200, json=job_body("job-err", "error", SERVER_ERROR))

    sleeps = install_transport(monkeypatch, handler, tmp_path)
    monkeypatch.setattr(transcription, "_whisper_fallback_ready", lambda _model: True)
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_whisper",
        Mock(return_value="[00:00 - 00:01] local word timings"),
    )

    with caplog.at_level(logging.WARNING, logger="src.media.common"):
        result = transcription._get_transcript_with_assemblyai(
            tmp_path / "video.mp4", "universal", runtime()
        )

    assert result == "[00:00 - 00:01] local word timings"
    assert len(posts) == 3
    assert sleeps == [2.0, 4.0]
    assert "falling back to Whisper" in caplog.text
    assert "support@assemblyai.com" not in caplog.text
    transcription._get_transcript_with_whisper.assert_called_once()


def test_retries_exhausted_without_whisper_is_a_transient_user_error(monkeypatch, tmp_path, caplog):
    def handler(request):
        if request.url.path == "/v2/upload":
            return upload_ok()
        if request.method == "POST":
            return httpx.Response(200, json=job_body("job-err", "queued"))
        return httpx.Response(200, json=job_body("job-err", "error", SERVER_ERROR))

    install_transport(monkeypatch, handler, tmp_path)
    monkeypatch.setattr(transcription, "_WHISPER_AVAILABLE", False)
    whisper = Mock(side_effect=AssertionError("whisper"))
    monkeypatch.setattr(transcription, "_get_transcript_with_whisper", whisper)

    with caplog.at_level(logging.ERROR, logger="src.media.common"):
        with pytest.raises(transcription.TransientTranscriptionError, match="temporarily unavailable") as error:
            transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime())

    whisper.assert_not_called()
    assert error.value.error_fingerprint == "assemblyai_transient"
    assert "support@" not in str(error.value)
    assert "Internal server error" not in str(error.value)
    assert "transient transcription failure" in caplog.text


@pytest.mark.parametrize(
    "message",
    [
        "The file does not contain any audio stream",
        "unsupported language for this file",
    ],
)
def test_permanent_transcript_error_does_not_retry(monkeypatch, tmp_path, caplog, message):
    posts = []

    def handler(request):
        if request.url.path == "/v2/upload":
            return upload_ok()
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(200, json=job_body("job-bad", "queued"))
        return httpx.Response(200, json=job_body("job-bad", "error", message))

    sleeps = install_transport(monkeypatch, handler, tmp_path)
    whisper = Mock(side_effect=AssertionError("whisper"))
    monkeypatch.setattr(transcription, "_get_transcript_with_whisper", whisper)

    with caplog.at_level(logging.ERROR, logger="src.media.common"):
        with pytest.raises(transcription.PermanentTranscriptionError) as error:
            transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime())

    assert len(posts) == 1
    assert sleeps == []
    whisper.assert_not_called()
    assert error.value.error_fingerprint == "assemblyai_permanent"
    assert "transcript" in str(error.value).lower()
    assert message not in str(error.value)
    assert "permanent transcription failure" in caplog.text
    assert "resubmitting" not in caplog.text


def test_auth_error_does_not_retry(monkeypatch, tmp_path):
    posts = []

    def handler(request):
        if request.url.path == "/v2/upload":
            return upload_ok()
        posts.append(request.method)
        return httpx.Response(401, json={"error": "Authentication error, API token invalid"})

    sleeps = install_transport(monkeypatch, handler, tmp_path)
    whisper = Mock(side_effect=AssertionError("whisper"))
    monkeypatch.setattr(transcription, "_get_transcript_with_whisper", whisper)

    with pytest.raises(transcription.PermanentTranscriptionError, match="authenticate") as error:
        transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime())

    assert posts == ["POST"]
    assert sleeps == []
    whisper.assert_not_called()
    assert error.value.error_fingerprint == "assemblyai_permanent"
    assert "token" not in str(error.value).lower()


def test_budget_exhausted_does_not_resubmit_or_fall_back(monkeypatch, tmp_path):
    calls = {"n": 0}

    def submit(*_args, **_kwargs):
        calls["n"] += 1
        clock["now"] = 10.0
        return SimpleNamespace(status=transcription.aai.TranscriptStatus.error, error=SERVER_ERROR)

    clock = {"now": 0.0}
    monkeypatch.setattr(transcription.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(transcription.time, "sleep", lambda _seconds: (_ for _ in ()).throw(AssertionError("sleep")))
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", submit)
    whisper = Mock(side_effect=AssertionError("whisper"))
    monkeypatch.setattr(transcription, "_whisper_word_timing_fallback", whisper)

    with pytest.raises(transcription.TransientTranscriptionError, match="temporarily unavailable"):
        transcription._get_transcript_with_assemblyai(
            tmp_path / "video.mp4",
            "universal",
            runtime(assembly_ai_http_timeout_seconds=10),
        )

    assert calls["n"] == 1
    whisper.assert_not_called()


def test_upload_retries_already_spent_fall_back_once(monkeypatch, tmp_path):
    helper = Mock(side_effect=RuntimeError("AssemblyAI audio upload unavailable after 3 attempts (HTTP 503)."))
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", helper)
    monkeypatch.setattr(transcription.time, "sleep", lambda _seconds: (_ for _ in ()).throw(AssertionError("sleep")))
    monkeypatch.setattr(transcription, "_whisper_fallback_ready", lambda _model: True)
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_whisper",
        Mock(return_value="[00:00 - 00:01] from whisper"),
    )

    result = transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime())

    assert result == "[00:00 - 00:01] from whisper"
    helper.assert_called_once()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("connection reset"), "resubmit"),
        (httpx.ReadTimeout("lost response"), "ambiguous"),
        (TimeoutError("budget"), "ambiguous"),
        (transcription.aai.types.TranscriptError("upstream", 500), "resubmit"),
        (transcription.aai.types.TranscriptError("slow down", 429), "resubmit"),
        (transcription.aai.types.TranscriptError("denied", 401), "permanent"),
        (transcription.aai.types.TranscriptError("bad audio", 400), "permanent"),
        (ValueError("not a provider failure"), None),
    ],
)
def test_exception_classification(error, expected):
    assert transcription._classify_assemblyai_exception(error) == expected


def test_backoff_is_exponential_with_jitter(monkeypatch):
    monkeypatch.setattr(transcription.random, "uniform", lambda _start, end: 0.0)
    assert transcription._assemblyai_backoff_seconds(1) == 2.0
    assert transcription._assemblyai_backoff_seconds(2) == 4.0
    assert transcription._assemblyai_backoff_seconds(5) == 30.0
    monkeypatch.setattr(transcription.random, "uniform", lambda _start, end: end)
    assert transcription._assemblyai_backoff_seconds(1) == 4.0
    assert transcription._assemblyai_backoff_seconds(2) == 8.0
    assert transcription._assemblyai_backoff_seconds(5) == 60.0


def test_whisper_fallback_requires_a_downloaded_model(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription, "_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(
        transcription,
        "_whisper",
        SimpleNamespace(_MODELS={"base": "https://example.com/checksum/base.pt"}),
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert transcription._whisper_fallback_ready("base") is False
    weights = tmp_path / "whisper" / "base.pt"
    weights.parent.mkdir()
    weights.write_bytes(b"weights")
    assert transcription._whisper_fallback_ready("base") is True
    assert transcription._whisper_fallback_ready(str(weights)) is True
    monkeypatch.setattr(transcription, "_WHISPER_AVAILABLE", False)
    assert transcription._whisper_fallback_ready("base") is False


def test_provider_detail_redacts_addresses_and_markup():
    detail = transcription._redact_provider_detail(
        "<html>upstream</html> https://cdn.example/secret support@assemblyai.com " + ("x" * 300)
    )
    assert "support@assemblyai.com" not in detail
    assert "https://cdn.example" not in detail
    assert "<html>" not in detail
    assert "[email]" in detail
    assert "[url]" in detail
    assert len(detail) <= 180
