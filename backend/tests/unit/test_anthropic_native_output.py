import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from src import ai
from src.workflows import llm


class Outline(BaseModel):
    chapters: list[str]
    payoffs: list[str]


def _config(model):
    return SimpleNamespace(
        llm=model, rerank_llm=None, openai_api_key=None, openrouter_api_key=None,
        openrouter_fallback_model="x/y", google_api_key=None, anthropic_api_key="test-anthropic-key",
        ollama_base_url=None, ollama_api_key=None, as_runtime_settings=lambda: {},
    )


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-haiku-5-5"])
def test_current_anthropic_models_use_native_output(model):
    assert ai._uses_native_output(f"anthropic:{model}")


def test_older_anthropic_models_keep_the_sdk_default():
    assert not ai._uses_native_output("anthropic:claude-haiku-4-5")


@pytest.mark.asyncio
async def test_haiku_workflow_plan_asks_for_a_json_schema_with_room_to_finish(monkeypatch):
    # A Haiku 5.5 outline came back as a forced tool call capped at 4096 tokens,
    # so later fields were cut off and every retry failed SourceOutline validation.
    captured = []
    outline = Outline(chapters=["intro"], payoffs=["the reveal"])

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": outline.model_dump_json()}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })

    real_provider = ai.AnthropicProvider
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(ai, "AnthropicProvider", type("TestProvider", (), {
            "model_profile": staticmethod(real_provider.model_profile),
            "__new__": lambda cls, api_key=None: real_provider(api_key=api_key, http_client=client),
        }))
        monkeypatch.setattr(llm, "get_config", lambda: _config("anthropic:claude-haiku-5-5"))
        monkeypatch.setattr(llm, "apply_settings_to_process_env", lambda settings: None)
        llm._agents.clear()
        try:
            agent = llm.build_agent(Outline, "Outline the source.", structural=True)
            result = await agent.run("Transcript units ...")
        finally:
            llm._agents.clear()

    assert result.output == outline
    body = captured[0]
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "tools" not in body and "tool_choice" not in body
    assert body["max_tokens"] == llm.MAX_OUTPUT_TOKENS
