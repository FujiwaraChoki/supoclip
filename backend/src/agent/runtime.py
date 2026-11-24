"""Starts agent turns and steps background jobs inside the API process.

A turn runs as an asyncio task next to the requests, calling the app
in-process. Everything it needs to survive is in PostgreSQL (transcript,
runs, jobs) and Redis (live events), so a restart loses at most the turn in
flight: the reaper marks it interrupted and the person can send again.

One poller per API process claims due jobs with ``FOR UPDATE SKIP LOCKED``,
so several processes (or two serving releases during a rollout) share the
work without stepping one job twice.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from . import events, store
from .attachments import content_blocks, previews
from .loop import Turn, declined_results, interrupted_results, unanswered_tool_uses
from .prompt import context_block, notice_block

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0
REAP_EVERY_SECONDS = 30.0
STALE_RUN_SECONDS = 90
MAX_JOBS_PER_POLL = 8

_app: Any = None
_poller: Optional[asyncio.Task] = None
_tasks: set[asyncio.Task] = set()
# The turn each local task runs, so Stop can cancel it at once.
_running: dict[str, asyncio.Task] = {}
_wake = asyncio.Event()


class Busy(Exception):
    """The conversation already has a turn running."""


def _launch(coro, run_id: Optional[str] = None) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    if run_id:
        _running[run_id] = task
        task.add_done_callback(lambda _t: _running.pop(run_id, None))
    return task


def start(app) -> None:
    """Called from the API's startup. Safe to call more than once."""
    global _app, _poller
    _app = app
    if _poller is None or _poller.done():
        _poller = asyncio.create_task(_poll_forever())


async def stop() -> None:
    global _poller
    if _poller is not None:
        _poller.cancel()
        try:
            await _poller
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _poller = None
    for task in list(_tasks):
        task.cancel()


def bind(app) -> None:
    """Remember the app without starting the poller (tests, one-off scripts)."""
    global _app
    _app = app


async def job_created(_job: dict) -> None:
    _wake.set()


# ----------------------------------------------------------------------
# Turns
# ----------------------------------------------------------------------
async def _settle_open_turn(conversation_id: str) -> tuple[list[dict], dict]:
    """Close a turn left waiting for approval (the person moved on) and answer any
    tool call that never got a result, so the transcript stays valid."""
    open_run = await store.open_run(conversation_id)
    if open_run and open_run["status"] in {"queued", "running"}:
        raise Busy()
    if open_run and open_run["status"] == "awaiting_approval":
        claimed = await store.claim_approval(open_run["id"])
        if claimed:
            results, cards = declined_results(open_run.get("pending") or {})
            await store.update_run(open_run["id"], status="completed", pending=None)
            await events.publish(conversation_id, "done", {"run_id": open_run["id"], "status": "declined"})
            return results, cards
    rows = await store.list_messages(conversation_id)
    unanswered = unanswered_tool_uses(rows)
    if unanswered:
        return interrupted_results(unanswered)
    return [], {}


def _title_from(text_value: str) -> str:
    words = " ".join(text_value.split())
    if len(words) <= 60:
        return words
    cut = words[:60].rsplit(" ", 1)[0]
    return f"{cut}…"


async def start_user_turn(
    conversation: dict,
    *,
    message: str,
    attachments: list[dict],
    timezone_name: Optional[str],
    locale: Optional[str],
    page: Optional[str],
    first_name: Optional[str],
) -> dict:
    if _app is None:
        raise RuntimeError("Agent runtime is not started")
    conversation_id = conversation["id"]
    results, cards = await _settle_open_turn(conversation_id)
    run = await store.create_run(conversation_id, conversation["user_id"], "user")
    if run is None:
        raise Busy()
    memories = [m["note"] for m in await store.list_memories(conversation["user_id"])]
    context = context_block(
        timezone_name=timezone_name, locale=locale, memories=memories, page=page, attachments=attachments,
        first_name=first_name,
    )
    files = content_blocks(attachments)
    content = [*results, {"type": "text", "text": context}, *files, {"type": "text", "text": message}]
    display: dict[str, Any] = {"kind": "user", "text": message}
    if attachments:
        display["attachments"] = previews(attachments)
    if cards:
        display["cards"] = cards
    await store.append_message(conversation_id, "user", content, run_id=run["id"], display=display)
    if not conversation.get("title"):
        await store.set_title_if_missing(conversation_id, _title_from(message))
    turn = Turn(_app, run, conversation, timezone_name=timezone_name)
    _launch(_guarded(turn, turn.go()), turn.run_id)
    return run


async def resume_approval(conversation: dict, run: dict, approved: bool, timezone_name: Optional[str]) -> bool:
    if _app is None:
        raise RuntimeError("Agent runtime is not started")
    claimed = await store.claim_approval(run["id"])
    if not claimed:
        return False
    turn = Turn(_app, claimed, conversation, timezone_name=timezone_name)
    _launch(_guarded(turn, turn.resume_after_approval(approved)), turn.run_id)
    return True


async def start_followup_turn(job: dict, text_value: str, notice: dict, *, timezone_name: Optional[str]) -> bool:
    """Wake the agent in the chat because background work finished. False: try later."""
    if _app is None:
        return False
    conversation = await store.get_conversation(job["conversation_id"], job["user_id"])
    if conversation is None:
        return True  # The chat was deleted; nothing to wake.
    try:
        results, cards = await _settle_open_turn(conversation["id"])
    except Busy:
        return False
    open_run = await store.open_run(conversation["id"])
    if open_run:
        return False
    run = await store.create_run(conversation["id"], conversation["user_id"], "followup")
    if run is None:
        return False
    memories = [m["note"] for m in await store.list_memories(conversation["user_id"])]
    context = context_block(timezone_name=timezone_name, locale=None, memories=memories)
    content = [*results, {"type": "text", "text": context}, {"type": "text", "text": notice_block(text_value)}]
    display = {"kind": "notice", "notice": notice, **({"cards": cards} if cards else {})}
    await store.append_message(conversation["id"], "user", content, run_id=run["id"], display=display)
    await events.publish(conversation["id"], "notice", {"run_id": run["id"], "notice": notice})
    turn = Turn(_app, run, conversation, timezone_name=timezone_name)
    _launch(_guarded(turn, turn.go()), turn.run_id)
    return True


async def _guarded(turn: Turn, coro) -> None:
    """Run a turn with a heartbeat. Stop (from any process) cancels it mid-stream."""
    main = asyncio.current_task()

    async def beat() -> None:
        while True:
            await asyncio.sleep(15)
            try:
                if await store.heartbeat(turn.run_id):
                    if main is not None:
                        main.cancel()
                    return
            except Exception:  # noqa: BLE001
                logger.warning("Agent heartbeat failed", exc_info=True)

    beater = asyncio.create_task(beat())
    try:
        await coro
    except asyncio.CancelledError:
        pass  # Turn.go recorded how it ended.
    finally:
        beater.cancel()


async def request_stop(conversation_id: str) -> bool:
    open_run = await store.open_run(conversation_id)
    if not open_run:
        return False
    if open_run["status"] == "awaiting_approval":
        _settled = await _settle_open_turn(conversation_id)
        # The declined results are answered on the next message; record them now.
        results, cards = _settled
        if results:
            await store.append_message(
                conversation_id, "user", results, run_id=open_run["id"], display={"kind": "tool_results", "cards": cards}
            )
        return True
    await store.update_run(open_run["id"], cancel_requested=True)
    task = _running.get(open_run["id"])
    if task is not None:
        task.cancel()
    return True


# ----------------------------------------------------------------------
# Poller
# ----------------------------------------------------------------------
async def _poll_forever() -> None:
    from .jobs import step

    last_reap = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            now = loop.time()
            if now - last_reap > REAP_EVERY_SECONDS:
                last_reap = now
                for run in await store.stale_runs(STALE_RUN_SECONDS):
                    await events.publish(
                        run["conversation_id"], "error",
                        {"run_id": run["id"], "message": "The agent was interrupted. Send your message again."},
                    )
            jobs = await store.claim_due_jobs(limit=MAX_JOBS_PER_POLL)
            if jobs:
                await asyncio.gather(*(step(_app, job) for job in jobs), return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Agent poller iteration failed", exc_info=True)
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
