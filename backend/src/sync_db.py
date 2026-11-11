"""PostgreSQL for synchronous code on any thread.

The storage client runs in worker threads next to blocking boto calls, and
sometimes on a thread whose event loop is already running. asyncpg connections
belong to the loop that created them and the application's engine belongs to
the main loop, so neither can be borrowed here. This module owns one daemon
thread with a private loop instead: a caller hands it a query and blocks for
the answer, exactly like the S3 request beside it.

Connection settings come from the configured engine, never from DATABASE_URL,
so a test that points the engine at a disposable database is honoured.
"""
from __future__ import annotations

import asyncio
import atexit
from contextlib import contextmanager
import os
import threading

import asyncpg

# Callers are single-row statements on the path of a signed URL: keep a
# database outage from holding a request for long.
CONNECT_TIMEOUT = 5
QUERY_TIMEOUT = 15
# A dropped or unresponsive connection, as opposed to a statement the server rejected.
DISCONNECTED = (
    asyncpg.PostgresConnectionError, asyncpg.InterfaceError, asyncpg.AdminShutdownError,
    asyncpg.CrashShutdownError, asyncpg.CannotConnectNowError, OSError,
)


class LockBusy(BlockingIOError):
    """Another session holds the advisory lock."""


class _Runtime:
    """The private loop and the connections bound to it."""

    def __init__(self):
        self.pid = os.getpid()
        self.loop = asyncio.new_event_loop()
        self.pool = None
        self.identity = None
        self.guard = asyncio.Lock()
        self.thread = threading.Thread(target=self._serve, name="sync-db", daemon=True)
        self.thread.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _shared(self, identity, params):
        async with self.guard:
            if self.pool is not None and self.identity != identity:
                # Tests reconfigure the engine; never answer from the old database.
                pool, self.pool = self.pool, None
                await self._dispose(pool)
            if self.pool is None:
                # Two connections: one slow statement must not queue every signed URL.
                # Nothing connects until the first query, and idle connections are
                # dropped before a proxy can silently discard them.
                self.pool = await asyncpg.create_pool(
                    min_size=0, max_size=2, max_inactive_connection_lifetime=60,
                    timeout=CONNECT_TIMEOUT, command_timeout=QUERY_TIMEOUT, **params)
                self.identity = identity
            return self.pool

    @staticmethod
    async def _dispose(pool):
        try:
            await asyncio.wait_for(pool.close(), 5)
        except Exception:
            pool.terminate()

    async def query(self, method, sql, args, identity, params):
        for retry in (True, False):
            pool = await self._shared(identity, params)
            try:
                return await getattr(pool, method)(sql, *args)
            except DISCONNECTED:
                # The pool replaces a broken connection on the next acquire. The
                # statement may have run already, so callers keep theirs idempotent.
                if not retry:
                    raise

    async def acquire(self, key, namespace, blocking, params):
        connection = await asyncpg.connect(timeout=CONNECT_TIMEOUT, **params)
        try:
            if blocking:
                # No statement timeout: the holder may be uploading a large video.
                await connection.execute("SELECT pg_advisory_lock(hashtextextended($1, $2))", key, namespace)
            elif not await connection.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, $2))",
                                               key, namespace, timeout=QUERY_TIMEOUT):
                raise LockBusy("Advisory lock is held by another session")
            return connection
        except BaseException:
            # Includes a cancelled wait: the session must not outlive its caller.
            connection.terminate()
            raise

    @staticmethod
    async def release(connection, key, namespace):
        try:
            # Unlocking first makes the release visible before this returns. Closing
            # alone frees the lock only once the server has torn the session down.
            await connection.execute("SELECT pg_advisory_unlock(hashtextextended($1, $2))",
                                     key, namespace, timeout=5)
        except Exception:
            pass
        try:
            await connection.close(timeout=5)
        except Exception:
            connection.terminate()

    async def close(self):
        async with self.guard:
            pool, self.pool, self.identity = self.pool, None, None
        if pool is not None:
            await self._dispose(pool)


_runtime: _Runtime | None = None
_start = threading.Lock()


def _call(method, *args):
    global _runtime
    with _start:
        # A forked child inherits the object but not its thread.
        if _runtime is None or _runtime.pid != os.getpid():
            _runtime = _Runtime()
        runtime = _runtime
    if threading.current_thread() is runtime.thread:
        raise RuntimeError("sync_db cannot be called from its own loop")
    future = asyncio.run_coroutine_threadsafe(getattr(runtime, method)(*args), runtime.loop)
    try:
        return future.result()
    except BaseException:
        # An interrupted caller must not leave a lock connection behind.
        future.cancel()
        raise


def _target():
    from .database import get_engine

    url = get_engine().url
    values = {"host": url.host, "port": url.port, "user": url.username,
              "password": url.password, "database": url.database}
    params = {name: value for name, value in values.items() if value is not None}
    if url.query.get("ssl"):
        params["ssl"] = url.query["ssl"]
    # The identity includes the password and stays in memory; it is never logged.
    return url.render_as_string(hide_password=False), params


def fetch(sql, *args) -> list[asyncpg.Record]:
    return _call("query", "fetch", sql, args, *_target())


def fetchrow(sql, *args) -> asyncpg.Record | None:
    return _call("query", "fetchrow", sql, args, *_target())


def fetchval(sql, *args):
    return _call("query", "fetchval", sql, args, *_target())


def execute(sql, *args) -> str:
    return _call("query", "execute", sql, args, *_target())


@contextmanager
def advisory_lock(key: str, *, blocking: bool = True, namespace: int = 113470):
    """Hold a session advisory lock on a connection of its own.

    A long upload under the lock therefore never blocks the short queries, and
    a process that dies releases the lock when its connection drops. The lock is
    not reentrant: taking the same key twice from one thread waits on itself.
    """
    connection = _call("acquire", key, namespace, blocking, _target()[1])
    try:
        yield
    finally:
        _call("release", connection, key, namespace)


def reset():
    """Close the cached connections. Safe when nothing was ever started."""
    with _start:
        runtime = _runtime
    if runtime is None or runtime.pid != os.getpid() or not runtime.thread.is_alive():
        return
    try:
        asyncio.run_coroutine_threadsafe(runtime.close(), runtime.loop).result(timeout=15)
    except Exception:
        pass


atexit.register(reset)
