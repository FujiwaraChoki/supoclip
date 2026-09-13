import time

import pytest

from src.workers.resource_locks import acquire_slot, release_slot, resource_slot


class _FakeSortedSetRedis:
    """Minimal in-memory stand-in for the sorted-set ops resource_locks.py uses."""

    def __init__(self):
        self._zsets: dict[str, dict[str, float]] = {}

    async def zremrangebyscore(self, key, min_score, max_score):
        zset = self._zsets.get(key, {})
        lo = float("-inf") if min_score == "-inf" else min_score
        hi = float("inf") if max_score == "+inf" else max_score
        for member, score in list(zset.items()):
            if lo <= score <= hi:
                del zset[member]

    async def zadd(self, key, mapping):
        self._zsets.setdefault(key, {}).update(mapping)

    async def zcard(self, key):
        return len(self._zsets.get(key, {}))

    async def zrem(self, key, member):
        self._zsets.get(key, {}).pop(member, None)


async def test_acquire_slot_succeeds_under_limit():
    redis = _FakeSortedSetRedis()
    holder = await acquire_slot("llm", 1, redis=redis)
    assert holder is not None


async def test_acquire_slot_fails_when_full():
    redis = _FakeSortedSetRedis()
    first = await acquire_slot("llm", 1, redis=redis)
    assert first is not None

    second = await acquire_slot("llm", 1, redis=redis)
    assert second is None


async def test_release_slot_frees_capacity():
    redis = _FakeSortedSetRedis()
    holder = await acquire_slot("gpu", 1, redis=redis)
    assert holder is not None

    await release_slot("gpu", holder, redis=redis)

    second = await acquire_slot("gpu", 1, redis=redis)
    assert second is not None


async def test_expired_holder_is_evicted():
    redis = _FakeSortedSetRedis()
    stale_holder = await acquire_slot("gpu", 1, redis=redis, ttl_seconds=-1)
    assert stale_holder is not None

    fresh_holder = await acquire_slot("gpu", 1, redis=redis)
    assert fresh_holder is not None


async def test_resource_slot_context_manager_releases_on_exit():
    redis = _FakeSortedSetRedis()
    async with resource_slot("render", 1, redis=redis):
        held = await acquire_slot("render", 1, redis=redis)
        assert held is None  # still full while inside the block

    freed = await acquire_slot("render", 1, redis=redis)
    assert freed is not None


async def test_resource_slot_times_out_when_never_freed():
    redis = _FakeSortedSetRedis()
    await acquire_slot("render", 1, redis=redis, holder_id="permanent-holder")

    with pytest.raises(TimeoutError):
        async with resource_slot("render", 1, redis=redis, poll_interval_seconds=0.01, timeout_seconds=0.05):
            pass
