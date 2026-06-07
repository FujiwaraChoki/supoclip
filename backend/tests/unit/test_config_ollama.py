from src import config
from src.config import Config


def test_transcription_provider_normalizes_local_whisper_alias():
    assert Config._normalize_transcription_provider("whisper") == "local_whisper"
    assert Config._normalize_transcription_provider("local-whisper") == "local_whisper"
    assert Config._normalize_transcription_provider("other") == "assemblyai"


def test_default_ollama_base_url_uses_localhost_outside_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: False)

    assert Config._default_ollama_base_url() == "http://localhost:11434/v1"


def test_default_ollama_base_url_uses_host_gateway_in_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: path == "/.dockerenv")

    assert Config._default_ollama_base_url() == "http://host.docker.internal:11434/v1"
