import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.repositories.edit_transaction import TaskCancelled
from src.workflows import llm
from src.workflows.llm import PlanStats, gather_all, run_retrying, run_with_repair


class Answer(BaseModel):
    value: int


def _result(value):
    return SimpleNamespace(output=Answer(value=value), usage=lambda: SimpleNamespace(input_tokens=7, output_tokens=3))


def _scripted(monkeypatch, values):
    prompts = []

    async def fake_run(agent, prompt, timeout_seconds=None):
        prompts.append(prompt)
        return _result(values.pop(0))

    monkeypatch.setattr("src.ai._run_transcript_analysis", fake_run)
    return prompts


def _issues_for(bad_values):
    return lambda answer: [f"value {answer.value} is wrong"] * bad_values.get(answer.value, 0)


@pytest.mark.asyncio
async def test_repair_sends_previous_answer_and_issues_through_the_shared_runner(monkeypatch):
    prompts = _scripted(monkeypatch, [1, 2])
    stats = PlanStats()

    answer, issues = await run_with_repair(object(), "PLAN THIS", _issues_for({1: 2}), stats=stats, stage="draft")

    assert answer.value == 2 and issues == []
    assert len(prompts) == 2
    repair = prompts[1]
    assert repair.startswith("PLAN THIS\n\nYOUR PREVIOUS ANSWER:\n")
    assert '{"value":1}' in repair
    assert "IT BREAKS THESE RULES:\n- value 1 is wrong\n- value 1 is wrong" in repair
    assert repair.endswith("Change only what is needed to fix these problems.")
    assert stats.repairs == 1 and stats.llm_calls == 2
    assert stats.stages["draft"]["input_tokens"] == 7 and stats.stages["draft_repair"]["calls"] == 1


@pytest.mark.asyncio
async def test_repair_keeps_the_answer_with_fewer_issues(monkeypatch):
    _scripted(monkeypatch, [1, 2])

    answer, issues = await run_with_repair(object(), "p", _issues_for({1: 1, 2: 3}))

    assert answer.value == 1 and len(issues) == 1


@pytest.mark.asyncio
async def test_a_failed_repair_keeps_the_usable_first_answer(monkeypatch):
    calls = []

    async def fake_run(agent, prompt, timeout_seconds=None):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("The AI analysis service took too long to respond.")
        return _result(1)

    monkeypatch.setattr("src.ai._run_transcript_analysis", fake_run)
    stats = PlanStats()

    answer, issues = await run_with_repair(object(), "p", _issues_for({1: 2}), stats=stats, stage="draft")

    assert answer.value == 1 and len(issues) == 2 and len(calls) == 2
    assert stats.stages["draft_repair"]["failed"] == 1


@pytest.mark.asyncio
async def test_a_cancelled_repair_is_never_swallowed(monkeypatch):
    calls = []

    async def fake_run(agent, prompt, timeout_seconds=None):
        calls.append(prompt)
        if len(calls) == 2:
            raise TaskCancelled()
        return _result(1)

    monkeypatch.setattr("src.ai._run_transcript_analysis", fake_run)

    with pytest.raises(TaskCancelled):
        await run_with_repair(object(), "p", _issues_for({1: 1}))


@pytest.mark.asyncio
async def test_too_many_issues_skip_the_repair(monkeypatch):
    prompts = _scripted(monkeypatch, [1])

    answer, issues = await run_with_repair(object(), "p", _issues_for({1: 16}))

    assert answer.value == 1 and len(issues) == 16 and len(prompts) == 1


@pytest.mark.asyncio
async def test_a_clean_answer_needs_one_call(monkeypatch):
    prompts = _scripted(monkeypatch, [5])

    answer, issues = await run_with_repair(object(), "p", _issues_for({}))

    assert answer.value == 5 and issues == [] and len(prompts) == 1


@pytest.mark.asyncio
async def test_run_records_property_usage_and_passes_the_timeout(monkeypatch):
    seen = {}

    async def fake_run(agent, prompt, timeout_seconds=None):
        seen["timeout"] = timeout_seconds
        return SimpleNamespace(output=1, usage=SimpleNamespace(input_tokens=11, output_tokens=4))

    monkeypatch.setattr("src.ai._run_transcript_analysis", fake_run)
    stats = PlanStats()
    await llm.run(object(), "p", timeout=42, stats=stats, stage="outline")

    assert seen["timeout"] == 42
    assert stats.stages["outline"]["input_tokens"] == 11 and stats.stages["outline"]["output_tokens"] == 4
    assert stats.as_dict(3.21)["plan_seconds"] == 3.2


def _config(**overrides):
    values = dict(
        llm="openai:gpt-5", rerank_llm="anthropic:claude-x", openai_api_key="k", openrouter_api_key=None,
        openrouter_fallback_model="x/y", google_api_key=None, anthropic_api_key="a", ollama_base_url=None,
        ollama_api_key=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values, as_runtime_settings=lambda: {})


def test_build_agent_uses_the_judgement_model_for_structural_passes_and_caches(monkeypatch):
    monkeypatch.setattr(llm, "get_config", lambda: _config())
    monkeypatch.setattr(llm, "apply_settings_to_process_env", lambda settings: None)
    built = []
    monkeypatch.setattr("src.ai._build_transcript_model", lambda cfg, name: built.append(name) or "test")
    llm._agents.clear()

    structural = llm.build_agent(Answer, "system", structural=True)
    bulk = llm.build_agent(Answer, "system", structural=False)
    again = llm.build_agent(Answer, "system", structural=True)
    other = llm.build_agent(Answer, "other system", structural=True)

    assert built == ["anthropic:claude-x", "openai:gpt-5", "anthropic:claude-x"]
    assert again is structural and bulk is not structural and other is not structural
    llm._agents.clear()


def test_the_production_model_gets_room_to_finish_long_plans(monkeypatch):
    monkeypatch.setattr(llm, "get_config", lambda: _config(llm="anthropic:claude-sonnet-5-5", rerank_llm="anthropic:claude-sonnet-5-5"))
    monkeypatch.setattr(llm, "apply_settings_to_process_env", lambda settings: None)
    monkeypatch.setattr("src.ai._build_transcript_model", lambda cfg, name: "test")
    llm._agents.clear()

    hosted = llm.build_agent(Answer, "system", structural=True)
    llm._agents.clear()
    monkeypatch.setattr(llm, "get_config", lambda: _config(llm="ollama:qwen", rerank_llm="ollama:qwen"))
    local = llm.build_agent(Answer, "system", structural=True)

    assert hosted.model_settings == {"max_tokens": llm.MAX_OUTPUT_TOKENS} and llm.MAX_OUTPUT_TOKENS > 8192
    assert local.model_settings is None
    llm._agents.clear()


def test_build_agent_refuses_a_model_without_its_key(monkeypatch):
    monkeypatch.setattr(llm, "get_config", lambda: _config(anthropic_api_key=None))
    monkeypatch.setattr(llm, "apply_settings_to_process_env", lambda settings: None)
    llm._agents.clear()

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.build_agent(Answer, "system", structural=True)


@pytest.mark.asyncio
async def test_run_retrying_retries_once_then_raises():
    attempts = []

    async def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return "ok"

    assert await run_retrying(flaky) == "ok"

    async def broken():
        attempts.append(1)
        raise ValueError("still broken")

    attempts.clear()
    with pytest.raises(ValueError):
        await run_retrying(broken)
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_run_retrying_never_retries_a_cancellation():
    attempts = []

    async def cancelled():
        attempts.append(1)
        raise TaskCancelled()

    with pytest.raises(TaskCancelled):
        await run_retrying(cancelled)
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_gather_all_cancels_the_rest_on_the_first_failure():
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def failing():
        await asyncio.sleep(0)
        raise RuntimeError("window failed")

    with pytest.raises(RuntimeError, match="window failed"):
        await gather_all([slow(), failing()])
    assert cancelled.is_set()
    assert await gather_all([asyncio.sleep(0, result=1), asyncio.sleep(0, result=2)]) == [1, 2]


@pytest.mark.asyncio
async def test_failed_calls_are_counted(monkeypatch):
    async def failing(agent, prompt, timeout_seconds=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr("src.ai._run_transcript_analysis", failing)
    stats = PlanStats()
    with pytest.raises(RuntimeError):
        await llm.run(object(), "p", stats=stats, stage="series_draft")
    assert stats.llm_calls == 1 and stats.stages["series_draft"]["failed"] == 1
