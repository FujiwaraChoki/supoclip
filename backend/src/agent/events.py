"""Live events for one conversation, through a Redis stream.

The turn appends events (text as it is written, each tool starting and
finishing, an approval request, the end of the turn) and the browser reads
them over SSE from any API process. A reconnecting browser passes the last id
it saw and misses nothing. Background jobs post to the same stream, so a page
that is open sees an edit finish without polling.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

from ..config import get_config
from ..workers.job_queue import JobQueue

logger = logging.getLogger(__name__)

STREAM_TTL_SECONDS = 6 * 3600
STREAM_MAX_LEN = 5000
PARTIAL_TTL_SECONDS = 15 * 60
# Events after which a turn's stream has nothing more to say.
TERMINAL = {"done", "error", "approval"}


def stream_key(conversation_id: str) -> str:
    return f"agent:conversation:{conversation_id}:events"


async def publish(conversation_id: str, event: str, data: dict[str, Any]) -> Optional[str]:
    """Append an event; never raises (a lost live event is shown on the next load)."""
    try:
        redis = await JobQueue.get_pool()
        key = stream_key(conversation_id)
        event_id = await redis.xadd(
            key,
            {"event": event, "data": json.dumps(data, default=str)},
            maxlen=STREAM_MAX_LEN,
            approximate=True,
        )
        await redis.expire(key, STREAM_TTL_SECONDS)
        return event_id.decode() if isinstance(event_id, bytes) else str(event_id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not publish agent event %s", event, exc_info=True)
        return None


def partial_key(conversation_id: str) -> str:
    return f"agent:conversation:{conversation_id}:partial"


async def set_partial(
    conversation_id: str, run_id: str, text: str, event_id: Optional[str], *, after_message_id: Optional[int] = None
) -> None:
    """The reply being written right now, with the stream id it reaches. A chat opened mid-reply
    shows this text and listens from that id, so nothing is missed or shown twice."""
    if not event_id:
        return
    try:
        redis = await JobQueue.get_pool()
        await redis.set(
            partial_key(conversation_id),
            json.dumps({"run_id": run_id, "text": text, "event_id": event_id, "after_message_id": after_message_id}),
            ex=PARTIAL_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not save the reply in progress", exc_info=True)


async def clear_partial(conversation_id: str) -> None:
    try:
        redis = await JobQueue.get_pool()
        await redis.delete(partial_key(conversation_id))
    except Exception:  # noqa: BLE001
        logger.warning("Could not clear the reply in progress", exc_info=True)


async def get_partial(conversation_id: str) -> Optional[dict[str, Any]]:
    try:
        redis = await JobQueue.get_pool()
        raw = await redis.get(partial_key(conversation_id))
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def latest_id(conversation_id: str) -> str:
    """The id of the newest event, so a new listener starts after history it already loaded."""
    try:
        redis = await JobQueue.get_pool()
        entries = await redis.xrevrange(stream_key(conversation_id), count=1)
        if entries:
            entry_id = entries[0][0]
            return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not read the agent event stream", exc_info=True)
    return "0-0"


async def listen(
    conversation_id: str, after: str, *, block_ms: int = 15000
) -> AsyncIterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(id, event, data)`` after ``after``; yields ``("", "ping", {})`` while idle.

    A blocking read holds its connection, so each listener has its own client
    instead of borrowing one from the job queue's pool.
    """
    from redis import asyncio as redis

    config = get_config()
    client = redis.Redis(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, decode_responses=True
    )
    key = stream_key(conversation_id)
    cursor = after or "0-0"
    try:
        while True:
            response = await client.xread({key: cursor}, count=200, block=block_ms)
            if not response:
                yield "", "ping", {}
                continue
            for _stream, entries in response:
                for entry_id, fields in entries:
                    cursor = entry_id
                    try:
                        data = json.loads(fields.get("data") or "{}")
                    except ValueError:
                        data = {}
                    yield entry_id, fields.get("event", "message"), data
    finally:
        await client.aclose()
