"""Carry download recovery notices from blocking providers to task progress."""

import asyncio
from contextvars import ContextVar
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, TypeVar

from .utils.async_helpers import run_in_thread


class DownloadRecovery(str, Enum):
    CONNECTION = "Retrying video download with another connection..."
    AUDIO = "Recovering video audio..."
    FULL_VIDEO = "Recovering the full video download..."
    BACKUP = "Using a backup video download option..."
    ANOTHER_BACKUP = "Trying another video download recovery option..."


_listener: ContextVar[Optional[Callable[[DownloadRecovery], None]]] = ContextVar(
    "download_recovery_listener", default=None,
)
T = TypeVar("T")
ProgressCallback = Callable[[int, str, str], Awaitable[None]]


def report_download_recovery(recovery: DownloadRecovery) -> None:
    listener = _listener.get()
    if listener is not None:
        listener(recovery)


async def run_download_in_thread(
    func: Callable[..., T], *args: Any,
    progress_callback: Optional[ProgressCallback] = None,
    progress: int = 10, **kwargs: Any,
) -> T:
    """Persist notices on the event loop while the provider runs in a thread.

    The listener belongs only to this operation. Callbacks finish before the
    next pipeline stage, and never use an async database session in a thread.
    """
    if progress_callback is None:
        return await run_in_thread(func, *args, **kwargs)

    loop = asyncio.get_running_loop()
    updates: asyncio.Queue[Optional[DownloadRecovery]] = asyncio.Queue()
    closed = False

    def send(recovery: DownloadRecovery) -> None:
        if not closed:
            loop.call_soon_threadsafe(updates.put_nowait, recovery)

    def execute() -> T:
        token = _listener.set(send)
        try:
            return func(*args, **kwargs)
        finally:
            _listener.reset(token)

    operation = asyncio.create_task(run_in_thread(execute))
    # Thread notices are queued before the operation's completion callback.
    operation.add_done_callback(lambda _: updates.put_nowait(None))
    previous = None
    try:
        while (recovery := await updates.get()) is not None:
            if recovery != previous:
                await progress_callback(progress, recovery.value, "processing")
                previous = recovery
        return await operation
    finally:
        closed = True
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
