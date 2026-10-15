"""
Helper functions for properly running synchronous operations in async context.
"""
import asyncio
from functools import wraps
from typing import Callable, TypeVar, Any
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')

async def run_in_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Run a synchronous function in a thread pool to avoid blocking the event loop.

    This should be used for any I/O-bound or CPU-bound sync operations like:
    - Video processing
    - File operations
    - YouTube downloads
    - Transcript generation
    """
    from ..media_storage import _workspace

    try:
        if _workspace.get() is None:
            return await asyncio.to_thread(func, *args, **kwargs)
        operation = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Python cannot stop a running thread. Keep its media leases until
            # it has stopped reading/writing, then propagate cancellation.
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if not operation.cancelled():
                operation.exception()
            raise
    except Exception as e:
        logger.error(f"Error running {func.__name__} in thread: {e}")
        raise


def async_wrap(func: Callable[..., T]) -> Callable[..., Any]:
    """
    Decorator to automatically wrap synchronous functions to run in thread pool.

    Usage:
        @async_wrap
        def my_sync_function(arg1, arg2):
            # ... blocking operations ...
            return result

        # Can now be awaited:
        result = await my_sync_function(arg1, arg2)
    """
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        return await run_in_thread(func, *args, **kwargs)

    return wrapper
