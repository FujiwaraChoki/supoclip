"""Workflow planner entry point.

``plan_workflow`` numbers the transcript into units, reads (or reuses) the
source outline and hands both to the workflow's planner. It owns the
contract every caller relies on: user-facing failures are ``WorkflowError``s
with a code and an English message, cancellation passes through untouched,
and the whole plan is bounded by ``WORKFLOW_PLAN_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from ..config import get_config
from ..utils.async_helpers import run_in_thread
from .contracts import PlanContext, WorkflowResult
from .errors import WorkflowError
from .language import language_name
from .llm import PlanStats, is_cancellation
from .options import workflow_runtime_minimum_error
from .outline import get_outline
from .planning import PlanInput, plan_language
from .recap import plan_recap
from .series import plan_series
from .topic import plan_topic
from .trailer import plan_trailer
from .units import build_units

logger = logging.getLogger(__name__)

Planner = Callable[[PlanInput], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]

PLANNERS: dict[str, Planner] = {
    "series": plan_series,
    "topic": plan_topic,
    "recap": plan_recap,
    "trailer": plan_trailer,
}

_PLAN_FAILED = "The AI editor couldn't finish this plan. Please try again."


async def plan_workflow(ctx: PlanContext) -> WorkflowResult:
    """Plan the segments for a non-highlights workflow.

    Called only for ``workflow != "highlights"``. Raises WorkflowError for
    user-facing failures and lets TaskCancelled / Exception("Task cancelled")
    propagate. Wall clock is bounded by WORKFLOW_PLAN_TIMEOUT_SECONDS; on
    timeout it raises WorkflowError("plan_failed", ...).
    """
    workflow = ctx.spec.workflow
    if workflow not in PLANNERS:
        raise ValueError(f"plan_workflow does not plan {workflow!r}")
    minimum = workflow_runtime_minimum_error(ctx.spec, ctx.source_seconds)
    if minimum is not None:
        raise minimum

    timeout = float(get_config().workflow_plan_timeout_seconds)
    started = time.monotonic()
    stats = PlanStats()
    try:
        async with asyncio.timeout(timeout):
            result = await _plan(ctx, stats)
    except TimeoutError as error:
        logger.error("Workflow %s plan exceeded %.0fs", workflow, timeout)
        raise WorkflowError("plan_failed", _PLAN_FAILED) from error
    except WorkflowError as error:
        logger.info("Workflow %s plan ended with %s", workflow, error.code)
        raise
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.error("Workflow %s plan failed (%s)", workflow, type(error).__name__, exc_info=True)
        raise WorkflowError("plan_failed", _PLAN_FAILED) from error
    result.plan["stats"] = stats.as_dict(time.monotonic() - started)
    logger.info(
        "Planned %s: %d segment(s), %d model call(s), %d repair(s), %.0fs",
        workflow,
        len(result.segments),
        stats.llm_calls,
        stats.repairs,
        time.monotonic() - started,
    )
    return result


async def _plan(ctx: PlanContext, stats: PlanStats) -> WorkflowResult:
    await ctx.progress(51, "Reading the whole video...")
    index = await run_in_thread(build_units, ctx.transcript_data, ctx.transcript_text)
    language = plan_language(index, ctx.transcript_data)
    outline = await get_outline(ctx, index, language_name(language), stats)
    plan_input = PlanInput(
        ctx=ctx,
        index=index,
        outline=outline,
        language=plan_language(index, ctx.transcript_data, outline.outline.language),
        stats=stats,
    )
    segments, plan = await PLANNERS[ctx.spec.workflow](plan_input)
    if not segments:
        # Every planner raises its own error before this; an empty plan is a bug.
        raise WorkflowError("plan_failed", _PLAN_FAILED)
    return WorkflowResult(
        segments=segments,
        plan=plan,
        summary=outline.outline.one_line or None,
        key_topics=list(outline.outline.key_topics),
        units_fingerprint=index.fingerprint,
    )
