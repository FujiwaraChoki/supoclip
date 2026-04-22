"""Tests for ``src/utils/async_helpers.py``."""

import pytest

from src.utils.async_helpers import async_wrap, run_in_thread


class TestRunInThread:
    @pytest.mark.asyncio
    async def test_returns_function_result(self):
        result = await run_in_thread(lambda x, y: x + y, 2, 3)
        assert result == 5

    @pytest.mark.asyncio
    async def test_accepts_kwargs(self):
        def fn(a, *, b):
            return a * b

        result = await run_in_thread(fn, 3, b=4)
        assert result == 12

    @pytest.mark.asyncio
    async def test_propagates_exceptions(self):
        def fn():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await run_in_thread(fn)


class TestAsyncWrap:
    @pytest.mark.asyncio
    async def test_wraps_sync_function(self):
        @async_wrap
        def add(a, b):
            return a + b

        assert await add(1, 2) == 3

    @pytest.mark.asyncio
    async def test_preserves_name(self):
        @async_wrap
        def greet():
            return "hi"

        assert greet.__name__ == "greet"

    @pytest.mark.asyncio
    async def test_propagates_exceptions(self):
        @async_wrap
        def failing():
            raise RuntimeError("nope")

        with pytest.raises(RuntimeError, match="nope"):
            await failing()
