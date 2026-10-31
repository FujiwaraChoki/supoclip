"""Model plumbing for the workflow planners: agents, the runner, one repair round.

Every call goes through ``ai._run_transcript_analysis``, the same runner
highlights uses: transient-error retries, one wall-clock budget per call,
and logging that never includes provider bodies (they may hold transcript
text). Bulk passes (outline windows, topic extraction) use ``LLM``; judgement
passes (outline, drafts, critiques, judges) use ``RERANK_LLM``, which
defaults to ``LLM``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Sequence, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput

from .. import ai
from ..config import get_config
from ..repositories.edit_transaction import TaskCancelled
from ..runtime_settings import apply_settings_to_process_env

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

CALL_TIMEOUT_SECONDS = 600
# More issues than this means the answer misunderstood the task; a repair
# round would only rewrite it. The deterministic solver takes it from there.
MAX_REPAIRABLE_ISSUES = 15
# Hosted models are built with an 8192-token output cap (``ai._build_transcript_model``).
# A ten-part series draft or a full outline, with the model's own reasoning,
# can come close to that, and a truncated answer is a failed call. Workflow
# agents get room to finish; only the tokens actually written are billed.
MAX_OUTPUT_TOKENS = 16384
_AGENT_CACHE_SIZE = 48
_agents: "OrderedDict[tuple, Agent]" = OrderedDict()


def model_name(*, structural: bool) -> str:
    cfg = get_config()
    if structural:
        return getattr(cfg, "rerank_llm", None) or cfg.llm
    return cfg.llm


def build_agent(output_type: type[T], system_prompt: str, *, structural: bool) -> Agent:
    """An agent for one planner pass, cached by schema, prompt, model and keys."""
    cfg = get_config()
    name = model_name(structural=structural)
    signature = (
        output_type.__name__,
        hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        name,
        cfg.openai_api_key,
        cfg.openrouter_api_key,
        cfg.openrouter_fallback_model,
        cfg.google_api_key,
        cfg.anthropic_api_key,
        cfg.ollama_base_url,
        cfg.ollama_api_key,
    )
    agent = _agents.get(signature)
    if agent is not None:
        _agents.move_to_end(signature)
        return agent
    apply_settings_to_process_env(cfg.as_runtime_settings())
    config_error = ai._get_missing_llm_key_error(name, cfg)
    if config_error:
        raise RuntimeError(config_error)
    native = ai._uses_native_output(name)
    agent = Agent(
        model=ai._build_transcript_model(cfg, name),
        output_type=NativeOutput(output_type, strict=True) if native else output_type,
        system_prompt=system_prompt,
        retries={"output": 2},
        model_settings={"max_tokens": MAX_OUTPUT_TOKENS} if native else None,
    )
    _agents[signature] = agent
    while len(_agents) > _AGENT_CACHE_SIZE:
        _agents.popitem(last=False)
    return agent


@dataclass
class PlanStats:
    """Calls, repairs and token use of one plan; stored in ``workflow_plan.stats``."""

    llm_calls: int = 0
    repairs: int = 0
    critique_fixes: int = 0
    stages: dict[str, dict[str, float]] = field(default_factory=dict)

    def record(self, stage: str, result: Any, seconds: float, *, failed: bool = False) -> None:
        self.llm_calls += 1
        entry = self.stages.setdefault(
            stage or "other", {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0}
        )
        entry["calls"] += 1
        entry["failed"] += int(failed)
        entry["seconds"] = round(entry["seconds"] + seconds, 2)
        usage = _usage(result)
        if usage is not None:
            entry["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
            entry["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)

    def as_dict(self, plan_seconds: float) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "repairs": self.repairs,
            "critique_fixes": self.critique_fixes,
            "plan_seconds": round(plan_seconds, 1),
            "stages": {name: dict(values) for name, values in self.stages.items()},
        }


def _usage(result: Any) -> Any:
    """Token usage of a run: a property on current pydantic-ai, a method on older runs and fakes."""
    usage = getattr(result, "usage", None)
    if usage is None or hasattr(usage, "input_tokens"):
        return usage
    if not callable(usage):
        return None
    try:
        return usage()
    except Exception:  # stats are best effort
        return None


def is_cancellation(error: BaseException) -> bool:
    return isinstance(error, TaskCancelled) or str(error) == "Task cancelled"


async def run(
    agent: Agent,
    prompt: str,
    *,
    timeout: float = CALL_TIMEOUT_SECONDS,
    stats: Optional[PlanStats] = None,
    stage: str = "",
) -> Any:
    """One model call through the shared runner; usage is recorded per stage."""
    started = time.monotonic()
    try:
        result = await ai._run_transcript_analysis(agent, prompt, timeout)
    except Exception:
        if stats is not None:
            stats.record(stage, None, time.monotonic() - started, failed=True)
        raise
    if stats is not None:
        stats.record(stage, result, time.monotonic() - started)
    return result


async def run_with_repair(
    agent: Agent,
    prompt: str,
    validate: Callable[[T], list[str]],
    *,
    timeout: float = CALL_TIMEOUT_SECONDS,
    stats: Optional[PlanStats] = None,
    stage: str = "",
) -> tuple[T, list[str]]:
    """Ask once; if the answer breaks rules, show it its issues and ask once more.

    The repair is a fresh prompt (original prompt, previous answer, issues)
    through the same runner, never a message-history continuation. The answer
    with fewer issues wins; what is left is the deterministic solver's job.
    A repair call that fails keeps the first answer: it was usable, only
    imperfect, and the solver handles imperfect.
    """
    first = (await run(agent, prompt, timeout=timeout, stats=stats, stage=stage)).output
    issues = validate(first)
    if not issues or len(issues) > MAX_REPAIRABLE_ISSUES:
        return first, issues
    repair = (
        prompt
        + "\n\nYOUR PREVIOUS ANSWER:\n"
        + first.model_dump_json()
        + "\n\nIT BREAKS THESE RULES:\n- "
        + "\n- ".join(issues)
        + "\n\nReturn the complete corrected answer in the same format. "
        "Change only what is needed to fix these problems."
    )
    if stats is not None:
        stats.repairs += 1
    try:
        second = (await run(agent, repair, timeout=timeout, stats=stats, stage=f"{stage}_repair")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        # Never the error text: provider bodies may contain transcript data.
        logger.warning("Workflow %s repair failed (%s); keeping the first answer", stage or "call", type(error).__name__)
        return first, issues
    second_issues = validate(second)
    if len(second_issues) <= len(issues):
        return second, second_issues
    return first, issues


async def gather_all(calls: Sequence[Awaitable[Any]]) -> list[Any]:
    """Run concurrently; on the first failure cancel the rest and raise it.

    For map passes that must not produce a partial result (every window or
    none): ``asyncio.gather`` would leave the siblings running.
    """
    tasks = [asyncio.ensure_future(call) for call in calls]
    if not tasks:
        return []
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    failed = next((task for task in tasks if task.done() and not task.cancelled() and task.exception()), None)
    if failed is not None:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise failed.exception()  # type: ignore[misc]
    return [task.result() for task in tasks]


async def run_retrying(
    call: Callable[[], Awaitable[Any]], *, attempts: int = 2, label: str = "call"
) -> Any:
    """``call()`` again once after any failure except cancellation.

    For passes where one bad answer must not sink the plan (outline windows,
    topic extraction windows). The last error propagates.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception as error:
            if is_cancellation(error) or attempt == attempts:
                raise
            # Never the error text: provider bodies may contain transcript data.
            logger.warning("Workflow %s failed (%s); retrying once", label, type(error).__name__)
    raise AssertionError("unreachable")
