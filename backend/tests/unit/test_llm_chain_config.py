"""The chain of models and the keys of the providers."""

from src.config import Config


def config_with(**attributes) -> Config:
    config = Config()
    config.llm = "google-gla:gemini-3-flash-preview"
    config.llm_fallbacks = ""
    config.llm_provider_keys = ""
    config.google_api_key = ""
    config.openrouter_api_key = ""
    config.openai_api_key = ""
    config.anthropic_api_key = ""
    config.ollama_api_key = ""
    for name, value in attributes.items():
        setattr(config, name, value)
    return config


def test_the_chain_starts_with_the_main_model():
    config = config_with(llm_fallbacks="google-gla:gemini-2.5-flash")

    assert config.llm_chain() == (
        "google-gla:gemini-3-flash-preview",
        "google-gla:gemini-2.5-flash",
    )


def test_the_chain_keeps_the_order_and_drops_the_repeats():
    config = config_with(
        llm_fallbacks=(
            " google-gla:gemini-2.5-flash , google-gla:gemini-2.5-flash-lite ,, "
            "google-gla:gemini-3-flash-preview "
        )
    )

    assert config.llm_chain() == (
        "google-gla:gemini-3-flash-preview",
        "google-gla:gemini-2.5-flash",
        "google-gla:gemini-2.5-flash-lite",
    )


def test_the_chain_of_a_deployment_without_fallbacks_is_the_main_model():
    config = config_with()

    assert config.llm_chain() == ("google-gla:gemini-3-flash-preview",)


def test_the_stored_keys_are_read_from_the_json_setting():
    config = config_with(
        llm_provider_keys='{"google": ["key-1", "key-2"], "openrouter": "key-3"}'
    )

    stored = config.stored_provider_keys()

    assert stored["google"] == ["key-1", "key-2"]
    assert stored["openrouter"] == ["key-3"]


def test_a_broken_json_setting_is_ignored():
    config = config_with(llm_provider_keys="{not json")

    assert config.stored_provider_keys() == {}


def test_the_keys_of_a_provider_start_with_the_stored_ones():
    config = config_with(
        llm_provider_keys='{"google": ["key-1", "key-2"]}',
        google_api_key="key-from-the-environment",
    )

    assert config.provider_keys("google") == ["key-1", "key-2", "key-from-the-environment"]


def test_the_key_of_the_environment_is_not_repeated():
    config = config_with(
        llm_provider_keys='{"google": ["key-1"]}',
        google_api_key="key-1",
    )

    assert config.provider_keys("google") == ["key-1"]


def test_a_provider_without_keys_has_none():
    config = config_with(llm_provider_keys='{"google": ["key-1"]}')

    assert config.provider_keys("openrouter") == []
