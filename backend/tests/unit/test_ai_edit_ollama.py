"""Local planner contracts and failures; all HTTP is intercepted, no model calls."""
import json
from types import SimpleNamespace

import httpx
import pytest

from src.services import ai_edit_service as ai


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("AI_EDIT_MODEL", "ollama:qwen3.8:27b")
    monkeypatch.setattr(ai, "get_config", lambda: SimpleNamespace(
        anthropic_api_key="", ollama_api_key="", resolve_ollama_base_url=lambda: "http://127.0.0.1:11434/v1/",
    ))
    real_client = httpx.AsyncClient
    def intercept(handler):
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    return intercept


async def test_local_uses_schema_and_bounded_nonstreaming_generation(local):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"message": {"content": json.dumps({"summary": "Muted.", "operations": [
            {"type": "set", "setting": "audio.muted", "value": "true"},
        ]})}, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 20})
    local(handler)
    assert ai.ai_editing_configured()
    result = await ai.plan_edit("Request:\nmute it")
    assert result.operations[0].setting == "audio.muted"
    assert len(seen) == 1
    assert str(seen[0].url) == "http://127.0.0.1:11434/api/chat"
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen3.8:27b"
    assert body["format"] == ai.EditPlan.model_json_schema()
    # A constrained grammar alone doesn't expose the catalogue to local models.
    system = body["messages"][0]["content"]
    schema = json.loads(system.split("JSON output schema:\n", 1)[1])
    assert schema == body["format"]
    assert schema["$defs"]["MoveBRoll"]["properties"]["id"]["type"] == "string"
    assert "move_broll with its existing id" in system
    assert body["stream"] is False and body["think"] is False
    assert body["options"] == {"temperature": 0, "num_predict": 2048, "num_ctx": 8192}


@pytest.mark.parametrize("status,payload,error", [
    (404, {}, "model isn't available"),
    (503, {}, "busy or unavailable"),
    (200, [], "answer was invalid"),
    (200, {}, "answer was invalid"),
    (200, {"message": {"content": "{}"}}, "answer was invalid"),
    (200, {"message": {"content": "not JSON"}}, "answer was invalid"),
    (200, {"message": {"content": '{"summary":"x","operations":[{"type":"shell"}]}'}}, "answer was invalid"),
    (200, {"done_reason": "length", "message": {"content": "{"}}, "answer was incomplete"),
])
async def test_local_failures_are_clear_without_retries_or_hosted_fallback(local, status, payload, error):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=payload)
    local(handler)
    with pytest.raises(ai.AIEditError, match=error):
        await ai.plan_edit("context")
    assert len(calls) == 1


async def test_local_network_timeout(local):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)
    local(handler)
    with pytest.raises(ai.AIEditError, match="Couldn't reach the local AI service"):
        await ai.plan_edit("context")


async def test_missing_local_model_does_not_fall_back_to_hosted(local, monkeypatch):
    monkeypatch.setenv("AI_EDIT_MODEL", "ollama:")
    assert ai.ai_editing_configured() is False
    with pytest.raises(ai.AIEditError, match="Set an Ollama model"):
        await ai.plan_edit("context")
