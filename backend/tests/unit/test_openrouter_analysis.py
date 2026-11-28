import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.providers.openrouter import OpenRouterProvider

from src import ai
from src.config import Config
from src.api.routes.admin import _setting_status


@pytest.fixture
def router_config(monkeypatch):
    monkeypatch.setenv("LLM", "openrouter:anthropic/claude-sonnet-5.5")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router-key")
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL", "openai/gpt-6-luna")
    return Config()


@pytest.mark.parametrize("actual_model", ["anthropic/claude-sonnet-5.5", "openai/gpt-6-luna"])
async def test_native_schema_request_and_both_model_responses(router_config, monkeypatch, actual_model):
    """Exercise the real SDK request and parsing without paid network calls.

    The gateway performs failover; this verifies its ordered request contract
    and that SupoClip accepts either model's schema-valid response.
    """
    captured = []
    analysis = ai.TranscriptAnalysis(
        most_relevant_segments=[ai.TranscriptSegment(
            start_time="00:00", end_time="00:30",
            text="A complete explanation with a practical takeaway.",
        )],
        summary="Practical advice", key_topics=["advice"],
    )

    def handler(request):
        assert request.url.host == "openrouter.ai"
        assert request.headers["Authorization"] == "Bearer test-router-key"
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "test-completion", "object": "chat.completion", "created": 1,
            "model": actual_model,
            "provider": "Anthropic" if actual_model.startswith("anthropic/") else "OpenAI",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": analysis.model_dump_json()}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sdk = AsyncOpenAI(api_key="test-router-key", base_url="https://openrouter.ai/api/v1", http_client=client)
        real_provider = OpenRouterProvider
        monkeypatch.setattr(ai, "OpenRouterProvider", type("TestProvider", (), {
            "model_profile": staticmethod(real_provider.model_profile),
            "__new__": lambda cls, **kwargs: real_provider(openai_client=sdk),
        }))
        model = ai._build_transcript_model(router_config)
        agent = Agent(model, output_type=NativeOutput(ai.TranscriptAnalysis, strict=True))
        result = await agent.run("Select a clip from the transcript.")

    assert result.output == analysis
    assert result.response.model_name == actual_model
    body = captured[0]
    assert body["model"] == "anthropic/claude-sonnet-5.5"
    assert body["models"] == ["openai/gpt-6-luna"]
    assert body["provider"]["require_parameters"] is True
    assert body["provider"]["allow_fallbacks"] is True
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in body
    assert body["max_completion_tokens"] == 8192


def test_missing_key_fails_before_request(router_config):
    router_config.openrouter_api_key = None
    assert "OPENROUTER_API_KEY" in ai._get_missing_llm_key_error(router_config.llm, router_config)


def test_admin_key_status_is_masked(router_config):
    status = _setting_status("OPENROUTER_API_KEY", {})
    assert status["input_type"] == "password"
    assert "test-router-key" not in json.dumps(status)


def test_invalid_model_ids_fail_before_request(router_config):
    assert "vendor" in ai._get_missing_llm_key_error("openrouter:sonnet", router_config)
    router_config.openrouter_fallback_model = "luna"
    with pytest.raises(RuntimeError, match="vendor/model"):
        ai._build_transcript_model(router_config)


def test_agent_cache_refreshes_after_key_or_fallback_change(router_config, monkeypatch):
    monkeypatch.setattr(ai, "get_config", lambda: router_config)
    monkeypatch.setattr(ai, "_transcript_agent", None)
    monkeypatch.setattr(ai, "_transcript_agent_signature", None)
    monkeypatch.setattr(ai, "apply_settings_to_process_env", lambda settings: None)
    first = ai.get_transcript_agent()
    assert ai.get_transcript_agent() is first
    router_config.openrouter_api_key = "rotated-test-key"
    second = ai.get_transcript_agent()
    assert second is not first
    router_config.openrouter_fallback_model = "openai/gpt-6-sol"
    assert ai.get_transcript_agent() is not second


# OpenAI Responses API (explicit ``openai-responses:<model>``). The model name is
# synthetic on purpose: any name the installed SDK has never heard of must work.
RESPONSES_MODEL = "example-responses-model"


@pytest.fixture
def responses_config(monkeypatch):
    monkeypatch.setenv("LLM", f"openai-responses:{RESPONSES_MODEL}")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    return Config()


async def test_openai_responses_strict_schema_without_storage(responses_config, monkeypatch):
    captured = []
    analysis = ai.TranscriptAnalysis(
        most_relevant_segments=[ai.TranscriptSegment(
            start_time="00:00", end_time="00:30",
            text="A complete explanation with a practical takeaway.",
        )],
        summary="Practical advice", key_topics=["advice"],
    )

    def handler(request):
        assert request.url.host == "api.openai.com"
        assert request.url.path.endswith("/responses")
        assert request.headers["Authorization"] == "Bearer test-openai-key"
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 1,
            "model": RESPONSES_MODEL, "status": "completed",
            "error": None, "incomplete_details": None,
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "output": [{
                "type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": analysis.model_dump_json(), "annotations": []}],
            }],
            "usage": {
                "input_tokens": 100, "output_tokens": 100, "total_tokens": 200,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sdk = AsyncOpenAI(api_key="test-openai-key", http_client=client)
        real_provider = ai.OpenAIProvider
        monkeypatch.setattr(ai, "OpenAIProvider", type("TestProvider", (), {
            "model_profile": staticmethod(real_provider.model_profile),
            "__new__": lambda cls, **kwargs: real_provider(openai_client=sdk),
        }))
        model = ai._build_transcript_model(responses_config)
        assert isinstance(model, ai.OpenAIResponsesModel)
        assert ai._uses_native_output(responses_config.llm)
        agent = Agent(model, output_type=NativeOutput(ai.TranscriptAnalysis, strict=True))
        result = await agent.run("Select a clip from the transcript.")

    assert result.output == analysis
    body = captured[0]
    assert body["model"] == RESPONSES_MODEL
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert not body.get("tools")
    # No reasoning setting is chosen for the model; the API's default applies.
    assert "reasoning" not in body
    assert body["max_output_tokens"] == 8192


def test_openai_responses_reuses_openai_key_check(responses_config):
    assert ai._get_missing_llm_key_error(responses_config.llm, responses_config) is None
    responses_config.openai_api_key = None
    assert "OPENAI_API_KEY" in ai._get_missing_llm_key_error(responses_config.llm, responses_config)
    assert "missing a model name" in ai._get_missing_llm_key_error("openai-responses:", responses_config)


def test_plain_openai_prefix_keeps_chat_completions(responses_config):
    # ``openai:`` is left to pydantic-ai's default (Chat Completions), unchanged.
    assert ai._build_transcript_model(responses_config, "openai:example-chat-model") == "openai:example-chat-model"
    assert not ai._uses_native_output("openai:example-chat-model")
