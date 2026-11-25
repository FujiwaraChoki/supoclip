"""One agent turn: Claude Sonnet 5.5 with SupoClip's tools, streamed to the page.

The transcript in ``agent_messages`` is append-only and holds every content
block exactly as Claude returned it (thinking and fallback blocks included),
because the next request must send them back unchanged. A turn:

1. streams Claude's reply, publishing text as it arrives;
2. runs the tools it asked for (in parallel), publishing each start and result;
3. stops for the person's approval when a tool needs it (publish now, delete);
4. repeats until Claude answers without tools, or the step limit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from ..config import get_config
from . import events, store
from .api_client import AgentAPI
from .prompt import SYSTEM_PROMPT
from .timeutil import zone
from .tools import TOOL_DEFINITIONS, TOOLS_BY_NAME, ToolContext, ToolOutcome, approval_summary, run_tool

logger = logging.getLogger(__name__)

AGENT_MODEL = "claude-sonnet-5-5"
MAX_MODEL_CALLS = 24
MAX_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 300.0
TEXT_FLUSH_SECONDS = 0.06
DECLINED = "The person declined this action, so it did not run. Ask what they'd like instead if it's unclear."
INTERRUPTED = "This action was interrupted before it finished; its outcome is unknown. Check before retrying."


class TurnError(Exception):
    """A failure to show the person as is."""


def agent_model() -> str:
    return (os.getenv("AGENT_MODEL") or AGENT_MODEL).strip() or AGENT_MODEL


def agent_configured() -> bool:
    return bool(get_config().anthropic_api_key)


# ----------------------------------------------------------------------
# Transcript helpers
# ----------------------------------------------------------------------
def api_messages(rows: list[dict]) -> list[dict]:
    """The rows as Messages API turns: consecutive turns of one role are merged."""
    merged: list[dict] = []
    for row in rows:
        content = row["content"] if isinstance(row["content"], list) else []
        if not content:
            continue
        if merged and merged[-1]["role"] == row["role"]:
            if row["role"] == "user":
                # tool_result blocks must come first in a user turn.
                previous = merged[-1]["content"]
                results = [b for b in previous + content if b.get("type") == "tool_result"]
                others = [b for b in previous + content if b.get("type") != "tool_result"]
                merged[-1]["content"] = results + others
            else:
                merged[-1]["content"] = merged[-1]["content"] + content
        else:
            merged.append({"role": row["role"], "content": list(content)})
    return merged


def unanswered_tool_uses(rows: list[dict]) -> list[dict]:
    """tool_use blocks of the last assistant turn that no tool_result answers yet."""
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if row["role"] == "assistant":
            uses = [b for b in row["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
            answered = {
                b.get("tool_use_id")
                for later in rows[index + 1:]
                for b in (later["content"] or [])
                if isinstance(b, dict) and b.get("type") == "tool_result"
            }
            return [b for b in uses if b.get("id") not in answered]
    return []


def tool_result_block(tool_use_id: str, outcome: ToolOutcome) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": outcome.model_text()}
    if not outcome.ok:
        block["is_error"] = True
    return block


def card_entry(name: str, outcome: ToolOutcome, *, declined: bool = False) -> dict:
    entry: dict[str, Any] = {"name": name, "ok": outcome.ok}
    if outcome.card:
        entry["card"] = outcome.card
    if not outcome.ok:
        entry["error"] = outcome.model_text()[:400]
    if declined:
        entry["declined"] = True
    return entry


def input_brief(name: str, data: Any) -> dict:
    """The few input fields a step label needs (never the whole caption)."""
    if not isinstance(data, dict):
        return {}
    keep = ("project_id", "clip_id", "post_id", "platform", "when", "workflow", "url", "query", "action", "count", "topic")
    brief = {k: data[k] for k in keep if k in data and isinstance(data[k], (str, int, float, bool))}
    if isinstance(brief.get("url"), str) and brief["url"].startswith("upload://"):
        brief["url"] = "upload"
    return brief


# ----------------------------------------------------------------------
# The turn
# ----------------------------------------------------------------------
class Turn:
    def __init__(self, app, run: dict, conversation: dict, *, timezone_name: Optional[str]):
        self.app = app
        self.run = run
        self.run_id = run["id"]
        self.conversation = conversation
        self.conversation_id = conversation["id"]
        self.tz = zone(timezone_name)
        self.timezone_name = timezone_name
        self.usage = dict(run.get("usage") or {})
        self.api = AgentAPI(app, conversation["user_id"], conversation.get("team_id"), trace_id=f"agent-{self.run_id}")

    async def publish(self, event: str, data: dict) -> Optional[str]:
        return await events.publish(self.conversation_id, event, {"run_id": self.run_id, **data})

    def context(self) -> ToolContext:
        from .runtime import job_created

        return ToolContext(
            app=self.app,
            api=self.api,
            user_id=self.conversation["user_id"],
            team_id=self.conversation.get("team_id"),
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            tz=self.tz,
            on_job_created=job_created,
        )

    async def execute(self, uses: list[dict]) -> tuple[list[dict], dict]:
        """Run tool_use blocks concurrently; returns tool_result blocks and their cards."""
        ctx = self.context()

        async def one(block: dict) -> tuple[dict, dict]:
            name = block.get("name", "")
            await self.publish("tool_start", {"id": block["id"], "name": name, "input": input_brief(name, block.get("input"))})
            started = time.monotonic()
            outcome = await run_tool(ctx, name, block.get("input"))
            entry = card_entry(name, outcome)
            await self.publish("tool_end", {"id": block["id"], **entry})
            logger.info(
                "Agent tool %s %s in %.1fs", name, "ok" if outcome.ok else "failed", time.monotonic() - started
            )
            return tool_result_block(block["id"], outcome), entry

        pairs = await asyncio.gather(*(one(block) for block in uses))
        return [p[0] for p in pairs], {block["id"]: pair[1] for block, pair in zip(uses, pairs)}

    async def go(self) -> None:
        await store.update_run(self.run_id, status="running")
        await self.publish("run", {"status": "running"})
        try:
            await self._loop()
        except asyncio.CancelledError:
            run = await store.get_run(self.run_id)
            if run and run.get("cancel_requested"):
                await store.update_run(self.run_id, status="cancelled", usage=self.usage)
                await self.publish("done", {"status": "cancelled"})
            else:
                await store.update_run(
                    self.run_id, status="interrupted", usage=self.usage,
                    error="The agent was interrupted. Send your message again.",
                )
                await self.publish("error", {"message": "The agent was interrupted. Send your message again."})
            raise
        except TurnError as exc:
            await store.update_run(self.run_id, status="failed", error=str(exc), usage=self.usage)
            await self.publish("error", {"message": str(exc)})
        except Exception:  # noqa: BLE001
            logger.exception("Agent turn %s failed", self.run_id)
            message = "Something went wrong on our side. Your work so far is saved; send your message again."
            await store.update_run(self.run_id, status="failed", error=message, usage=self.usage)
            await self.publish("error", {"message": message})

    async def _loop(self) -> None:
        import anthropic

        config = get_config()
        if not config.anthropic_api_key:
            raise TurnError("The agent isn't set up on this server (no Anthropic API key).")
        client = anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=2
        )
        system = [{"type": "text", "text": SYSTEM_PROMPT}]
        for _step in range(MAX_MODEL_CALLS):
            if await store.heartbeat(self.run_id):
                await store.update_run(self.run_id, status="cancelled", usage=self.usage)
                await self.publish("done", {"status": "cancelled"})
                return
            rows = await store.list_messages(self.conversation_id)
            # The reply being written follows this message: a chat opened mid-reply shows it only
            # while the transcript still ends there (not twice once it is saved).
            self._after_message_id = rows[-1]["id"] if rows else None
            message = await self._call_model(client, system, api_messages(rows))
            content = [block.to_dict(mode="json") for block in message.content]
            if content:
                await store.append_message(self.conversation_id, "assistant", content, run_id=self.run_id)
            await events.clear_partial(self.conversation_id)
            stop = message.stop_reason
            uses = [b for b in content if b.get("type") == "tool_use"]
            if stop == "pause_turn":
                continue
            if stop == "refusal":
                raise TurnError("I can't help with that request. Try asking another way.")
            if stop == "max_tokens" and uses:
                # A cut-off tool call must not run; answer it so the transcript stays valid.
                outcome = ToolOutcome("The request was cut off before it finished. Nothing ran.", ok=False)
                await store.append_message(
                    self.conversation_id, "user", [tool_result_block(b["id"], outcome) for b in uses],
                    run_id=self.run_id,
                    display={"kind": "tool_results", "cards": {b["id"]: card_entry(b["name"], outcome) for b in uses}},
                )
                raise TurnError("My answer got too long and was cut off. Ask for a smaller piece at a time.")
            if not uses:
                break
            waiting = [b for b in uses if TOOLS_BY_NAME.get(b.get("name")) and TOOLS_BY_NAME[b["name"]].needs_approval(b.get("input") or {})]
            ready = [b for b in uses if b not in waiting]
            results, cards = await self.execute(ready) if ready else ([], {})
            if waiting:
                await self._ask_approval(waiting, results, cards)
                return
            await store.append_message(
                self.conversation_id, "user", results, run_id=self.run_id,
                display={"kind": "tool_results", "cards": cards},
            )
        else:
            await self.publish("notice", {"text": "step_limit"})
        await self._finish()

    async def _call_model(self, client, system: list[dict], messages: list[dict]):
        import anthropic

        pending_text: list[str] = []
        written: list[str] = []
        last_flush = time.monotonic()

        async def flush() -> None:
            nonlocal last_flush
            if pending_text:
                chunk = "".join(pending_text)
                event_id = await self.publish("text", {"text": chunk})
                pending_text.clear()
                written.append(chunk)
                # Kept so a chat opened mid-reply shows it (the tab may have been closed).
                await events.set_partial(
                    self.conversation_id, self.run_id, "".join(written), event_id,
                    after_message_id=getattr(self, "_after_message_id", None),
                )
            last_flush = time.monotonic()

        try:
            async with client.beta.messages.stream(
                model=agent_model(),
                max_tokens=MAX_TOKENS,
                system=system,
                tools=TOOL_DEFINITIONS,
                messages=messages,
                thinking={"type": "adaptive"},
                # Multi-step tool use: medium keeps turns quick without losing judgment.
                output_config={"effort": "medium"},
                # Caches the system prompt, tools and the conversation so far between steps.
                cache_control={"type": "ephemeral"},
                # A request the safety classifiers decline is re-run on Anthropic's fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if event.type == "text":
                        pending_text.append(event.text)
                        if time.monotonic() - last_flush >= TEXT_FLUSH_SECONDS:
                            await flush()
                await flush()
                message = await stream.get_final_message()
        except anthropic.RateLimitError:
            raise TurnError("The agent is busy right now. Try again in a minute.") from None
        except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.InternalServerError):
            raise TurnError("Couldn't reach the AI service. Try again.") from None
        except anthropic.BadRequestError as exc:
            logger.error("Agent request rejected: %s", getattr(exc, "message", exc))
            raise TurnError("The AI service couldn't handle this conversation. Start a new chat to continue.") from None
        except anthropic.APIStatusError as exc:
            logger.error("Agent request failed with HTTP %s", exc.status_code)
            raise TurnError("The AI service couldn't handle that request. Try again.") from None
        usage = message.usage
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            self.usage[key] = int(self.usage.get(key) or 0) + int(getattr(usage, key, 0) or 0)
        self.usage["model_calls"] = int(self.usage.get("model_calls") or 0) + 1
        return message

    async def _ask_approval(self, waiting: list[dict], results: list[dict], cards: dict) -> None:
        items = [{"id": b["id"], **approval_summary(b["name"], b.get("input") or {})} for b in waiting]
        await store.update_run(
            self.run_id,
            status="awaiting_approval",
            pending={"results": results, "cards": cards, "waiting": waiting, "items": items},
            usage=self.usage,
        )
        await self.publish("approval", {"items": items})

    async def _finish(self) -> None:
        await store.update_run(self.run_id, status="completed", usage=self.usage)
        await self.publish("done", {"status": "completed"})
        logger.info(
            "Agent turn %s done: %s model calls, %s in / %s out / %s cached tokens",
            self.run_id, self.usage.get("model_calls"), self.usage.get("input_tokens"),
            self.usage.get("output_tokens"), self.usage.get("cache_read_input_tokens"),
        )

    async def resume_after_approval(self, approved: bool) -> None:
        """Run (or decline) the actions that waited for approval, then carry on."""
        pending = self.run.get("pending") or {}
        waiting = pending.get("waiting") or []
        results = list(pending.get("results") or [])
        cards = dict(pending.get("cards") or {})
        await store.update_run(self.run_id, status="running", pending=None)
        await self.publish("run", {"status": "running"})
        try:
            if approved:
                done, done_cards = await self.execute(waiting)
                results.extend(done)
                cards.update(done_cards)
            else:
                for block in waiting:
                    outcome = ToolOutcome(DECLINED, ok=False)
                    results.append(tool_result_block(block["id"], outcome))
                    cards[block["id"]] = card_entry(block["name"], outcome, declined=True)
                    await self.publish("tool_end", {"id": block["id"], **cards[block["id"]]})
            await store.append_message(
                self.conversation_id, "user", results, run_id=self.run_id,
                display={"kind": "tool_results", "cards": cards},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Agent approval for %s failed", self.run_id)
            raise
        await self.go()


def declined_results(pending: dict) -> tuple[list[dict], dict]:
    """tool_result blocks for a pending approval the person moved past by writing a new message."""
    results = list(pending.get("results") or [])
    cards = dict(pending.get("cards") or {})
    for block in pending.get("waiting") or []:
        outcome = ToolOutcome("The person didn't approve this; they wrote a new message instead. It did not run.", ok=False)
        results.append(tool_result_block(block["id"], outcome))
        cards[block["id"]] = card_entry(block["name"], outcome, declined=True)
    return results, cards


def interrupted_results(uses: list[dict]) -> tuple[list[dict], dict]:
    outcome = ToolOutcome(INTERRUPTED, ok=False)
    return (
        [tool_result_block(b["id"], outcome) for b in uses],
        {b["id"]: card_entry(b.get("name", ""), outcome) for b in uses},
    )
