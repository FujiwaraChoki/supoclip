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
    transcriber = SimpleNamespace(submit=Mock(return_value=submitted))
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
    runtime_config = SimpleNamespace(assembly_ai_api_key="test", assembly_ai_http_timeout_seconds=2)
    helper = Mock(side_effect=TimeoutError("deadline exceeded"))
    monkeypatch.setattr(transcription, "_submit_and_wait_for_assemblyai_transcript", helper)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    with pytest.raises(TimeoutError):
        transcription._get_transcript_with_assemblyai(tmp_path / "video.mp4", "universal", runtime_config)
    helper.assert_called_once()
