"""Rate limits, concurrency caps and typed limit errors for the creator agent.

Turn limits (daily, burst, Pro trial and per-user concurrent active turns) are
enforced atomically in PostgreSQL under a per-user advisory lock using the
durable ``agent_turn_events`` ledger, so deleting conversations or racing
requests across chats cannot bypass them.

Endpoint request rates and open SSE event streams are tracked in Redis (shared
across API processes), with safe fallbacks if Redis is temporarily unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import anyio
from fastapi import HTTPException

from ..workers.job_queue import JobQueue

logger = logging.getLogger(__name__)

REDIS_OP_TIMEOUT_SECONDS = 1.0

DEFAULT_DAILY_TURNS = 150
DEFAULT_BURST_TURNS = 10
DEFAULT_BURST_WINDOW_SECONDS = 60
DEFAULT_MAX_CONCURRENT_TURNS = 2

DEFAULT_REQUEST_LIMIT = 120
DEFAULT_REQUEST_WINDOW_SECONDS = 60
DEFAULT_WRITE_LIMIT = 30
DEFAULT_WRITE_WINDOW_SECONDS = 60

DEFAULT_MAX_SSE_STREAMS = 6
SSE_SLOT_TTL_SECONDS = 60
SSE_SLOT_REFRESH_SECONDS = 20

_ENDPOINT_KEY = "agent:rate:{bucket}:{user_id}:{window_start}"
_SSE_KEY = "agent:sse:{user_id}"

# Atomic fixed-window counter: guarantees the key always gets an expiry even if a
# process dies between INCR and EXPIRE.
_WINDOW_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 or redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return count
"""

# Atomic expiring-slot set for concurrent SSE streams per user.
_SSE_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', tonumber(ARGV[1]))
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then
    return 0
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[1]) + tonumber(ARGV[2]), ARGV[4])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) + 30)
return 1
"""

# Process-local fallback for SSE stream tracking when Redis is unavailable or stubbed.
_local_sse_slots: dict[str, dict[str, float]] = {}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def daily_turn_limit() -> int:
    return _env_int("AGENT_DAILY_TURN_LIMIT", DEFAULT_DAILY_TURNS)


def burst_turn_limit() -> int:
    return _env_int("AGENT_BURST_TURN_LIMIT", DEFAULT_BURST_TURNS)


def burst_window_seconds() -> int:
    return _env_int("AGENT_BURST_WINDOW_SECONDS", DEFAULT_BURST_WINDOW_SECONDS)


def max_concurrent_turns() -> int:
    return _env_int("AGENT_MAX_CONCURRENT_TURNS", DEFAULT_MAX_CONCURRENT_TURNS)


def request_rate_limit() -> int:
    return _env_int("AGENT_REQUEST_RATE_LIMIT", DEFAULT_REQUEST_LIMIT)


def request_window_seconds() -> int:
    return _env_int("AGENT_REQUEST_WINDOW_SECONDS", DEFAULT_REQUEST_WINDOW_SECONDS)


def write_rate_limit() -> int:
    return _env_int("AGENT_WRITE_RATE_LIMIT", DEFAULT_WRITE_LIMIT)


def write_window_seconds() -> int:
    return _env_int("AGENT_WRITE_WINDOW_SECONDS", DEFAULT_WRITE_WINDOW_SECONDS)


def max_sse_streams() -> int:
    return _env_int("AGENT_MAX_SSE_STREAMS", DEFAULT_MAX_SSE_STREAMS)


@dataclass(frozen=True)
class TurnLimits:
    daily_limit: int
    burst_limit: int
    burst_window_seconds: int
    max_concurrent: int
    trial_limit: Optional[int] = None
    stale_run_seconds: int = 90

    @classmethod
    def from_env(cls, *, trial_limit: Optional[int] = None, stale_run_seconds: int = 90) -> "TurnLimits":
        return cls(
            daily_limit=daily_turn_limit(),
            burst_limit=burst_turn_limit(),
            burst_window_seconds=burst_window_seconds(),
            max_concurrent=max_concurrent_turns(),
            trial_limit=trial_limit,
            stale_run_seconds=stale_run_seconds,
        )


class AgentLimitError(Exception):
    """A rate, concurrency or allowance limit that maps directly to an HTTP response."""

    def __init__(self, *, code: str, message: str, status: int = 429, retry_after: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = max(1, int(retry_after)) if retry_after is not None else None

    def to_http_exception(self) -> HTTPException:
        detail: dict[str, object] = {"code": self.code, "message": self.message}
        headers: Optional[dict[str, str]] = None
        if self.retry_after is not None:
            detail["retry_after"] = self.retry_after
            headers = {"Retry-After": str(self.retry_after)}
        return HTTPException(status_code=self.status, detail=detail, headers=headers)


class TrialLimitReached(AgentLimitError):
    def __init__(self) -> None:
        super().__init__(
            status=402,
            code="AGENT_TRIAL_USED",
            message="You've used your free Agent messages. Agent is part of the Creator plan.",
        )


class DailyLimitReached(AgentLimitError):
    def __init__(self, retry_after: Optional[int] = None) -> None:
        super().__init__(
            status=429,
            code="AGENT_DAILY_LIMIT",
            message="You've reached today's agent limit. It resets within 24 hours.",
            retry_after=retry_after or 60,
        )


class BurstLimitReached(AgentLimitError):
    def __init__(self, retry_after: Optional[int] = None) -> None:
        super().__init__(
            status=429,
            code="AGENT_RATE_LIMITED",
            message="You're sending messages too quickly. Try again in a moment.",
            retry_after=retry_after or 10,
        )


class ConcurrentLimitReached(AgentLimitError):
    def __init__(self, retry_after: int = 5) -> None:
        super().__init__(
            status=429,
            code="AGENT_CONCURRENT_LIMIT",
            message="You already have replies running in other chats. Wait for one to finish before sending another.",
            retry_after=retry_after,
        )


class RequestRateLimited(AgentLimitError):
    def __init__(self, retry_after: Optional[int] = None) -> None:
        super().__init__(
            status=429,
            code="AGENT_RATE_LIMITED",
            message="Too many agent requests. Try again in a moment.",
            retry_after=retry_after or 10,
        )


class TooManyStreamsError(AgentLimitError):
    def __init__(self, retry_after: int = 10) -> None:
        super().__init__(
            status=429,
            code="AGENT_TOO_MANY_STREAMS",
            message="Too many live chat connections open. Close another tab and try again.",
            retry_after=retry_after,
        )


async def _get_redis(queue_adapter: Any = None) -> Any:
    adapter = queue_adapter or JobQueue
    return await asyncio.wait_for(adapter.get_pool(), timeout=REDIS_OP_TIMEOUT_SECONDS)


async def hit_endpoint_limit(
    bucket: str,
    user_id: str,
    *,
    limit: int,
    window_seconds: int,
    queue_adapter: Any = None,
) -> tuple[bool, int]:
    """Increment one fixed-window bucket in Redis. Returns ``(allowed, retry_after)``.

    Fails open when Redis is unreachable so a transient Redis blip never breaks the
    UI; durable turn limits are still enforced in PostgreSQL.
    """
    now = int(time.time())
    window_start = now - (now % window_seconds)
    retry_after = max(1, window_start + window_seconds - now)
    key = _ENDPOINT_KEY.format(bucket=bucket, user_id=user_id, window_start=window_start)
    try:
        redis = await _get_redis(queue_adapter)
        if hasattr(redis, "eval"):
            count = int(
                await asyncio.wait_for(
                    redis.eval(_WINDOW_LUA, 1, key, window_seconds), timeout=REDIS_OP_TIMEOUT_SECONDS
                )
            )
        elif hasattr(redis, "incr"):
            count = int(await asyncio.wait_for(redis.incr(key), timeout=REDIS_OP_TIMEOUT_SECONDS))
            if count == 1 and hasattr(redis, "expire"):
                await asyncio.wait_for(redis.expire(key, window_seconds), timeout=REDIS_OP_TIMEOUT_SECONDS)
        else:
            return True, 0
    except Exception:  # noqa: BLE001
        logger.warning("Agent endpoint rate limit check failed for bucket %s; allowing request", bucket, exc_info=True)
        return True, 0
    return (count <= limit), (0 if count <= limit else retry_after)


async def enforce_endpoint_limit(user_id: str, *, write: bool = False, queue_adapter: Any = None) -> None:
    """Enforce the per-user HTTP request rate limit for ``/agent/*`` endpoints."""
    bucket = "write" if write else "read"
    limit = write_rate_limit() if write else request_rate_limit()
    window = write_window_seconds() if write else request_window_seconds()
    allowed, retry_after = await hit_endpoint_limit(
        bucket, user_id, limit=limit, window_seconds=window, queue_adapter=queue_adapter
    )
    if not allowed:
        raise RequestRateLimited(retry_after=retry_after)


def _prune_local_sse(user_id: str, now: float) -> dict[str, float]:
    slots = _local_sse_slots.get(user_id)
    if not slots:
        return {}
    live = {slot_id: exp for slot_id, exp in slots.items() if exp > now}
    if live:
        _local_sse_slots[user_id] = live
    else:
        _local_sse_slots.pop(user_id, None)
    return live


async def acquire_sse_slot(user_id: str, *, queue_adapter: Any = None) -> str:
    """Reserve one concurrent SSE stream slot for ``user_id``, or raise ``TooManyStreamsError``."""
    slot_id = uuid.uuid4().hex
    limit = max_sse_streams()
    now = time.time()
    key = _SSE_KEY.format(user_id=user_id)
    try:
        redis = await _get_redis(queue_adapter)
        if hasattr(redis, "eval"):
            acquired = int(
                await asyncio.wait_for(
                    redis.eval(_SSE_ACQUIRE_LUA, 1, key, now, SSE_SLOT_TTL_SECONDS, limit, slot_id),
                    timeout=REDIS_OP_TIMEOUT_SECONDS,
                )
            )
            if not acquired:
                raise TooManyStreamsError()
            return slot_id
    except TooManyStreamsError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("Agent SSE slot check failed in Redis; falling back to local tracker", exc_info=True)

    live = _prune_local_sse(user_id, now)
    if len(live) >= limit:
        raise TooManyStreamsError()
    live[slot_id] = now + SSE_SLOT_TTL_SECONDS
    _local_sse_slots[user_id] = live
    return slot_id


async def refresh_sse_slot(user_id: str, slot_id: str, *, queue_adapter: Any = None) -> None:
    """Extend an active SSE stream slot's expiry while the connection stays open."""
    now = time.time()
    key = _SSE_KEY.format(user_id=user_id)
    try:
        redis = await _get_redis(queue_adapter)
        if hasattr(redis, "zadd"):
            await asyncio.wait_for(
                redis.zadd(key, {slot_id: now + SSE_SLOT_TTL_SECONDS}), timeout=REDIS_OP_TIMEOUT_SECONDS
            )
            if hasattr(redis, "expire"):
                await asyncio.wait_for(
                    redis.expire(key, SSE_SLOT_TTL_SECONDS + 30), timeout=REDIS_OP_TIMEOUT_SECONDS
                )
            return
    except Exception:  # noqa: BLE001
        logger.debug("Could not refresh SSE slot in Redis", exc_info=True)

    slots = _local_sse_slots.get(user_id)
    if slots is not None and slot_id in slots:
        slots[slot_id] = now + SSE_SLOT_TTL_SECONDS


async def release_sse_slot(user_id: str, slot_id: str, *, queue_adapter: Any = None) -> None:
    """Release an SSE stream slot when the client disconnects.

    Shielded from anyio task-group cancellation so sse_starlette tearing down
    the generator on disconnect still frees the slot immediately.
    """
    with anyio.CancelScope(shield=True):
        key = _SSE_KEY.format(user_id=user_id)
        try:
            redis = await _get_redis(queue_adapter)
            if hasattr(redis, "zrem"):
                await asyncio.wait_for(redis.zrem(key, slot_id), timeout=REDIS_OP_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001
            logger.debug("Could not release SSE slot in Redis", exc_info=True)

        slots = _local_sse_slots.get(user_id)
        if slots is not None:
            slots.pop(slot_id, None)
            if not slots:
                _local_sse_slots.pop(user_id, None)


def reset_local_state() -> None:
    """Clear process-local fallback state (for tests)."""
    _local_sse_slots.clear()
