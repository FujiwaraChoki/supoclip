"""Hard limits on actual stream response bytes, including HLS retries."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
import threading
import time


class TransferStopped(RuntimeError):
    pass


class TransferBudget:
    def __init__(self, limit: int):
        if limit <= 0:
            raise ValueError("Stream downloads require a positive byte limit")
        self.limit = limit
        self.total = 0
        self.failure = None
        self.lock = threading.Lock()

    def check(self):
        if self.failure:
            raise TransferStopped(self.failure)

    def add(self, count):
        with self.lock:
            self.total += count
            if self.total > self.limit:
                self.failure = f"Stream download exceeded the {self.limit} byte job limit"
            self.check()


_active = ContextVar("stream_transfer", default=None)
_cancel = ContextVar("stream_transfer_cancel", default=None)


class Transfer:
    """Stop a transfer that stops making progress, or that runs past a hard cap.

    ``timeout`` is how long the transfer may go without receiving a byte; every
    byte read pushes it back. ``max_seconds`` (optional) caps the whole transfer.
    A slow but steady download of a long VOD is not a hung one: the byte budget,
    not the clock, bounds what it costs.
    """

    def __init__(self, budget, timeout, max_seconds=None):
        now = time.monotonic()
        self.budget = budget
        self.idle_seconds = timeout
        self.progressed = now
        self.hard_deadline = now + max_seconds if max_seconds else None
        self.cancelled = _cancel.get()

    @property
    def deadline(self):
        idle = self.progressed + self.idle_seconds
        return idle if self.hard_deadline is None else min(idle, self.hard_deadline)

    def progress(self):
        self.progressed = time.monotonic()

    def check(self):
        self.budget.check()
        if self.cancelled is not None and self.cancelled.is_set():
            self.budget.failure = "Stream download cancelled"
        elif time.monotonic() >= self.deadline:
            self.budget.failure = "Stream download timed out"
        self.budget.check()


@contextmanager
def transfer_scope(budget, timeout=300, max_seconds=None):
    transfer = Transfer(budget, max(1, timeout), max(1, max_seconds) if max_seconds else None)
    token = _active.set(transfer)
    try:
        transfer.check()
        yield transfer
        transfer.check()
    finally:
        _active.reset(token)


def account_chunk(data):
    transfer = _active.get()
    if transfer is not None:
        transfer.check()
        transfer.budget.add(len(data))
        if data:
            transfer.progress()


def check_transfer():
    transfer = _active.get()
    if transfer is not None:
        transfer.check()


class LimitedResponse:
    def __init__(self, response, transfer):
        self.response, self.transfer = response, transfer

    def __getattr__(self, key):
        return getattr(self.response, key)

    def read(self, size=-1):
        self.transfer.check()
        remaining = self.transfer.budget.limit - self.transfer.budget.total
        # At most one extra byte is read to detect exhaustion. Bound unbounded
        # reads too (manifests/extractor responses), not just media fragments.
        size = min(size, remaining + 1) if size is not None and size >= 0 else remaining + 1
        try:
            data = self.response.read(size)
            self.transfer.budget.add(len(data))
            if data:
                self.transfer.progress()
            self.transfer.check()
            return data
        except BaseException:
            self.response.close()
            raise

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    read1 = read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.response.close()


@contextmanager
def bounded_client(options):
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request

    transfer = _active.get()
    with YoutubeDL(options) as client:
        if transfer is not None:
            original = client.urlopen

            def open_response(request):
                transfer.check()
                if isinstance(request, str):
                    request = Request(request)
                request.extensions["timeout"] = min(10, max(.1, transfer.deadline - time.monotonic()))
                return LimitedResponse(original(request), transfer)

            client.urlopen = open_response
        yield client
        if transfer is not None:
            transfer.check()


async def run_stream_download(func, *args, should_cancel=None, **kwargs):
    """Signal the sync HTTP reader before waiting for its thread to finish."""
    event = threading.Event()
    token = _cancel.set(event)
    operation = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        while not operation.done():
            await asyncio.wait({operation}, timeout=.5)
            if should_cancel is not None and await should_cancel():
                event.set()
        return await operation
    except BaseException:
        event.set()
        # A thread cannot be killed; bounded socket reads let it exit safely.
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
    finally:
        _cancel.reset(token)
