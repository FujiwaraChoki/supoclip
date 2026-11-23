"""Unit tests for Soniox async transcription provider."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from src.config import Config
from src.media import captions, transcription


def test_config_normalizes_soniox_provider_and_clamps_timeouts(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "Soniox")
    monkeypatch.setenv("SONIOX_API_KEY", "test-soniox-key")
    monkeypatch.setenv("SONIOX_MODEL", "stt-async-v6")
    monkeypatch.setenv("SONIOX_HTTP_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("SONIOX_REQUEST_TIMEOUT_SECONDS", "999")
    cfg = Config()
    assert cfg.transcription_provider == "soniox"
    assert cfg.soniox_api_key == "test-soniox-key"
    assert cfg.soniox_model == "stt-async-v6"
    assert cfg.soniox_http_timeout_seconds == 30
    assert cfg.soniox_request_timeout_seconds == 300
    assert cfg.as_runtime_settings()["SONIOX_API_KEY"] == "test-soniox-key"


def test_soniox_token_merger_handles_subwords_whitespace_punctuation_and_padding():
    payload = {
        "text": "All right, sleep is great! Yes.",
        "tokens": [
            {"text": "All", "start_ms": 100, "end_ms": 160, "confidence": 0.99, "speaker": "1", "language": "en"},
            # Standalone space token must flush the preceding word instead of gluing words.
            {"text": " ", "start_ms": 160, "end_ms": 160, "confidence": 1.0, "speaker": "1", "language": "en"},
            {"text": "right", "start_ms": 200, "end_ms": 260, "confidence": 0.95, "speaker": "1", "language": "en"},
            {"text": ",", "start_ms": 260, "end_ms": 320, "confidence": 0.99, "speaker": "1", "language": "en"},
            {"text": " sl", "start_ms": 500, "end_ms": 560, "confidence": 0.9, "speaker": "1", "language": "en"},
            {"text": "eep", "start_ms": 560, "end_ms": 620, "confidence": 0.96, "speaker": "1", "language": "en"},
            # Tight 60ms single-token word followed closely by next word.
            {"text": " is", "start_ms": 700, "end_ms": 760, "confidence": None, "speaker": "1", "language": "en"},
            # Zero-duration word (start == end) must still end cleanly without overlapping next word.
            {"text": " great", "start_ms": 800, "end_ms": 800, "confidence": 0.94, "speaker": "1", "language": "en"},
            {"text": "!", "start_ms": 800, "end_ms": 800, "confidence": 0.99, "speaker": "1", "language": "en"},
            # Speaker switch starts a new utterance.
            {"text": " Yes", "start_ms": 1500, "end_ms": 1560, "confidence": 0.99, "speaker": "2", "language": "en"},
            {"text": ".", "start_ms": 1560, "end_ms": 1620, "confidence": 0.99, "speaker": "2", "language": "en"},
        ],
    }

    data = transcription._soniox_result_to_transcript_data(payload, audio_duration_ms=2000)

    assert [w["text"] for w in data["words"]] == ["All", "right,", "sleep", "is", "great!", "Yes."]
    assert data["words"][3]["confidence"] == 1.0
    assert data["language_code"] == "en"
    assert len(data["utterances"]) == 2
    assert data["utterances"][0]["speaker"] == "1"
    assert data["utterances"][1]["speaker"] == "2"

    for prev, nxt in zip(data["words"], data["words"][1:]):
        assert prev["start"] <= prev["end"] <= nxt["start"]
    assert data["words"][4]["end"] == 800 + transcription.SONIOX_MIN_WORD_DURATION_MS
    assert data["words"][3]["end"] == 790
    assert data["words"][5]["end"] == 1500 + transcription.SONIOX_MIN_WORD_DURATION_MS


def test_soniox_token_merger_handles_no_space_scripts_and_cjk_punctuation():
    payload = {
        "text": "你好，世界。",
        "tokens": [
            {"text": "你", "start_ms": 100, "end_ms": 200, "confidence": 0.99, "speaker": "1", "language": "zh"},
            {"text": "好", "start_ms": 220, "end_ms": 320, "confidence": 0.99, "speaker": "1", "language": "zh"},
            {"text": "，", "start_ms": 320, "end_ms": 360, "confidence": 0.99, "speaker": "1", "language": "zh"},
            {"text": "世", "start_ms": 500, "end_ms": 600, "confidence": 0.98, "speaker": "1", "language": "zh"},
            {"text": "界", "start_ms": 620, "end_ms": 720, "confidence": 0.98, "speaker": "1", "language": "zh"},
            {"text": "。", "start_ms": 720, "end_ms": 760, "confidence": 0.99, "speaker": "1", "language": "zh"},
        ],
    }

    data = transcription._soniox_result_to_transcript_data(payload, audio_duration_ms=1000)
    assert [w["text"] for w in data["words"]] == ["你", "好，", "世", "界。"]
    assert data["language_code"] == "zh"

    # Japanese and Thai without spaces also split per token rather than collapsing,
    # and opening brackets do not attach to the preceding token.
    ja_payload = {
        "tokens": [
            {"text": "彼", "start_ms": 100, "end_ms": 180, "speaker": "1", "language": "ja"},
            {"text": "は", "start_ms": 180, "end_ms": 240, "speaker": "1", "language": "ja"},
            {"text": "「", "start_ms": 250, "end_ms": 290, "speaker": "1", "language": "ja"},
            {"text": "はい", "start_ms": 300, "end_ms": 420, "speaker": "1", "language": "ja"},
            {"text": "」", "start_ms": 420, "end_ms": 460, "speaker": "1", "language": "ja"},
            {"text": "。", "start_ms": 460, "end_ms": 500, "speaker": "1", "language": "ja"},
        ]
    }
    ja_data = transcription._soniox_result_to_transcript_data(ja_payload, audio_duration_ms=800)
    assert [w["text"] for w in ja_data["words"]] == ["彼", "は", "「", "はい」。"]


def test_soniox_token_merger_keeps_opening_quotes_and_apostrophes_separate():
    payload = {
        "tokens": [
            {"text": "He", "start_ms": 100, "end_ms": 160, "speaker": "1", "language": "en"},
            {"text": " said", "start_ms": 200, "end_ms": 260, "speaker": "1", "language": "en"},
            {"text": ' "', "start_ms": 300, "end_ms": 330, "speaker": "1", "language": "en"},
            {"text": "hi", "start_ms": 330, "end_ms": 420, "speaker": "1", "language": "en"},
            {"text": '"', "start_ms": 420, "end_ms": 450, "speaker": "1", "language": "en"},
            {"text": " just", "start_ms": 550, "end_ms": 620, "speaker": "1", "language": "en"},
            {"text": " '", "start_ms": 660, "end_ms": 690, "speaker": "1", "language": "en"},
            {"text": "cause", "start_ms": 690, "end_ms": 780, "speaker": "1", "language": "en"},
        ]
    }
    data = transcription._soniox_result_to_transcript_data(payload, audio_duration_ms=1000)
    assert [w["text"] for w in data["words"]] == ["He", "said", '"hi"', "just", "'cause"]
    assert data["words"][2]["start"] == 300
    assert data["words"][4]["start"] == 660


def test_soniox_token_merger_handles_french_spaced_punct_gaps_and_overlaps():
    payload = {
        "tokens": [
            {"text": "Pourquoi", "start_ms": 100, "end_ms": 500, "speaker": "1", "language": "fr"},
            # French spaced question mark attaches to previous word instead of orphan word.
            {"text": " ?", "start_ms": 500, "end_ms": 540, "speaker": "1", "language": "fr"},
            # Overlapping end on word 1 vs start on word 2 (450 < 500 -> clamped to 450).
            {"text": " reall", "start_ms": 450, "end_ms": 510, "speaker": "1", "language": "fr"},
            # Drawn-out vowel subword (450ms gap < 1000ms) stays merged into one word.
            {"text": "y", "start_ms": 960, "end_ms": 1020, "speaker": "1", "language": "fr"},
            # Long pause (>= 1000ms) forces a new word even without a leading space.
            {"text": "merci", "start_ms": 2200, "end_ms": 2400, "speaker": "1", "language": "fr"},
        ]
    }
    data = transcription._soniox_result_to_transcript_data(payload, audio_duration_ms=3000)
    assert [w["text"] for w in data["words"]] == ["Pourquoi?", "really", "merci"]
    assert data["words"][0]["end"] == 450
    assert data["words"][1]["start"] == 450
    assert data["words"][1]["end"] <= data["words"][2]["start"]


def test_soniox_upload_retries_polls_and_deletes_transcription_before_file(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    media = tmp_path / "audio.mp3"
    media.write_bytes(b"fake-mp3-data")

    calls: list[tuple[str, str]] = []
    upload_attempts = 0
    poll_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_attempts, poll_attempts
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/files":
            upload_attempts += 1
            if upload_attempts == 1:
                raise httpx.ReadTimeout("temporary upload timeout", request=request)
            return httpx.Response(200, json={"id": "file-123"})
        if request.method == "POST" and request.url.path == "/v1/transcriptions":
            body = json.loads(request.content)
            assert body["model"] == "stt-async-v6"
            assert body["file_id"] == "file-123"
            assert body["enable_speaker_diarization"] is True
            assert body["enable_language_identification"] is False
            assert body["language_hints"] == ["de"]
            assert body["language_hints_strict"] is True
            return httpx.Response(200, json={"id": "tx-456", "status": "queued"})
        if request.method == "GET" and request.url.path == "/v1/transcriptions/tx-456":
            poll_attempts += 1
            if poll_attempts == 1:
                return httpx.Response(503, text="temporarily unavailable")
            if poll_attempts == 2:
                return httpx.Response(200, json={"id": "tx-456", "status": "processing"})
            return httpx.Response(200, json={"id": "tx-456", "status": "completed", "audio_duration_ms": 3000})
        if request.method == "GET" and request.url.path == "/v1/transcriptions/tx-456/transcript":
            return httpx.Response(
                200,
                json={
                    "text": "Guten Tag.",
                    "tokens": [
                        {"text": "Guten", "start_ms": 100, "end_ms": 200, "confidence": 0.99, "speaker": "1", "language": "de"},
                        {"text": " Tag", "start_ms": 300, "end_ms": 400, "confidence": 0.98, "speaker": "1", "language": "de"},
                        {"text": ".", "start_ms": 400, "end_ms": 460, "confidence": 0.99, "speaker": "1", "language": "de"},
                    ],
                },
            )
        if request.method == "DELETE" and request.url.path in ("/v1/transcriptions/tx-456", "/v1/files/file-123"):
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with httpx.Client(base_url="https://api.soniox.com", transport=httpx.MockTransport(handler)) as client:
        transcript_payload, job = transcription._submit_and_wait_for_soniox_transcript(
            client, media, "stt-async-v6", "de_de", 900, 60
        )

    assert upload_attempts == 2
    assert sum(1 for m, p in calls if m == "POST" and p == "/v1/transcriptions") == 1
    assert poll_attempts == 3
    delete_paths = [p for m, p in calls if m == "DELETE"]
    assert delete_paths == ["/v1/transcriptions/tx-456", "/v1/files/file-123"]
    assert job["audio_duration_ms"] == 3000
    assert transcript_payload["text"] == "Guten Tag."


def test_soniox_deletes_transcription_and_file_when_job_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    media = tmp_path / "audio.mp3"
    media.write_bytes(b"fake-mp3-data")
    delete_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "file-err"})
        if request.method == "POST" and request.url.path == "/v1/transcriptions":
            return httpx.Response(200, json={"id": "tx-err", "status": "queued"})
        if request.method == "GET" and request.url.path == "/v1/transcriptions/tx-err":
            return httpx.Response(
                200,
                json={"id": "tx-err", "status": "error", "error_message": "corrupt audio"},
            )
        if request.method == "DELETE":
            delete_paths.append(request.url.path)
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with httpx.Client(base_url="https://api.soniox.com", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="corrupt audio"):
            transcription._submit_and_wait_for_soniox_transcript(
                client, media, "stt-async-v6", "", 60, 30
            )
    assert delete_paths == ["/v1/transcriptions/tx-err", "/v1/files/file-err"]


def test_soniox_polling_timeout_does_not_delete_active_transcription(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    clock = iter([0.0, 0.0, 2.5, 2.5])
    monkeypatch.setattr(transcription.time, "monotonic", lambda: next(clock))
    media = tmp_path / "audio.mp3"
    media.write_bytes(b"fake-mp3-data")
    delete_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "file-to"})
        if request.method == "POST" and request.url.path == "/v1/transcriptions":
            return httpx.Response(200, json={"id": "tx-to", "status": "processing"})
        if request.method == "GET" and request.url.path == "/v1/transcriptions/tx-to":
            return httpx.Response(200, json={"id": "tx-to", "status": "processing"})
        if request.method == "DELETE":
            delete_paths.append(request.url.path)
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with httpx.Client(base_url="https://api.soniox.com", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TimeoutError, match="within 2s"):
            transcription._submit_and_wait_for_soniox_transcript(
                client, media, "stt-async-v6", "", 2, 30
            )
    # Active job cannot be deleted on Soniox; only the uploaded file is cleaned up.
    assert delete_paths == ["/v1/files/file-to"]


@pytest.mark.parametrize("status_code", [401, 402])
def test_soniox_non_retryable_4xx_fails_immediately_without_retry(monkeypatch, tmp_path, status_code):
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    media = tmp_path / "audio.mp3"
    media.write_bytes(b"fake-mp3-data")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.method == "POST" and request.url.path == "/v1/files":
            attempts += 1
            if status_code == 401:
                return httpx.Response(401, json={"error": "invalid_api_key"})
            return httpx.Response(200, json={"id": "file-402"})
        if request.method == "POST" and request.url.path == "/v1/transcriptions":
            attempts += 1
            return httpx.Response(402, json={"error": "organization_balance_exhausted"})
        if request.method == "DELETE" and request.url.path == "/v1/files/file-402":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with httpx.Client(base_url="https://api.soniox.com", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            transcription._submit_and_wait_for_soniox_transcript(
                client, media, "stt-async-v6", "", 60, 30
            )
    assert exc_info.value.response.status_code == status_code
    assert attempts == (1 if status_code == 401 else 2)


def test_get_video_transcript_dispatches_to_soniox_and_writes_cache_for_captions(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")

    runtime = SimpleNamespace(
        transcription_provider="soniox",
        soniox_api_key="test-key",
        soniox_model="stt-async-v6",
        soniox_base_url="https://api.soniox.com",
        soniox_http_timeout_seconds=120,
        soniox_request_timeout_seconds=30,
        transcription_language="",
    )
    monkeypatch.setattr(transcription, "get_config", lambda: runtime)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda p: p)

    mock_payload = {
        "text": "Hello world. How are you?",
        "tokens": [
            {"text": "Hello", "start_ms": 500, "end_ms": 620, "confidence": 0.99, "speaker": "1", "language": "en"},
            {"text": " world", "start_ms": 800, "end_ms": 920, "confidence": 0.98, "speaker": "1", "language": "en"},
            {"text": ".", "start_ms": 920, "end_ms": 980, "confidence": 0.99, "speaker": "1", "language": "en"},
            {"text": " How", "start_ms": 1500, "end_ms": 1560, "confidence": 0.97, "speaker": "2", "language": "en"},
            {"text": " are", "start_ms": 1700, "end_ms": 1760, "confidence": 0.97, "speaker": "2", "language": "en"},
            {"text": " you", "start_ms": 1900, "end_ms": 1960, "confidence": 0.99, "speaker": "2", "language": "en"},
            {"text": "?", "start_ms": 1960, "end_ms": 2020, "confidence": 0.99, "speaker": "2", "language": "en"},
        ],
    }
    monkeypatch.setattr(
        transcription,
        "_submit_and_wait_for_soniox_transcript",
        Mock(return_value=(mock_payload, {"audio_duration_ms": 3000})),
    )

    formatted = transcription.get_video_transcript(video)
    assert "Speaker 1: Hello world." in formatted
    assert "Speaker 2: How are you?" in formatted

    cached = transcription.load_cached_transcript_data(video)
    assert cached is not None
    assert cached["language_code"] == "en"
    assert [w["text"] for w in cached["words"]] == ["Hello", "world.", "How", "are", "you?"]

    ass_path = tmp_path / "clip.ass"
    ok = captions.build_assemblyai_ass_subtitles(
        video_path=video,
        clip_start=0.0,
        clip_end=3.0,
        output_ass_path=ass_path,
        video_width=1080,
        video_height=1920,
    )
    assert ok is True
    ass_text = ass_path.read_text(encoding="utf-8")
    assert "Hello" in ass_text
    assert "world." in ass_text
