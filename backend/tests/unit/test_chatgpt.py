import asyncio
import base64
import hashlib
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel

from src import ai, chatgpt_analysis
from src.services import chatgpt_service as service
from src.services.chatgpt_service import ChatGPTError, ChatGPTService, PLAN_SCOPE


@pytest.fixture
def db():
    return SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())


@pytest.fixture
def repo(monkeypatch):
    repo = SimpleNamespace(**{name: AsyncMock() for name in (
        "host_id", "account", "accounts", "save_attempt", "consume_attempt", "save_account", "save_tokens", "select_model", "register_client", "lock_user", "disable", "acknowledge"
    )}, tokens=lambda row: row["tokens"])
    repo.host_id.return_value = "urn:uuid:stable-host"
    monkeypatch.setattr(service, "Repo", repo)
    monkeypatch.setenv("NEXT_PUBLIC_APP_URL", "http://localhost:3107")
    monkeypatch.delenv("CHATGPT_REDIRECT_URI", raising=False)
    return repo


async def test_authorization_has_pkce_nonce_host_and_encrypted_attempt_contract(db, repo):
    result = await ChatGPTService(db).authorize("alice")
    params = parse_qs(urlsplit(result["authorization_url"]).query)
    attempt = repo.save_attempt.await_args.args[3]
    assert params["client_id"] == ["dynamic_agent_client"]
    assert params["agent_name_hint"] == ["SupoClip"]
    assert params["ext_agent_host_id"] == ["urn:uuid:stable-host"]
    assert params["redirect_uri"] == ["http://127.0.0.1:8000/chatgpt/callback"]
    assert params["state"] == [result["state"]]
    assert params["nonce"] == [attempt["nonce"]]
    expected = base64.urlsafe_b64encode(hashlib.sha256(attempt["verifier"].encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [expected]
    assert PLAN_SCOPE in params["scope"][0].split()


async def test_reconnect_reuses_registration_and_host(db, repo):
    repo.account.return_value = {"client_id": "oaiapp_saved", "subject": "alice", "email": "alice@example.com", "encrypted_tokens": None}
    result = await ChatGPTService(db).authorize("user", "account")
    params = parse_qs(urlsplit(result["authorization_url"]).query)
    assert params["client_id"] == ["oaiapp_saved"]
    assert "agent_name_hint" not in params
    assert repo.save_attempt.await_args.args[3]["subject"] == "alice"


@pytest.mark.parametrize("value", ["https://example.com", "http://192.168.1.4:3107", "http://localhost:3107/path", "http://attacker@localhost:3107"])
def test_remote_frontend_is_rejected(monkeypatch, value):
    monkeypatch.setenv("NEXT_PUBLIC_APP_URL", value)
    with pytest.raises(ChatGPTError, match="locally"):
        service.local_urls()


async def test_invalid_state_never_exchanges_code(db, repo, monkeypatch):
    repo.consume_attempt.return_value = None
    exchange = AsyncMock()
    monkeypatch.setattr(service, "token_request", exchange)
    with pytest.raises(ChatGPTError, match="expired"):
        await ChatGPTService(db).complete("alice", "wrong", "code", "oaiapp_new")
    exchange.assert_not_awaited()


@pytest.mark.parametrize("client_id,error", [(None, None), ("dynamic_agent_client", None), ("oaiapp_new", "access_denied")])
async def test_incomplete_or_declined_registration_does_not_exchange(db, repo, monkeypatch, client_id, error):
    repo.consume_attempt.return_value = {"client_id": "dynamic_agent_client"}
    exchange = AsyncMock()
    monkeypatch.setattr(service, "token_request", exchange)
    with pytest.raises(ChatGPTError):
        await ChatGPTService(db).complete("alice", "state", "code", client_id, error)
    exchange.assert_not_awaited()


async def test_returning_client_cannot_be_substituted(db, repo, monkeypatch):
    repo.consume_attempt.return_value = {"client_id": "oaiapp_saved"}
    exchange = AsyncMock()
    monkeypatch.setattr(service, "token_request", exchange)
    with pytest.raises(ChatGPTError, match="different account"):
        await ChatGPTService(db).complete("alice", "state", "code", "oaiapp_attacker")
    exchange.assert_not_awaited()


async def test_new_registration_validates_identity_before_saving(db, repo, monkeypatch):
    repo.consume_attempt.return_value = {"client_id": "dynamic_agent_client", "verifier": "verifier", "nonce": "nonce", "redirect_uri": "callback", "subject": None}
    exchange = AsyncMock(return_value={"access_token": "secret", "refresh_token": "renewable", "id_token": "signed", "scope": PLAN_SCOPE, "expires_in": 3600})
    monkeypatch.setattr(service, "token_request", exchange)
    monkeypatch.setattr(service, "validate_identity", lambda token, client_id, nonce: {"sub": "alice", "email": "alice@example.com"})
    assert await ChatGPTService(db).complete("user", "state", "code", "oaiapp_issued") == {"connected": True}
    assert exchange.await_args.args[0]["client_id"] == "oaiapp_issued"
    assert repo.save_account.await_args.args[1:3] == ("user", "oaiapp_issued")


async def test_failed_first_exchange_retains_issued_registration_without_credentials(db, repo, monkeypatch):
    repo.consume_attempt.return_value = {"client_id": "dynamic_agent_client", "verifier": "verifier", "nonce": "nonce", "redirect_uri": "callback", "subject": None}
    monkeypatch.setattr(service, "token_request", AsyncMock(side_effect=ChatGPTError("expired", 401)))
    with pytest.raises(ChatGPTError, match="expired"):
        await ChatGPTService(db).complete("user", "state", "code", "oaiapp_issued")
    repo.register_client.assert_awaited_once_with(db, "user", "oaiapp_issued")
    repo.save_account.assert_not_awaited()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize("change", [{}, {"nonce": "wrong"}, {"iss": "https://attacker.example"}, {"aud": "wrong"}, {"exp": 1}, {"sub": ""}])
def test_oidc_signature_issuer_audience_expiration_and_nonce(monkeypatch, signing_key, change):
    claims = {"sub": "alice", "iss": service.ISSUER, "aud": "oaiapp_saved", "exp": int(time.time()) + 600, "nonce": "nonce", **change}
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    monkeypatch.setattr(service, "_jwks", SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=signing_key.public_key())))
    if change:
        with pytest.raises(ChatGPTError, match="verify"):
            service.validate_identity(token, "oaiapp_saved", "nonce")
    else:
        assert service.validate_identity(token, "oaiapp_saved", "nonce")["sub"] == "alice"


async def test_rotating_refresh_uses_saved_client_and_saves_latest_tokens(db, repo, monkeypatch):
    repo.account.return_value = {"client_id": "oaiapp_saved", "encrypted_tokens": "encrypted", "enabled": True,
                                 "tokens": {"access_token": "expired", "refresh_token": "old-refresh", "scope": PLAN_SCOPE, "expires_at": 0}}
    exchange = AsyncMock(return_value={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})
    monkeypatch.setattr(service, "token_request", exchange)
    assert await ChatGPTService(db).access_token("alice", "account", require_enabled=True) == "new-access"
    assert repo.account.await_args.kwargs == {"lock": True}
    assert exchange.await_args.args[0] == {"grant_type": "refresh_token", "client_id": "oaiapp_saved", "refresh_token": "old-refresh"}
    assert repo.save_tokens.await_args.args[3]["refresh_token"] == "new-refresh"


async def test_refresh_cannot_retain_revoked_plan_permission(db, repo, monkeypatch):
    repo.account.return_value = {"client_id": "oaiapp_saved", "encrypted_tokens": "encrypted", "enabled": True,
                                 "tokens": {"access_token": "expired", "refresh_token": "old", "scope": PLAN_SCOPE, "expires_at": 0}}
    monkeypatch.setattr(service, "token_request", AsyncMock(return_value={"access_token": "new", "scope": "openid"}))
    with pytest.raises(ChatGPTError, match="permission was revoked"):
        await ChatGPTService(db).access_token("alice", "account")


async def test_live_catalog_filters_hidden_models(db, repo, monkeypatch):
    monkeypatch.setattr(ChatGPTService, "access_token", AsyncMock(return_value="token"))
    original_client = httpx.AsyncClient
    def handle(request):
        assert request.headers["authorization"] == "Bearer token"
        return httpx.Response(200, json={"models": [{"slug": "available", "display_name": "Available", "visibility": "list"}, {"slug": "hidden", "visibility": "hide"}]})
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handle)))
    assert await ChatGPTService(db).models("alice", "account") == [{"id": "available", "name": "Available"}]


class Answer(BaseModel):
    answer: str


@pytest.fixture
def inference(monkeypatch):
    @asynccontextmanager
    async def session():
        yield object()
    monkeypatch.setattr(chatgpt_analysis, "AsyncSessionLocal", session)
    monkeypatch.setattr(ChatGPTService, "access_token", AsyncMock(return_value="plan-token"))
    calls = []
    chunks = ["{\"answer\":\"yes\"}"]
    terminal = ["response.completed"]
    def handle(request):
        calls.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer plan-token"
        events = [{"type": "response.output_text.delta", "delta": chunk, "item_id": "item", "output_index": 0, "content_index": 0, "sequence_number": 1} for chunk in chunks]
        events.extend({"type": kind, "sequence_number": 2, "response": {"id": "r", "object": "response", "status": "completed" if kind == "response.completed" else "failed", "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}, "output": []}} for kind in terminal)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))
    original = chatgpt_analysis.AsyncOpenAI
    monkeypatch.setattr(chatgpt_analysis, "AsyncOpenAI", lambda **kwargs: original(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))))
    return calls, chunks, terminal


async def test_plan_inference_requires_completed_stream_and_uses_no_unsupported_fields(inference):
    calls, _, _ = inference
    agent = chatgpt_analysis.ChatGPTAnalysisAgent(("alice", "account", "chosen-model"), Answer, "instructions")
    result = await agent.run("transcript")
    assert result.output.answer == "yes"
    assert calls[0]["store"] is False and calls[0]["stream"] is True
    assert calls[0]["input"] == [{"role": "user", "content": "transcript"}]
    assert calls[0]["instructions"] == "instructions"
    assert not {"temperature", "max_output_tokens", "previous_response_id", "tools"} & calls[0].keys()


@pytest.mark.parametrize("output_type,payload", [
    (ai.TranscriptAnalysis, {"most_relevant_segments": [], "summary": "Diagnostic", "key_topics": [], "broll_opportunities": None}),
    (ai.CandidateRanking, {"ranking": []}),
])
async def test_real_clip_schemas_satisfy_strict_structured_output_contract(inference, output_type, payload):
    calls, chunks, _ = inference
    chunks[:] = [json.dumps(payload)]
    result = await chatgpt_analysis.ChatGPTAnalysisAgent(("alice", "account", "model"), output_type, "instructions").run("transcript")
    assert isinstance(result.output, output_type)
    response_format = calls[0]["text"]["format"]
    assert response_format["strict"] is True

    def check_schema(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check_schema(value)
        elif isinstance(node, list):
            for value in node:
                check_schema(value)

    check_schema(response_format["schema"])
    if output_type is ai.TranscriptAnalysis:
        broll = response_format["schema"]["properties"]["broll_opportunities"]
        assert {"type": "null"} in broll["anyOf"]
        assert "default" not in broll


def test_invalid_schema_error_identifies_application_problem():
    error = chatgpt_analysis.response_error("invalid_json_schema", 400)
    assert "Update SupoClip" in str(error)
    assert error.status_code == 502


@pytest.mark.parametrize("terminal_event", [None, "response.failed", "response.incomplete"])
async def test_interrupted_and_usage_failed_streams_never_return_partial_json(inference, terminal_event):
    calls, _, terminal = inference
    terminal[:] = [terminal_event] if terminal_event else []
    with pytest.raises(ChatGPTError):
        await chatgpt_analysis.ChatGPTAnalysisAgent(("alice", "account", "model"), Answer, "instructions").run("transcript")
    assert len(calls) == 1


async def test_schema_repair_is_bounded(inference):
    calls, chunks, _ = inference
    chunks[:] = ['{"wrong": "shape"}']
    with pytest.raises(ChatGPTError, match="three attempts"):
        await chatgpt_analysis.ChatGPTAnalysisAgent(("alice", "account", "model"), Answer, "instructions").run("transcript")
    assert len(calls) == 3


async def test_concurrent_analysis_contexts_do_not_share_account_or_agent():
    async def select(user):
        token = chatgpt_analysis.chatgpt_account.set((user, f"{user}-account", f"{user}-model"))
        try:
            await asyncio.sleep(0)
            assert ai.get_transcript_agent().binding[0] == user
            assert ai.get_rerank_agent().binding[0] == user
        finally:
            chatgpt_analysis.chatgpt_account.reset(token)
    await asyncio.gather(select("alice"), select("bob"))
    assert chatgpt_analysis.chatgpt_account.get() is None


async def test_rerank_does_not_hide_plan_limit(monkeypatch):
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: SimpleNamespace(run=AsyncMock(side_effect=ChatGPTError("limit", 429))))
    candidates = [ai.TranscriptSegment(start_time="00:00", end_time="00:30", text="A valid clip example with enough content.") for _ in range(2)]
    with pytest.raises(ChatGPTError, match="limit"):
        await ai.rerank_candidates(candidates)
