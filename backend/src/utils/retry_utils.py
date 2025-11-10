"""
Retry utilities with exponential backoff for resilient operations.
"""
import asyncio
import logging
from functools import wraps
from typing import TypeVar, Callable, Any, Type

logger = logging.getLogger(__name__)

T = TypeVar('T')


def async_retry(
    max_attempts: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,)
):
    """
    Retry decorator with exponential backoff for async functions.

    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each attempt
        exceptions: Tuple of exception types to catch and retry

    Example:
        @async_retry(max_attempts=3, delay=2, backoff=2)
        async def fetch_data():
            # API call that might fail
            pass
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts. "
                            f"Last error: {e}",
                            exc_info=True
                        )
                        raise

                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {current_delay}s..."
                    )

                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

            # This should never be reached, but just in case
            raise last_exception or Exception("Unexpected error in retry logic")

        return wrapper
    return decorator


def sync_retry(
    max_attempts: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,)
):
    """
    Retry decorator with exponential backoff for synchronous functions.

    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each attempt
        exceptions: Tuple of exception types to catch and retry

    Example:
        @sync_retry(max_attempts=3, delay=2, backoff=2)
        def download_file(url):
            # Download that might fail
            pass
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time
            current_delay = delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts. "
                            f"Last error: {e}",
                            exc_info=True
                        )
                        raise

                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {current_delay}s..."
                    )

                    time.sleep(current_delay)
                    current_delay *= backoff

            # This should never be reached, but just in case
            raise last_exception or Exception("Unexpected error in retry logic")

        return wrapper
    return decorator


class RetryableError(Exception):
    """Exception that indicates an operation should be retried."""
    pass


class NonRetryableError(Exception):
    """Exception that indicates an operation should NOT be retried."""
    pass


async def retry_with_exponential_backoff(
    func: Callable[..., T],
    *args,
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    **kwargs
) -> T:
    """
    Utility function to retry an async operation with exponential backoff.

    Args:
        func: Async function to retry
        *args: Positional arguments for func
        max_attempts: Maximum number of attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        backoff_factor: Factor to multiply delay by after each attempt
        **kwargs: Keyword arguments for func

    Returns:
        Result from successful function call

    Raises:
        Last exception if all retries fail
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)

        except NonRetryableError:
            # Don't retry these
            raise

        except Exception as e:
            last_exception = e

            if attempt == max_attempts:
                logger.error(
                    f"Operation failed after {max_attempts} attempts. Last error: {e}",
                    exc_info=True
                )
                raise

            logger.warning(
                f"Attempt {attempt}/{max_attempts} failed: {e}. "
                f"Retrying in {delay:.1f}s..."
            )

            await asyncio.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)

    raise last_exception or Exception("Unexpected error in retry logic")
