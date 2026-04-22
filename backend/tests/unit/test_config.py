"""Tests for ``src/config.py`` helpers and Config construction."""

import pytest

from src.config import Config, get_config, set_config_override


class TestGetOptionalEnv:
    def test_absent_returns_none(self, monkeypatch):
        monkeypatch.delenv("X_TEST_VAR", raising=False)
        assert Config._get_optional_env("X_TEST_VAR") is None

    def test_blank_returns_none(self, monkeypatch):
        monkeypatch.setenv("X_TEST_VAR", "   ")
        assert Config._get_optional_env("X_TEST_VAR") is None

    def test_returns_stripped_value(self, monkeypatch):
        monkeypatch.setenv("X_TEST_VAR", "  hello  ")
        assert Config._get_optional_env("X_TEST_VAR") == "hello"


class TestGetBoolEnv:
    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes"])
    def test_truthy(self, monkeypatch, value):
        monkeypatch.setenv("X_BOOL", value)
        assert Config._get_bool_env("X_BOOL", default=False) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE"])
    def test_falsy(self, monkeypatch, value):
        monkeypatch.setenv("X_BOOL", value)
        assert Config._get_bool_env("X_BOOL", default=True) is False

    def test_unknown_uses_default(self, monkeypatch):
        monkeypatch.setenv("X_BOOL", "maybe")
        assert Config._get_bool_env("X_BOOL", default=True) is True
        assert Config._get_bool_env("X_BOOL", default=False) is False

    def test_absent_uses_default(self, monkeypatch):
        monkeypatch.delenv("X_BOOL", raising=False)
        assert Config._get_bool_env("X_BOOL", default=True) is True


class TestGetCsvEnv:
    def test_absent_returns_default(self, monkeypatch):
        monkeypatch.delenv("X_CSV", raising=False)
        assert Config._get_csv_env("X_CSV", ["a", "b"]) == ["a", "b"]

    def test_empty_returns_default(self, monkeypatch):
        monkeypatch.setenv("X_CSV", "")
        assert Config._get_csv_env("X_CSV", ["a"]) == ["a"]

    def test_trims_and_splits(self, monkeypatch):
        monkeypatch.setenv("X_CSV", " one , two,  three  ")
        assert Config._get_csv_env("X_CSV", []) == ["one", "two", "three"]

    def test_skips_blanks(self, monkeypatch):
        monkeypatch.setenv("X_CSV", "a,,b,  ,c")
        assert Config._get_csv_env("X_CSV", []) == ["a", "b", "c"]


class TestNormalizeApifyQuality:
    @pytest.mark.parametrize("value", ["360", "480", "720", "1080"])
    def test_valid(self, value):
        assert Config._normalize_apify_quality(value) == value

    @pytest.mark.parametrize("value", [None, "", "4k", "2160", "junk"])
    def test_falls_back_to_1080(self, value):
        assert Config._normalize_apify_quality(value) == "1080"

    def test_trims_whitespace(self):
        assert Config._normalize_apify_quality("  720  ") == "720"


class TestNormalizeYoutubeMetadataProvider:
    def test_data_api_alias(self):
        assert (
            Config._normalize_youtube_metadata_provider("youtube_data_api")
            == "youtube_data_api"
        )
        assert (
            Config._normalize_youtube_metadata_provider("YOUTUBE_DATA_API")
            == "youtube_data_api"
        )

    @pytest.mark.parametrize("value", [None, "", "yt_dlp", "anything-else"])
    def test_defaults_to_ytdlp(self, value):
        assert Config._normalize_youtube_metadata_provider(value) == "yt_dlp"


class TestInferDefaultLlm:
    def test_google_preferred(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LLM", raising=False)
        cfg = Config()
        assert cfg.llm.startswith("google-gla:")

    def test_openai_fallback(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("LLM", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = Config()
        assert cfg.llm.startswith("openai:")

    def test_anthropic_fallback(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        cfg = Config()
        assert cfg.llm.startswith("anthropic:")


class TestResolveYoutubeDataApiKey:
    def test_prefers_dedicated_key(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "yt-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        cfg = Config()
        assert cfg.resolve_youtube_data_api_key() == "yt-key"

    def test_falls_back_to_google_key(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        cfg = Config()
        assert cfg.resolve_youtube_data_api_key() == "google-key"

    def test_returns_none_when_no_keys(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        cfg = Config()
        assert cfg.resolve_youtube_data_api_key() is None


class TestGetConfigOverride:
    def test_override_is_returned(self):
        sentinel = Config()
        set_config_override(sentinel)
        try:
            assert get_config() is sentinel
        finally:
            set_config_override(None)

    def test_without_override_returns_fresh(self):
        set_config_override(None)
        cfg_a = get_config()
        cfg_b = get_config()
        assert isinstance(cfg_a, Config)
        assert isinstance(cfg_b, Config)
        # fresh instances each time
        assert cfg_a is not cfg_b
