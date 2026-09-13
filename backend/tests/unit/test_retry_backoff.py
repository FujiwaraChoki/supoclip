import pytest

from src.retry_backoff import is_rate_limit_error, with_exponential_backoff


async def test_succeeds_on_first_try_without_retry():
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    result = await with_exponential_backoff(fn, base_delay=0.001)
    assert result == "ok"
    assert len(calls) == 1


async def test_retries_until_success():
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return "ok"

    result = await with_exponential_backoff(fn, max_retries=5, base_delay=0.001)
    assert result == "ok"
    assert len(calls) == 3


async def test_raises_after_max_retries_exhausted():
    async def fn():
        raise RuntimeError("permanent failure")

    with pytest.raises(RuntimeError, match="permanent failure"):
        await with_exponential_backoff(fn, max_retries=3, base_delay=0.001)


async def test_does_not_retry_when_not_retryable():
    calls = []

    async def fn():
        calls.append(1)
        raise ValueError("non-retryable")

    with pytest.raises(ValueError):
        await with_exponential_backoff(
            fn, max_retries=5, base_delay=0.001, retryable=lambda exc: False
        )
    assert len(calls) == 1


def test_is_rate_limit_error_detects_status_code_429():
    class FakeError(Exception):
        status_code = 429

    assert is_rate_limit_error(FakeError("boom")) is True


def test_is_rate_limit_error_detects_message_text():
    assert is_rate_limit_error(Exception("429 Too Many Requests")) is True
    assert is_rate_limit_error(Exception("RESOURCE_EXHAUSTED")) is True


def test_is_rate_limit_error_false_for_unrelated_error():
    assert is_rate_limit_error(Exception("connection refused")) is False
