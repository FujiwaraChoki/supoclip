from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

import httpx
import pytest

from src.media import transcription


def polling_job(monkeypatch, handler):
    client = httpx.Client(base_url="https://api.assemblyai.com", transport=httpx.MockTransport(handler))
    sdk_client = SimpleNamespace(http_client=client)
    submitted = SimpleNamespace(id="existing-job", _client=sdk_client)
    transcriber = SimpleNamespace(
        upload_file=Mock(return_value="https://cdn.assemblyai.com/audio"),
        submit=Mock(return_value=submitted),
    )
    # The real SDK response parser runs; only the final cache-rich Transcript is stubbed.
    monkeypatch.setattr(transcription.aai.Transcript, "from_response", lambda **kwargs: kwargs["response"])
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    return transcriber, client


def response(status):
    return httpx.Response(200, json={"id": "existing-job", "status": status, "audio_url": "https://example.com/audio.mp3"})


def test_poll_timeout_retries_same_job_and_uses_short_request_timeout(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return response("completed")
    transcriber, client = polling_job(monkeypatch, handler)
    try:
        result = transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert result.status == "completed"
    transcriber.submit.assert_called_once()
    assert all(str(r.url).endswith("/transcript/existing-job") for r in requests)
    assert all(r.extensions["timeout"]["read"] == 30 for r in requests)


def test_expired_budget_stops_before_another_poll(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return response("processing")
    transcriber, client = polling_job(monkeypatch, handler)
    clock = iter([0, 0, 1, 2])
    monkeypatch.setattr(transcription.time, "monotonic", lambda: next(clock))
    try:
        with pytest.raises(TimeoutError, match="within 2s"):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 2)
    finally:
        client.close()
    assert len(requests) == 1
    assert requests[0].extensions["timeout"]["read"] == 2
    transcriber.submit.assert_called_once()


def test_transcription_timeout_does_not_resubmit_paid_job(monkeypatch, tmp_path):
    runtime_config = SimpleNamespace(
        assembly_ai_api_key="test", assembly_ai_http_timeout_seconds=2, transcription_language=""
    )
    helper = Mock(side_effect=TimeoutError("deadline exceeded"))
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", helper)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    with pytest.raises(TimeoutError):
        transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime_config)
    helper.assert_called_once()


def test_upload_timeout_retries_before_single_paid_submission(monkeypatch):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    audio_url = "https://cdn.assemblyai.com/audio"
    transcriber.upload_file.side_effect = [httpx.ReadTimeout("upload timeout"), audio_url]
    try:
        transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert transcriber.upload_file.call_count == 2
    transcriber.submit.assert_called_once_with(audio_url, config=None)


def test_exhausted_upload_retries_do_not_submit_transcript(monkeypatch):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    transcriber.upload_file.side_effect = httpx.ConnectError("upload unavailable")
    try:
        with pytest.raises(RuntimeError, match="upload unavailable after 3 attempts"):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert transcriber.upload_file.call_count == 3
    transcriber.submit.assert_not_called()


def test_submission_timeout_is_not_retried(monkeypatch):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    transcriber.submit.side_effect = httpx.ReadTimeout("submission response lost")
    try:
        with pytest.raises(httpx.ReadTimeout):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    transcriber.upload_file.assert_called_once()
    transcriber.submit.assert_called_once()


@pytest.mark.parametrize(
    ("language", "expected_code", "expected_detection"),
    [("", None, True), ("es", "es", None)],
)
def test_language_is_detected_unless_configured(
    monkeypatch, tmp_path, language, expected_code, expected_detection
):
    runtime_config = SimpleNamespace(
        assembly_ai_api_key="test", assembly_ai_http_timeout_seconds=2, transcription_language=language
    )
    helper = Mock(side_effect=TimeoutError("stop after submit"))
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", helper)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    with pytest.raises(TimeoutError):
        transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime_config)
    config = helper.call_args.args[2]
    assert config.language_code == expected_code
    assert config.language_detection is expected_detection


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_sdk_transient_upload_errors_retry_before_single_submission(monkeypatch, status):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    transcriber.upload_file.side_effect = [
        transcription.aai.types.TranscriptError("upstream failure", status),
        "https://cdn.assemblyai.com/audio",
    ]
    try:
        transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert transcriber.upload_file.call_count == 2
    transcriber.submit.assert_called_once()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_sdk_permanent_upload_errors_do_not_retry(monkeypatch, status):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    transcriber.upload_file.side_effect = transcription.aai.types.TranscriptError("invalid request", status)
    try:
        with pytest.raises(transcription.aai.AssemblyAIError):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    transcriber.upload_file.assert_called_once()
    transcriber.submit.assert_not_called()


def test_exhausted_gateway_errors_are_safe_to_display_and_do_not_submit(monkeypatch):
    transcriber, client = polling_job(monkeypatch, lambda _: response("completed"))
    transcriber.upload_file.side_effect = transcription.aai.types.TranscriptError("<html>private upstream details</html>", 502)
    try:
        with pytest.raises(RuntimeError, match=r"after 3 attempts \(HTTP 502\)") as error:
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert "private" not in str(error.value)
    assert transcriber.upload_file.call_count == 3
    transcriber.submit.assert_not_called()


def test_status_503_retries_existing_job_without_resubmission(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(503) if len(requests) == 1 else response("completed")
    transcriber, client = polling_job(monkeypatch, handler)
    try:
        result = transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 900)
    finally:
        client.close()
    assert result.status == "completed"
    assert len(requests) == 2
    transcriber.submit.assert_called_once()


@pytest.mark.parametrize("alias", ["best", "universal", "universal-3-pro", "universal-3-5-pro", ""])
def test_quality_aliases_prefer_current_pro_model(alias):
    assert transcription._assemblyai_speech_models_value(alias) == ["universal-3-5-pro", "universal-2"]


@pytest.mark.parametrize("alias", ["nano", "universal-2"])
def test_explicit_value_model_remains_available(alias):
    assert transcription._assemblyai_speech_models_value(alias) == ["universal-2"]


def test_job_client_has_bounded_timeout_without_mutating_global_settings(monkeypatch, tmp_path):
    runtime = SimpleNamespace(assembly_ai_api_key="job-key", assembly_ai_http_timeout_seconds=900,
                              assembly_ai_request_timeout_seconds=45, transcription_language="")
    before = (transcription.aai.settings.api_key, transcription.aai.settings.http_timeout)
    clients = []
    actual_client = transcription.aai.Client
    def capture(**kwargs):
        client = actual_client(**kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(transcription.aai, "Client", capture)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", Mock(side_effect=TimeoutError("stop")))
    with pytest.raises(TimeoutError):
        transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "best", runtime)
    assert clients[0].settings.http_timeout == 45
    assert clients[0].settings.api_key == "job-key"
    assert clients[0].http_client.is_closed
    assert (transcription.aai.settings.api_key, transcription.aai.settings.http_timeout) == before



def test_repeated_status_503_stops_at_total_budget_without_resubmission(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(503)
    transcriber, client = polling_job(monkeypatch, handler)
    clock = iter([0, 0, 1, 2])
    monkeypatch.setattr(transcription.time, "monotonic", lambda: next(clock))
    try:
        with pytest.raises(TimeoutError, match="within 2s"):
            transcription._submit_and_wait_for_assemblyai_transcript(transcriber, Path("audio.mp3"), None, 2)
    finally:
        client.close()
    assert len(requests) == 1
    transcriber.submit.assert_called_once()


def test_real_sdk_retries_gateway_upload_and_submits_once(monkeypatch, tmp_path):
    import json
    calls = []
    attempts = 0
    def handler(request):
        nonlocal attempts
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == '/v2/upload':
            attempts += 1
            if attempts == 1:
                return httpx.Response(502, text='<html>Bad Gateway</html>')
            return httpx.Response(200, json={'upload_url': 'https://cdn.assemblyai.com/upload/test'})
        if request.method == 'POST':
            assert json.loads(request.content)['speech_models'] == ['universal-3-5-pro', 'universal-2']
            return response('queued')
        return response('completed')
    sdk_client = transcription.aai.Client(settings=transcription.aai.Settings(api_key='test'))
    sdk_client.http_client.close()
    sdk_client._http_client = httpx.Client(base_url='https://api.assemblyai.com', transport=httpx.MockTransport(handler))
    transcriber = transcription.aai.Transcriber(client=sdk_client)
    media = tmp_path / 'audio.mp3'
    media.write_bytes(b'complete-audio-bytes')
    monkeypatch.setattr(transcription.time, 'sleep', lambda _: None)
    try:
        config = transcription.aai.TranscriptionConfig(speech_models=transcription._assemblyai_speech_models_value('best'))
        result = transcription._submit_and_wait_for_assemblyai_transcript(transcriber, media, config, 900)
    finally:
        sdk_client.http_client.close()
    assert result.status == 'completed'
    uploads = [body for method, path, body in calls if path == '/v2/upload']
    assert uploads == [b'complete-audio-bytes', b'complete-audio-bytes']
    assert sum(method == 'POST' and path == '/v2/transcript' for method, path, _ in calls) == 1
