"""Generic exponential backoff retry helper.

Generalizes (rather than duplicating) the ad hoc retry pattern in
`youtube_utils.py` (`for attempt in range(max_retries): ... time.sleep(2**attempt)`),
for use with Gemini 429s and any other future retryable API call — async-
native rather than a blocking `time.sleep`.
"""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_exponential_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 5,
    base_delay: float = 1.0,
    retryable: Callable[[Exception], bool] = lambda exc: True,
) -> T:
    """Call `fn()`, retrying with exponential backoff (base_delay * 2**attempt)
    on any exception for which `retryable(exc)` is True. Re-raises the last
    exception once `max_retries` is exhausted, or immediately if `retryable`
    returns False."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if not retryable(exc) or attempt == max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.info(
                "Retryable call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1,
                max_retries,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    # Unreachable (loop always returns or raises), but keeps type checkers happy.
    assert last_exc is not None
    raise last_exc


def is_rate_limit_error(exc: Exception) -> bool:
    """Best-effort check for a 429/rate-limit style error across HTTP clients
    that don't share a common exception type (httpx, google-genai, etc.)."""
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code == 429:
        return True
    return "429" in str(exc) or "rate limit" in str(exc).lower() or "resource_exhausted" in str(exc).lower()
