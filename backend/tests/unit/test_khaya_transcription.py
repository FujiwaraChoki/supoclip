import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.config import Config
from src.media import transcription

REAL_HTTPX_CLIENT = httpx.Client


def khaya_config(**overrides):
    values = dict(
        khaya_api_key="test-key",
        khaya_base_url="https://khaya.test/asr/v3",
        khaya_language="twi",
        khaya_chunk_seconds=600,
        khaya_concurrency=2,
        khaya_requests_per_minute=600,
        khaya_request_timeout_seconds=30,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def word_payload(*words):
    return {
        "text": " ".join(w for w, _, _ in words),
        "timings": {
            "unit": "seconds",
            "granularity": "word",
            "segments": [],
            "words": [{"word": w, "start": s, "end": e} for w, s, e in words],
        },
    }


def mock_khaya(monkeypatch, tmp_path, handler, chunk_offsets=(0.0, 120.0)):
    """Stub audio prep/splitting and route Khaya HTTP calls to ``handler``.

    Re-splitting a failed chunk yields two sub-pieces whose bytes name their parent.
    """
    chunks = []
    for index, offset in enumerate(chunk_offsets):
        chunk = tmp_path / f"chunk_{index:05d}.mp3"
        chunk.write_bytes(f"audio-{index}".encode())
        chunks.append((chunk, offset))

    def split(path, seconds, chunk_dir=None):
        if chunk_dir is None:
            return chunks
        chunk_dir.mkdir(parents=True, exist_ok=True)
        pieces = []
        for index in range(2):
            piece = chunk_dir / f"chunk_{index:05d}.mp3"
            piece.write_bytes(path.read_bytes() + f"-part{index}".encode())
            pieces.append((piece, index * seconds))
        return pieces

    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "_split_audio_for_khaya", split)
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        transcription.httpx,
        "Client",
        lambda **kwargs: REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_config_accepts_khaya_provider():
    assert Config._normalize_transcription_provider("Khaya") == "khaya"


def test_words_are_offset_by_chunk_start_and_whole_seconds_stay_seconds():
    words = transcription._khaya_words_from_response(
        word_payload(("Akwaaba", 0, 0.5), ("Ghana", 2, 3)), offset=120.0
    )
    assert [(w["word"], w["start"], w["end"]) for w in words] == [
        (" Akwaaba", 120.0, 120.5),
        (" Ghana", 122.0, 123.0),
    ]
    # Integer seconds must not be mistaken for milliseconds by the cache writer.
    assert all(isinstance(w["start"], float) for w in words)


def test_segment_timings_are_spread_evenly_across_words():
    payload = {
        "text": "me pe sika",
        "timings": {"granularity": "segment", "words": [], "segments": [{"text": "me pe sika", "start": 3, "end": 6}]},
    }
    words = transcription._khaya_words_from_response(payload, offset=10.0)
    assert [(w["start"], w["end"]) for w in words] == [(13.0, 14.0), (14.0, 15.0), (15.0, 16.0)]


def test_transcribe_with_khaya_merges_chunks_and_caches_milliseconds(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.content == b"audio-0":
            return httpx.Response(200, json=word_payload(("Ɛte", 0, 0.4), ("sɛn?", 0.4, 1)))
        return httpx.Response(200, json=word_payload(("Amen.", 1, 1.5)))

    mock_khaya(monkeypatch, tmp_path, handler)
    video_path = tmp_path / "video.mp4"
    result = transcription.transcribe_with_khaya(video_path, khaya_config())

    assert all(r.headers["Ocp-Apim-Subscription-Key"] == "test-key" for r in requests)
    assert all(r.url.params["language"] == "twi" and r.url.params["timestamps"] == "word" for r in requests)
    assert all(str(r.url).startswith("https://khaya.test/asr/v3/transcribe") for r in requests)
    assert [s["text"] for s in result["segments"]] == ["Ɛte sɛn?", "Amen."]
    assert result["segments"][1]["start"] == 121.0

    transcription.cache_transcript_data(video_path, result)
    cached = json.loads(video_path.with_suffix(".transcript_cache.json").read_text())
    assert [(w["text"], w["start"], w["end"]) for w in cached["words"]] == [
        (" Ɛte", 0, 400),
        (" sɛn?", 400, 1000),
        (" Amen.", 121000, 121500),
    ]
    assert cached["language_code"] == "twi"


def test_falls_back_to_segment_timestamps_when_words_unsupported(monkeypatch, tmp_path):
    modes = []

    def handler(request):
        modes.append(request.url.params["timestamps"])
        if request.url.params["timestamps"] == "word":
            return httpx.Response(
                400,
                json={"error": {"code": "VALIDATION_FAILED", "details": [{"code": "UNSUPPORTED_TIMESTAMPS"}]}},
            )
        return httpx.Response(
            200,
            json={"text": "yɛ da wo ase", "timings": {"segments": [{"text": "yɛ da wo ase", "start": 0, "end": 2}], "words": []}},
        )

    mock_khaya(monkeypatch, tmp_path, handler)
    result = transcription.transcribe_with_khaya(tmp_path / "video.mp4", khaya_config())

    assert modes == ["word", "segment", "segment"]
    # One segment per chunk: the two-minute gap must not merge them.
    assert [len(s["words"]) for s in result["segments"]] == [4, 4]
    assert result["segments"][1]["start"] == 120.0


def test_retries_rate_limits_then_fails_on_bad_key(monkeypatch, tmp_path):
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=word_payload(("Yoo", 0, 0.3)))

    mock_khaya(monkeypatch, tmp_path, handler, chunk_offsets=(0.0,))
    result = transcription.transcribe_with_khaya(tmp_path / "video.mp4", khaya_config())
    assert len(attempts) == 2
    assert result["segments"][0]["text"] == "Yoo"

    mock_khaya(monkeypatch, tmp_path, lambda request: httpx.Response(401), chunk_offsets=(0.0,))
    with pytest.raises(RuntimeError, match="KHAYA_API_KEY"):
        transcription.transcribe_with_khaya(tmp_path / "other.mp4", khaya_config())


def test_resume_reuses_saved_pieces_and_only_sends_missing_ones(monkeypatch, tmp_path):
    sent = []

    def failing_second_chunk(request):
        sent.append(request.content)
        if request.content == b"audio-1":
            return httpx.Response(401)
        return httpx.Response(200, json=word_payload(("Akwaaba", 0, 0.5)))

    mock_khaya(monkeypatch, tmp_path, failing_second_chunk)
    video_path = tmp_path / "video.mp4"
    with pytest.raises(RuntimeError):
        transcription.transcribe_with_khaya(video_path, khaya_config())
    assert sent.count(b"audio-0") == 1

    sent.clear()
    mock_khaya(monkeypatch, tmp_path, lambda request: (sent.append(request.content), httpx.Response(200, json=word_payload(("Amen", 0, 0.5))))[1])
    result = transcription.transcribe_with_khaya(video_path, khaya_config())

    assert sent == [b"audio-1"]
    assert [s["text"] for s in result["segments"]] == ["Akwaaba", "Amen"]


def test_server_errors_split_the_chunk_and_skip_only_what_still_fails(monkeypatch, tmp_path):
    sent = []

    def handler(request):
        sent.append(request.content)
        if request.content in (b"audio-1", b"audio-1-part1"):
            return httpx.Response(500, json={"error": {"code": "SYSTEM_ERROR"}})
        return httpx.Response(200, json=word_payload(("Yesu", 0, 0.5)))

    mock_khaya(monkeypatch, tmp_path, handler, chunk_offsets=(0.0, 600.0, 1200.0))
    result = transcription.transcribe_with_khaya(tmp_path / "video.mp4", khaya_config())

    # Full chunk tried twice, then halves; the bad half is tried twice and skipped.
    assert sent.count(b"audio-1") == transcription.KHAYA_SERVER_ERROR_ATTEMPTS
    assert sent.count(b"audio-1-part0") == 1
    assert sent.count(b"audio-1-part1") == transcription.KHAYA_SERVER_ERROR_ATTEMPTS
    assert [s["start"] for s in result["segments"]] == [0.0, 600.0, 1200.0]

    # A resume reuses the saved halves and the remembered skip: no calls at all.
    sent.clear()
    mock_khaya(monkeypatch, tmp_path, handler, chunk_offsets=(0.0, 600.0, 1200.0))
    transcription.transcribe_with_khaya(tmp_path / "video.mp4", khaya_config())
    assert sent == []


def test_undecodable_piece_is_skipped_and_not_resent_on_resume(monkeypatch, tmp_path):
    sent = []

    def handler(request):
        sent.append(request.content)
        if request.content == b"audio-1":
            return httpx.Response(
                400,
                json={"error": {"code": "VALIDATION_FAILED", "details": [{"code": "INVALID_AUDIO_FORMAT"}]}},
            )
        return httpx.Response(200, json=word_payload(("Amen", 0, 0.5)))

    mock_khaya(monkeypatch, tmp_path, handler, chunk_offsets=(0.0, 600.0, 1200.0, 1800.0))
    video_path = tmp_path / "video.mp4"
    first = transcription.transcribe_with_khaya(video_path, khaya_config())
    assert sent.count(b"audio-1") == 1
    assert [s["start"] for s in first["segments"]] == [0.0, 1200.0, 1800.0]

    sent.clear()
    transcription.transcribe_with_khaya(video_path, khaya_config())
    assert sent == []


def test_too_many_skipped_pieces_fail_the_task(monkeypatch, tmp_path):
    mock_khaya(
        monkeypatch,
        tmp_path,
        lambda request: httpx.Response(500),
        chunk_offsets=(0.0,),
    )
    with pytest.raises(RuntimeError, match="a resume will only retry the rest"):
        transcription.transcribe_with_khaya(tmp_path / "video.mp4", khaya_config())


def test_rate_limiter_spaces_request_starts(monkeypatch):
    clock = {"now": 100.0}
    sleeps = []
    monkeypatch.setattr(transcription.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(transcription.time, "sleep", sleeps.append)

    limiter = transcription._KhayaRateLimiter(requests_per_minute=10)
    for _ in range(3):
        limiter.wait()

    assert sleeps == [6.0, 12.0]
    assert limiter.calls == 3


def test_missing_key_is_reported_before_any_work(tmp_path):
    with pytest.raises(RuntimeError, match="KHAYA_API_KEY"):
        transcription.transcribe_with_khaya(tmp_path / "video.mp4", khaya_config(khaya_api_key=None))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_split_audio_reports_real_chunk_offsets(tmp_path):
    audio = tmp_path / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=duration=25", "-ac", "1", str(audio)],
        capture_output=True,
        check=True,
    )
    chunks = transcription._split_audio_for_khaya(audio, chunk_seconds=10)

    assert [path.name for path, _ in chunks] == ["chunk_00000.mp3", "chunk_00001.mp3", "chunk_00002.mp3"]
    assert [round(offset) for _, offset in chunks] == [0, 10, 20]
    assert all(path.exists() and path.stat().st_size > 0 for path, _ in chunks)


def test_split_audio_drops_sub_second_tail(tmp_path, monkeypatch):
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    def fake_ffmpeg(command, timeout=900):
        (chunk_dir / "segments.csv").write_text(
            "chunk_00000.mp3,0.000000,120.024000\n"
            "chunk_00001.mp3,120.024000,240.012000\n"
            "chunk_00002.mp3,240.012000,240.045062\n"
        )
        for index in range(3):
            (chunk_dir / f"chunk_{index:05d}.mp3").write_bytes(b"x")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(transcription, "run_ffmpeg_command", fake_ffmpeg)
    chunks = transcription._split_audio_for_khaya(tmp_path / "audio.mp3", 120, chunk_dir)

    assert [path.name for path, _ in chunks] == ["chunk_00000.mp3", "chunk_00001.mp3"]
    assert not (chunk_dir / "chunk_00002.mp3").exists()
