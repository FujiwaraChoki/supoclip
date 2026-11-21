"""edit_fx.settings: strict API parsing, lenient persisted reads, the settings menu."""

import json

import pytest

from src.edit_fx.contracts import EditStyle
from src.edit_fx.settings import EditStyleError, edit_style_schema, parse_edit_style, read_edit_style


@pytest.mark.parametrize("raw", [None, {}])
def test_parse_empty_is_off(raw):
    assert parse_edit_style(raw) == EditStyle()


def test_parse_valid():
    style = parse_edit_style({"intensity": "bold", "music_mood": "hype", "progress_bar": True})
    assert style.intensity == "bold" and style.music_mood == "hype" and style.progress_bar


@pytest.mark.parametrize("raw", [
    "bold",                                         # not an object
    ["intensity", "bold"],
    42,
    {"intensity": "extreme"},                            # bad value
    {"intensity": "bold", "turbo": True},           # unknown key
    {"sfx": "true"},                                 # string for bool
    {"sfx": 1},                                      # int for bool
    {"censor": "beep"},
    {"grade": "sepia"},
    {"music_mood": None},
    {"intensity": None},
    {"intensity": "Bold"},
])
def test_parse_is_strict(raw):
    with pytest.raises(EditStyleError):
        parse_edit_style(raw)


def test_parse_error_names_the_problem():
    with pytest.raises(EditStyleError, match="turbo"):
        parse_edit_style({"turbo": True})
    with pytest.raises(EditStyleError, match="censor"):
        parse_edit_style({"censor": "loud"})


def test_parse_error_is_a_value_error():
    assert issubclass(EditStyleError, ValueError)


def test_read_accepts_dict_json_string_and_bytes():
    stored = EditStyle(intensity="max", grain=True).to_json()
    for raw in (stored, json.dumps(stored), json.dumps(stored).encode()):
        assert read_edit_style(raw) == EditStyle(intensity="max", grain=True)


@pytest.mark.parametrize("raw", [None, {}, "", "not json", "[1,2]", "null", 5, [], b"\xff"])
def test_read_unreadable_is_off(raw):
    assert read_edit_style(raw) == EditStyle()


def test_read_keeps_an_active_style_with_a_newer_key(caplog):
    with caplog.at_level("INFO"):
        style = read_edit_style({"intensity": "bold", "laser_eyes": True, "music": False})
    assert style == EditStyle(intensity="bold", music=False)
    assert style.active
    assert any("fx_compat_dropped_keys" in r.getMessage() and "laser_eyes" in r.getMessage() for r in caplog.records)


def test_read_invalid_value_falls_back_to_that_keys_default():
    style = read_edit_style({"intensity": "bold", "censor": "nuclear", "sfx": "yes", "grade": "noir"})
    assert style == EditStyle(intensity="bold", grade="noir")


def test_read_invalid_intensity_is_off_but_keeps_nothing_else_wrong():
    assert read_edit_style({"intensity": "ludicrous", "sfx": False}) == EditStyle(sfx=False)


def test_read_never_raises_on_hostile_input():
    class Weird(dict):
        def items(self):
            raise RuntimeError("boom")

    assert read_edit_style(Weird(intensity="bold")) == EditStyle()


def test_schema_lists_every_setting_but_intensity():
    schema = edit_style_schema()
    keys = [entry["key"] for entry in schema]
    assert keys == [k for k in EditStyle.model_fields if k != "intensity"]
    assert schema[0] == {"key": "sfx", "type": "bool", "default": True}
    by_key = {entry["key"]: entry for entry in schema}
    assert by_key["censor"] == {"key": "censor", "type": "enum", "default": "bleep_bar",
                                "choices": ["off", "bleep", "bleep_bar", "bleep_pixelate"]}
    assert by_key["music_mood"]["choices"] == ["auto", "hype", "chill", "dramatic", "funny", "inspiring", "dark",
                                                  "aggressive"]
    assert by_key["grade"]["choices"][0] == "auto"
    assert all(entry["default"] == EditStyle.model_fields[entry["key"]].default for entry in schema)
    json.dumps(schema)


# --- env and job helpers (edit_fx.env is the only reader of these variables) ------------------------------------

from src.edit_fx import env  # noqa: E402
from src.edit_fx.jobs import FxJob, fx_active  # noqa: E402


def test_env_defaults(monkeypatch):
    for name in ("EDIT_PLAN_LLM", "EDIT_PLAN_TIMEOUT_SECONDS", "EDIT_PLAN_DAILY_LIMIT", "FX_RUNTIME", "FX_DENO_BIN",
                 "FX_WARP", "FX_RENDER_TIMEOUT_SECONDS", "FX_LIBRARY_DIR", "FX_LOUDNORM", "REACTIONS_PROVIDER",
                 "GIPHY_API_KEY", "FX_CHAIN_MODE"):
        monkeypatch.delenv(name, raising=False)
    assert env.edit_plan_timeout() == 45.0 and env.edit_plan_daily_limit() == 100
    assert env.fx_runtime() == "deno" and env.fx_deno_bin() == "deno" and env.fx_warp_enabled()
    assert env.fx_render_timeout(60) == 900 and env.fx_render_timeout(200) == 1200
    assert env.fx_library_dir().name == "fx_library"
    assert env.fx_loudnorm_mode() == "static" and env.reactions_provider() == "off" and env.giphy_api_key() == ""
    assert env.fx_chain_mode() == "pipe"


def test_env_overrides_and_bad_values(monkeypatch, tmp_path):
    monkeypatch.setenv("EDIT_PLAN_LLM", " openai:gpt-5 ")
    monkeypatch.setenv("EDIT_PLAN_TIMEOUT_SECONDS", "nope")
    monkeypatch.setenv("EDIT_PLAN_DAILY_LIMIT", "-3")
    monkeypatch.setenv("FX_RUNTIME", "OFF")
    monkeypatch.setenv("FX_WARP", "off")
    monkeypatch.setenv("FX_RENDER_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("FX_LIBRARY_DIR", str(tmp_path))
    monkeypatch.setenv("FX_LOUDNORM", "loud")
    monkeypatch.setenv("REACTIONS_PROVIDER", "giphy")
    monkeypatch.setenv("FX_CHAIN_MODE", "file")
    assert env.edit_plan_model() == "openai:gpt-5"
    assert env.edit_plan_timeout() == 45.0 and env.edit_plan_daily_limit() == 100
    assert env.fx_runtime() == "off" and not env.fx_warp_enabled()
    assert env.fx_render_timeout(600) == 120.0
    assert env.fx_library_dir() == tmp_path
    assert env.fx_loudnorm_mode() == "static" and env.reactions_provider() == "giphy"
    assert env.fx_chain_mode() == "file"
    monkeypatch.setenv("FX_RUNTIME", " Legacy ")
    assert env.fx_runtime() == "legacy"
    monkeypatch.setenv("FX_RUNTIME", "none")
    assert env.fx_runtime() == "deno"


def test_edit_plan_model_falls_back_to_llm(monkeypatch):
    from src.config import get_config

    monkeypatch.delenv("EDIT_PLAN_LLM", raising=False)
    assert env.edit_plan_model() == get_config().llm


def test_fx_active():
    assert not fx_active(EditStyle(), "default") and not fx_active(EditStyle(), None)
    assert fx_active(EditStyle(), "slam")                       # style only
    assert fx_active(EditStyle(intensity="subtle"), "hormozi")
    job = FxJob(style=EditStyle(intensity="bold"), task_id="t", clip_index=2)
    assert job.cached_beats is None and job.on_beats is None
