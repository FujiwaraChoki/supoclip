import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media import transcription


class FakeModel:
    instances = []
    calls = []

    def __init__(self, *args, **kwargs):
        FakeModel.instances.append((args, kwargs))

    def transcribe(self, path, **kwargs):
        FakeModel.calls.append(kwargs)
        word = SimpleNamespace(word=" hi", start=1.25, end=1.75, probability=0.9)
        seg = SimpleNamespace(start=1.0, end=2.0, text=" hi", words=[word])
        return iter([seg]), SimpleNamespace(language="en")


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeModel.instances, FakeModel.calls = [], []
    monkeypatch.setattr(transcription, "_FasterWhisperModel", FakeModel)
    monkeypatch.setattr(transcription, "_FASTER_WHISPER_MODEL_CACHE", {})
    monkeypatch.delenv("WHISPER_VAD", raising=False)
    monkeypatch.delenv("WHISPER_BEAM_SIZE", raising=False)
    monkeypatch.delenv("WHISPER_ENGINE", raising=False)


def test_result_shape_and_defaults():
    r = transcription._transcribe_with_faster_whisper(Path("a.wav"), "base", "en")
    assert r["language"] == "en" and r["text"] == " hi"
    assert r["segments"][0]["words"][0] == {
        "word": " hi", "start": 1.25, "end": 1.75, "probability": 0.9,
    }
    kw = FakeModel.calls[0]
    assert kw["word_timestamps"] is True
    assert kw["vad_filter"] is True
    assert kw["beam_size"] == 1


def test_vad_disabled(monkeypatch):
    monkeypatch.setenv("WHISPER_VAD", "false")
    transcription._transcribe_with_faster_whisper(Path("a.wav"), "base", None)
    assert FakeModel.calls[0]["vad_filter"] is False


def test_transcribe_with_whisper_uses_faster(monkeypatch):
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda p: Path("a.wav"))
    r = transcription.transcribe_with_whisper(Path("v.mp4"), "base", "en_us")
    assert r["segments"] and FakeModel.calls[0]["language"] == "en"


def test_ms_conversion():
    r = transcription._transcribe_with_faster_whisper(Path("a.wav"), "base", "en")
    data = transcription._whisper_result_to_transcript_data(r)
    w = data["words"][0]
    assert (w["start"], w["end"]) == (1250, 1750)


def test_use_faster_flags(monkeypatch):
    assert transcription._use_faster_whisper() is True
    monkeypatch.setenv("WHISPER_ENGINE", "openai")
    assert transcription._use_faster_whisper() is False
    monkeypatch.delenv("WHISPER_ENGINE")
    monkeypatch.setattr(transcription, "_FasterWhisperModel", None)
    assert transcription._use_faster_whisper() is False


def test_model_cache():
    a = transcription._get_faster_whisper_model("base")
    b = transcription._get_faster_whisper_model("base")
    assert a is b and len(FakeModel.instances) == 1


def test_analysis_concurrency_env(monkeypatch):
    from src import ai

    try:
        monkeypatch.setenv("ANALYSIS_CONCURRENCY", "8")
        assert importlib.reload(ai).ANALYSIS_WINDOW_CONCURRENCY == 8
        monkeypatch.setenv("ANALYSIS_CONCURRENCY", "0")
        assert importlib.reload(ai).ANALYSIS_WINDOW_CONCURRENCY == 1
    finally:
        monkeypatch.delenv("ANALYSIS_CONCURRENCY", raising=False)
        importlib.reload(ai)
    assert ai.ANALYSIS_WINDOW_CONCURRENCY == 6


def test_x264_preset_env(monkeypatch):
    from src.media import common

    try:
        monkeypatch.setenv("FINAL_X264_PRESET", "slow")
        assert importlib.reload(common).FINAL_VIDEO_PRESET == "slow"
        monkeypatch.setenv("FINAL_X264_PRESET", "bogus")
        assert importlib.reload(common).FINAL_VIDEO_PRESET == "fast"
    finally:
        monkeypatch.delenv("FINAL_X264_PRESET", raising=False)
        importlib.reload(common)
    assert common.FINAL_VIDEO_PRESET == "fast"
