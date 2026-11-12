import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import social_caption_service as captions


def _config_with_chain(monkeypatch, chain=("google-gla:gemini-2.5-flash",)):
    """A config whose chain holds the given models."""
    config = SimpleNamespace(
        llm=chain[0],
        llm_chain=lambda: tuple(chain),
        provider_keys=lambda provider: [],
        provider_env_key=lambda provider: None,
        as_runtime_settings=lambda: {},
    )
    monkeypatch.setattr(captions, "get_config", lambda: config)
    monkeypatch.setattr(captions, "apply_settings_to_process_env", lambda settings: None)
    monkeypatch.setattr(
        captions, "_get_missing_llm_key_error", lambda name, config: None
    )
    return config


async def test_caption_uses_the_chain_model_and_grounded_clip_data(monkeypatch):
    run = AsyncMock(return_value=SimpleNamespace(output="  Watch this idea.  "))
    settings = {}

    def agent(model, **kwargs):
        settings.update(kwargs)
        settings["model"] = model
        return SimpleNamespace(run=run)

    _config_with_chain(monkeypatch, ("google-gla:gemini-2.5-flash",))
    monkeypatch.setattr(captions, "Agent", agent)
    monkeypatch.setattr(
        captions, "_build_transcript_model", lambda config, model, key=None: model
    )
    result = await captions.generate_social_caption(
        {"hook_title": "An idea", "text": "A real transcript"}, "instagram"
    )
    assert result == "Watch this idea."
    assert settings["model"] == "google-gla:gemini-2.5-flash"
    assert json.loads(run.await_args.args[0]) == {
        "platform": "instagram", "headline": "An idea", "transcript": "A real transcript"
    }


async def test_the_caption_asks_for_the_language_of_the_clip(monkeypatch):
    prompts = []

    def agent(model, **kwargs):
        prompts.append(kwargs.get("system_prompt", ""))
        return SimpleNamespace(
            run=AsyncMock(return_value=SimpleNamespace(output="A caption"))
        )

    _config_with_chain(monkeypatch)
    monkeypatch.setattr(captions, "Agent", agent)
    monkeypatch.setattr(
        captions, "_build_transcript_model", lambda config, model, key=None: model
    )
    await captions.generate_social_caption({"text": "A transcript"}, "youtube")
    await captions.generate_social_caption(
        {"text": "A transcript"}, "youtube", target_language="Russian"
    )

    assert "transcript's language" in prompts[0]
    assert "Russian" in prompts[1]


async def test_a_busy_model_makes_the_next_one_answer(monkeypatch):
    """The caption comes from the next model of the chain."""
    from src.llm_fallback import Attempt, FallbackResult

    async def fake_fallback(build_agent, run, **kwargs):
        agent = build_agent("google-gla:gemini-2.5-flash", None)
        value = await run(agent)
        attempts = (
            Attempt(
                model="google-gla:gemini-3-flash-preview",
                provider="google-gla",
                key_fingerprint=None,
                seconds=1.0,
                ok=False,
            ),
            Attempt(
                model="google-gla:gemini-2.5-flash",
                provider="google-gla",
                key_fingerprint=None,
                seconds=0.2,
                ok=True,
            ),
        )
        return FallbackResult(
            value=value, model="google-gla:gemini-2.5-flash", attempts=attempts
        )

    _config_with_chain(
        monkeypatch,
        ("google-gla:gemini-3-flash-preview", "google-gla:gemini-2.5-flash"),
    )
    monkeypatch.setattr(captions, "run_with_fallback", fake_fallback)
    monkeypatch.setattr(captions, "Agent", lambda *args, **kwargs: SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(output="A caption"))
    ))
    monkeypatch.setattr(
        captions, "_build_transcript_model", lambda config, model, key=None: model
    )

    result = await captions.generate_social_caption({"text": "A transcript"}, "youtube")

    assert result == "A caption"


@pytest.mark.parametrize("output", [" ", "x" * 2201])
async def test_rejects_invalid_caption(monkeypatch, output):
    _config_with_chain(monkeypatch)
    monkeypatch.setattr(captions, "Agent", lambda *args, **kwargs: SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(output=output))
    ))
    monkeypatch.setattr(
        captions, "_build_transcript_model", lambda config, model, key=None: model
    )
    with pytest.raises(ValueError):
        await captions.generate_social_caption({"text": "Transcript"}, "youtube")
