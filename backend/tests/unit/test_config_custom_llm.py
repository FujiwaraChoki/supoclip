from src import config
from src.config import Config
from src.runtime_settings import RUNTIME_SETTING_KEYS


def test_custom_llm_settings_come_from_env(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://llm.example.com/v1/")
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "sk-test")
    monkeypatch.setattr(config, "get_cached_setting", lambda name: None)
    monkeypatch.setattr(config, "setting_prefers_admin", lambda name: False)

    cfg = Config()

    assert cfg.custom_llm_base_url == "https://llm.example.com/v1/"
    assert cfg.custom_llm_api_key == "sk-test"


def test_empty_custom_llm_api_key_is_none(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "")
    monkeypatch.setattr(config, "get_cached_setting", lambda name: None)
    monkeypatch.setattr(config, "setting_prefers_admin", lambda name: False)

    assert Config().custom_llm_api_key is None


def test_admin_custom_llm_base_url_overrides_env_when_preferred(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://env.example.com/v1")
    values = {"CUSTOM_LLM_BASE_URL": "https://admin.example.com/v1"}
    monkeypatch.setattr(config, "get_cached_setting", lambda name: values.get(name))
    monkeypatch.setattr(config, "setting_prefers_admin", lambda name: name in values)

    assert Config().custom_llm_base_url == "https://admin.example.com/v1"


def test_env_wins_when_admin_value_is_not_preferred(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://env.example.com/v1")
    values = {"CUSTOM_LLM_BASE_URL": "https://admin.example.com/v1"}
    monkeypatch.setattr(config, "get_cached_setting", lambda name: values.get(name))
    monkeypatch.setattr(config, "setting_prefers_admin", lambda name: False)

    assert Config().custom_llm_base_url == "https://env.example.com/v1"


def test_custom_llm_settings_are_admin_editable():
    assert "CUSTOM_LLM_BASE_URL" in RUNTIME_SETTING_KEYS
    assert "CUSTOM_LLM_API_KEY" in RUNTIME_SETTING_KEYS
