from types import SimpleNamespace

import pytest

import src.ai as ai


def _config(**overrides):
    base = dict(
        llm="custom:deepseek-v4.1-flash:free",
        rerank_llm=None,
        custom_llm_base_url="https://llm.example.com/v1/",
        custom_llm_api_key=None,
        openai_api_key=None,
        openrouter_api_key=None,
        openrouter_fallback_model="",
        google_api_key=None,
        anthropic_api_key=None,
        ollama_base_url=None,
        ollama_api_key=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_custom_is_a_supported_provider():
    assert "custom" in ai.SUPPORTED_LLM_PROVIDERS


def test_custom_model_id_keeps_colon_suffix():
    assert ai._split_llm_name("custom:deepseek-v4.1-flash:free") == (
        "custom",
        "deepseek-v4.1-flash:free",
    )


def test_custom_provider_needs_only_a_base_url():
    assert ai._get_missing_llm_key_error("custom:deepseek-v4.1-flash:free", _config()) is None


def test_custom_provider_requires_a_base_url():
    error = ai._get_missing_llm_key_error("custom:model", _config(custom_llm_base_url=None))

    assert error is not None
    assert "CUSTOM_LLM_BASE_URL" in error


def test_custom_provider_requires_a_model_name():
    error = ai._get_missing_llm_key_error("custom", _config())

    assert error is not None
    assert "model name" in error


@pytest.mark.parametrize("base_url", ["llm.example.com/v1", "ftp://gateway/v1"])
def test_custom_provider_rejects_non_http_base_url(base_url):
    error = ai._get_missing_llm_key_error(
        "custom:model", _config(custom_llm_base_url=base_url)
    )

    assert error is not None
    assert "http://" in error


@pytest.mark.parametrize("rerank_model", ["custom:rerank-model", "custom:vendor:model"])
def test_rerank_model_uses_the_same_custom_validation(rerank_model):
    assert ai._get_missing_llm_key_error(rerank_model, _config()) is None

    error = ai._get_missing_llm_key_error(
        rerank_model, _config(custom_llm_base_url=None)
    )
    assert error is not None
    assert "CUSTOM_LLM_BASE_URL" in error


def test_build_custom_model_wires_an_openai_compatible_provider(monkeypatch):
    calls = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            calls["provider"] = kwargs

    class FakeModel:
        def __init__(self, model_name, *, provider=None, **kwargs):
            calls["model"] = (model_name, provider)

    monkeypatch.setattr(ai, "OpenAIProvider", FakeProvider)
    monkeypatch.setattr(ai, "OpenAIChatModel", FakeModel)

    ai._build_transcript_model(_config())

    assert calls["provider"] == {
        "base_url": "https://llm.example.com/v1/",
        "api_key": None,
    }
    model_name, provider = calls["model"]
    assert model_name == "deepseek-v4.1-flash:free"
    assert isinstance(provider, FakeProvider)


def test_build_custom_model_forwards_the_api_key(monkeypatch):
    calls = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            calls["provider"] = kwargs

    monkeypatch.setattr(ai, "OpenAIProvider", FakeProvider)
    monkeypatch.setattr(ai, "OpenAIChatModel", lambda *a, **kw: None)

    ai._build_transcript_model(_config(custom_llm_api_key="sk-test"))

    assert calls["provider"]["api_key"] == "sk-test"


def test_build_custom_model_uses_the_rerank_model_name(monkeypatch):
    calls = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            calls["provider"] = kwargs

    class FakeModel:
        def __init__(self, model_name, *, provider=None, **kwargs):
            calls["model"] = (model_name, provider)

    monkeypatch.setattr(ai, "OpenAIProvider", FakeProvider)
    monkeypatch.setattr(ai, "OpenAIChatModel", FakeModel)

    ai._build_transcript_model(_config(), "custom:rerank-vendor:rerank-model")

    assert calls["model"][0] == "rerank-vendor:rerank-model"
