import pytest

from src import config
from src.config import Config


def test_default_ollama_base_url_uses_localhost_outside_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: False)

    assert Config._default_ollama_base_url() == "http://localhost:11434/v1"


def test_default_ollama_base_url_uses_host_gateway_in_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: path == "/.dockerenv")

    assert Config._default_ollama_base_url() == "http://host.docker.internal:11434/v1"


def test_youtube_duration_limit_uses_active_subscription_plan(monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_DURATION", "5400")
    monkeypatch.setenv("PRO_YOUTUBE_MAX_VIDEO_DURATION", "7200")
    monkeypatch.setenv("SCALE_YOUTUBE_MAX_VIDEO_DURATION", "10800")
    config = Config()

    assert config.max_youtube_video_duration_for_plan("free", "active") == 5400
    assert config.max_youtube_video_duration_for_plan("pro", "active") == 7200
    assert config.max_youtube_video_duration_for_plan("scale", "trialing") == 10800
    assert config.max_youtube_video_duration_for_plan("scale", "inactive") == 5400
    assert config.max_youtube_video_duration_for_plan("scale", "past_due", "stripe") == 10800
    assert config.max_youtube_video_duration_for_plan("scale", "past_due") == 5400
    assert config.max_youtube_video_duration_for_plan("scale", "unpaid", "stripe") == 5400


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 30.0),
        ("", 30.0),
        ("60", 60.0),
        ("45.5", 45.5),
        ("0", 30.0),
        ("-5", 30.0),
        ("nope", 30.0),
        ("inf", 30.0),
        ("999", 180.0),
    ],
)
def test_youtube_preflight_timeout_default_and_override(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS", raw)
    assert Config().youtube_preflight_timeout_seconds == expected


def test_hook_teaser_is_off_unless_requested(monkeypatch):
    monkeypatch.delenv("HOOK_FIRST_TEASER", raising=False)
    assert Config().hook_first_teaser is False
    monkeypatch.setenv("HOOK_FIRST_TEASER", "true")
    assert Config().hook_first_teaser is True


def test_whisper_accepts_documented_model_size_setting(monkeypatch):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "tiny")
    assert Config().whisper_model == "tiny"
    monkeypatch.setenv("WHISPER_MODEL", "base")
    assert Config().whisper_model == "base"
