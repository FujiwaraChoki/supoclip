from pydantic_ai.models.openai import OpenAIModel

from src import ai
from src.config import Config
from src.api.routes.admin import _setting_status


def test_nvidia_model_uses_nim_endpoint(monkeypatch):
    monkeypatch.setenv("LLM", "nvidia:meta/llama-3.3-70b-instruct")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    config = Config()

    model = ai._build_transcript_model(config)

    assert isinstance(model, OpenAIModel)
    assert model.model_name == "meta/llama-3.3-70b-instruct"
    assert model.client.base_url == ai.NVIDIA_API_BASE_URL + "/"
    assert model.client.api_key == "test-nvidia-key"


def test_nvidia_provider_requires_key(monkeypatch):
    monkeypatch.setenv("LLM", "nvidia:meta/llama-3.3-70b-instruct")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    config = Config()

    assert "NVIDIA_API_KEY" in ai._get_missing_llm_key_error(config.llm, config)


def test_nvidia_key_is_masked_in_admin_status():
    status = _setting_status("NVIDIA_API_KEY", {})

    assert status["input_type"] == "password"
    assert "test-nvidia-key" not in str(status)
