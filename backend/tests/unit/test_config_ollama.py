from src import config
from src.config import Config


def test_default_ollama_base_url_uses_localhost_outside_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: False)

    assert Config._default_ollama_base_url() == "http://localhost:11434/v1"


def test_default_ollama_base_url_uses_host_gateway_in_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: path == "/.dockerenv")

    assert Config._default_ollama_base_url() == "http://host.docker.internal:11434/v1"


def test_default_transcript_provider_prefers_assemblyai_when_key_is_set(monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_PROVIDER", raising=False)
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setenv("ASSEMBLY_AI_API_KEY", "assembly-test-key")
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "small")

    cfg = Config()

    assert cfg.transcript_provider == "assemblyai"
    assert cfg.whisper_model == "small"


def test_default_transcript_provider_falls_back_to_whisper_without_assemblyai(monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_PROVIDER", raising=False)
    monkeypatch.delenv("ASSEMBLY_AI_API_KEY", raising=False)
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "base")

    cfg = Config()

    assert cfg.transcript_provider == "whisper"
    assert cfg.whisper_model == "base"


def test_transcript_provider_normalizes_legacy_assembly_ai_name(monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_PROVIDER", "assembly_ai")
    monkeypatch.delenv("ASSEMBLY_AI_API_KEY", raising=False)

    cfg = Config()

    assert cfg.transcript_provider == "assemblyai"


def test_transcript_provider_normalizes_faster_whisper_name(monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_PROVIDER", "faster-whisper")
    monkeypatch.setenv("FASTER_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("FASTER_WHISPER_COMPUTE_TYPE", "float16")

    cfg = Config()

    assert cfg.transcript_provider == "faster_whisper"
    assert cfg.faster_whisper_device == "cuda"
    assert cfg.faster_whisper_compute_type == "float16"


def test_openai_base_url_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://vllm.local:8000/v1")

    cfg = Config()

    assert cfg.openai_base_url == "http://vllm.local:8000/v1"
    assert cfg.resolve_openai_base_url() == "http://vllm.local:8000/v1"
