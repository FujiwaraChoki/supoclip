"""The agent home screen: conversations, turns, approvals and the live event stream.

Session auth only (the signed headers the web app sends): an API key drives
SupoClip through the REST API or MCP, not through this chat.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ...agent import events, runtime, store, timeline
from ...agent.access import agent_access, daily_turn_limit, first_name, home_mode, offer_choice
from ...agent.attachments import AttachmentError, parse_attachments
from ...agent.loop import agent_model
from ...auth_headers import AuthContext, resolve_auth_context
from ...config import get_config
from ...database import get_db
from ...feature_flags import client_platform

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])

MAX_MESSAGE_CHARS = 4000
_TIMEZONE = re.compile(r"^[A-Za-z0-9_+/-]{1,64}$")
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")


async def _ctx(request: Request, db: AsyncSession) -> AuthContext:
    return await resolve_auth_context(request, db, get_config(), session_only=True)


async def _require_access(request: Request, db: AsyncSession, ctx: AuthContext, *, finishing: bool = False) -> None:
    """``finishing``: the request continues a turn already counted (an approval), so a Pro trial
    that ran out on that very turn may still finish it."""
    access = await agent_access(db, ctx.user_id, ctx.team_id, client_platform(request))
    if finishing and access["reason"] == "trial_used":
        return
    if not access["available"]:
        messages = {
            "disabled": "The agent isn't available right now.",
            "not_configured": "The agent isn't set up on this server.",
            "plan": "The agent is part of the Creator plan.",
            "trial_used": "You've used your free Agent messages. Agent is part of the Creator plan.",
        }
        raise HTTPException(
            status_code=402 if access["reason"] in {"plan", "trial_used"} else 403,
            detail={"code": f"AGENT_{(access['reason'] or 'unavailable').upper()}", "message": messages.get(access["reason"], "Unavailable.")},
        )


async def _conversation(conversation_id: str, ctx: AuthContext) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", conversation_id):
        raise HTTPException(404, "Conversation not found")
    conversation = await store.get_conversation(conversation_id, ctx.user_id)
    if conversation is None or conversation.get("team_id") != ctx.team_id:
        raise HTTPException(404, "Conversation not found")
    return conversation


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Expected a JSON body") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Expected a JSON object")
    return payload


def _message_fields(payload: dict) -> dict:
    try:
        return _parse_message_fields(payload)
    except AttachmentError as exc:
        raise HTTPException(422, {"code": "AGENT_ATTACHMENT", "message": str(exc)}) from None


def _parse_message_fields(payload: dict) -> dict:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(422, "Write a message")
    message = message.strip()
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(422, f"Keep messages under {MAX_MESSAGE_CHARS} characters")
    attachments = parse_attachments(payload.get("attachments"))
    timezone_name = payload.get("timezone") if isinstance(payload.get("timezone"), str) else None
    if timezone_name and not _TIMEZONE.match(timezone_name):
        timezone_name = None
    locale = payload.get("locale") if isinstance(payload.get("locale"), str) else None
    if locale and not _LOCALE.match(locale):
        locale = None
    page = payload.get("page") if isinstance(payload.get("page"), str) else None
    return {
        "message": message,
        "attachments": attachments,
        "timezone_name": timezone_name,
        "locale": locale,
        "page": page[:200] if page else None,
    }


async def _start(request: Request, db: AsyncSession, ctx: AuthContext, conversation: dict, fields: dict) -> dict:
    if await store.turns_today(ctx.user_id) >= daily_turn_limit():
        raise HTTPException(
            429,
            {"code": "AGENT_DAILY_LIMIT", "message": "You've reached today's agent limit. It resets within 24 hours."},
        )
    name = await first_name(db, ctx.user_id)
    await db.close()
    try:
        return await runtime.start_user_turn(conversation, first_name=name, **fields)
    except runtime.Busy:
        raise HTTPException(409, {"code": "AGENT_BUSY", "message": "Wait for the current reply to finish."}) from None


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    """Whether the agent home is offered to the caller, and what they picked."""
    ctx = await _ctx(request, db)
    access = await agent_access(db, ctx.user_id, ctx.team_id, client_platform(request))
    mode = await home_mode(db, ctx.user_id)
    return {
        **access,
        "home_mode": mode,
        "offer_choice": await offer_choice(db, ctx.user_id, access, mode),
        "model": agent_model() if access["available"] else None,
        "daily_limit": daily_turn_limit(),
    }


@router.get("/conversations")
async def list_conversations(request: Request, db: AsyncSession = Depends(get_db), limit: int = 30):
    ctx = await _ctx(request, db)
    await db.close()
    return {"conversations": await store.list_conversations(ctx.user_id, ctx.team_id, limit)}


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await _require_access(request, db, ctx)
    fields = _message_fields(await _body(request))
    conversation = await store.create_conversation(ctx.user_id, ctx.team_id, None)
    conversation["user_id"] = ctx.user_id
    try:
        run = await _start(request, db, ctx, conversation, fields)
    except BaseException:
        # Nothing was said yet: don't leave an empty chat behind.
        await store.delete_conversation(conversation["id"], ctx.user_id)
        raise
    return {"conversation": await store.get_conversation(conversation["id"], ctx.user_id), "run": run}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await db.close()
    conversation = await _conversation(conversation_id, ctx)
    # The stream position first: an event written while the transcript loads is replayed, not lost.
    last_event_id = await events.latest_id(conversation_id)
    rows = await store.list_messages(conversation_id)
    open_run = await store.open_run(conversation_id)
    jobs = await store.list_jobs(conversation_id)
    from ...agent.tools import _job_card

    # A reply being written while nobody watched (closed tab): shown as is, then followed live
    # from the exact event it reached.
    partial = None
    if open_run and open_run.get("status") in {"queued", "running"}:
        saved = await events.get_partial(conversation_id)
        last_message_id = rows[-1]["id"] if rows else None
        # Only while the transcript still ends where this reply began: once the reply (or the next
        # step) is saved, the saved rows and the stream carry it.
        if (
            saved and saved.get("run_id") == open_run["id"] and saved.get("text")
            and saved.get("after_message_id") == last_message_id
        ):
            partial = {"text": saved["text"], "event_id": saved.get("event_id")}

    return {
        "conversation": conversation,
        "items": timeline.build(rows, open_run),
        "run": open_run and {k: open_run[k] for k in ("id", "status", "pending") if k in open_run},
        "jobs": [_job_card(job) for job in jobs],
        "last_event_id": last_event_id,
        "partial": partial,
    }


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await db.close()
    await _conversation(conversation_id, ctx)
    payload = await _body(request)
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(422, "Give the chat a name")
    await store.rename_conversation(conversation_id, ctx.user_id, title.strip())
    return {"ok": True}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await db.close()
    await _conversation(conversation_id, ctx)
    await runtime.request_stop(conversation_id)
    await store.delete_conversation(conversation_id, ctx.user_id)
    return {"ok": True}


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def send_message(conversation_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    conversation = await _conversation(conversation_id, ctx)
    await _require_access(request, db, ctx)
    fields = _message_fields(await _body(request))
    return {"run": await _start(request, db, ctx, conversation, fields)}


@router.post("/conversations/{conversation_id}/stop")
async def stop(conversation_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await db.close()
    await _conversation(conversation_id, ctx)
    return {"stopped": await runtime.request_stop(conversation_id)}


@router.post("/conversations/{conversation_id}/approval")
async def approval(conversation_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Approve or decline the actions the agent is waiting on (publish now, delete)."""
    ctx = await _ctx(request, db)
    conversation = await _conversation(conversation_id, ctx)
    payload = await _body(request)
    approved = payload.get("approved")
    if not isinstance(approved, bool):
        raise HTTPException(422, "approved must be true or false")
    if approved:
        await _require_access(request, db, ctx, finishing=True)
    await db.close()
    run = await store.open_run(conversation_id)
    if not run or run["status"] != "awaiting_approval" or (payload.get("run_id") and payload["run_id"] != run["id"]):
        raise HTTPException(409, {"code": "NOTHING_TO_APPROVE", "message": "There's nothing waiting for approval."})
    timezone_name = payload.get("timezone") if isinstance(payload.get("timezone"), str) else None
    if timezone_name and not _TIMEZONE.match(timezone_name):
        timezone_name = None
    if not await runtime.resume_approval(conversation, run, approved, timezone_name):
        raise HTTPException(409, {"code": "NOTHING_TO_APPROVE", "message": "That was already answered."})
    return {"ok": True}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await db.close()
    if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
        raise HTTPException(404, "Not found")
    if not await store.cancel_job(job_id, ctx.user_id):
        raise HTTPException(409, "That has already finished.")
    return {"ok": True}


@router.get("/conversations/{conversation_id}/events")
async def stream_events(
    conversation_id: str, request: Request, db: AsyncSession = Depends(get_db), after: Optional[str] = None
):
    """SSE: everything that happens in the chat after ``after`` (or Last-Event-ID)."""
    ctx = await _ctx(request, db)
    await db.close()
    await _conversation(conversation_id, ctx)
    cursor = request.headers.get("last-event-id") or after or "$"
    if cursor != "$" and not re.fullmatch(r"\d+-\d+", cursor):
        cursor = "$"
    if cursor == "$":
        cursor = await events.latest_id(conversation_id)

    async def generator():
        async for event_id, event, data in events.listen(conversation_id, cursor):
            if await request.is_disconnected():
                return
            if event == "ping":
                yield {"event": "ping", "data": "{}"}
                continue
            yield {"id": event_id, "event": event, "data": json.dumps(data, default=str)}

    return EventSourceResponse(generator(), ping=20)


@router.get("/memories")
async def list_memories(request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await db.close()
    return {"memories": await store.list_memories(ctx.user_id)}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _ctx(request, db)
    await db.close()
    if not await store.delete_memory(ctx.user_id, memory_id):
        raise HTTPException(404, "Not found")
    return {"ok": True}
