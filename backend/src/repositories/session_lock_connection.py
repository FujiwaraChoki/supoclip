"""Dedicated physical connections for advisory locks, outside the request pool."""

from contextlib import asynccontextmanager
import logging

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def session_lock_connection(db):
    # Waiters must not consume connections the lock holder needs for its writes.
    engine = create_async_engine(db.bind.url, poolclass=NullPool)
    connection = None
    try:
        connection = await engine.connect()
        yield connection
    finally:
        # Physical closure releases every session advisory lock, even on
        # cancellation. Cleanup errors must not turn an already committed and
        # enqueued generation into a 500 that prompts duplicate client retries.
        if connection is not None:
            try:
                await connection.invalidate()
            except Exception:
                logger.exception("Could not invalidate advisory lock connection")
            try:
                await connection.close()
            except Exception:
                logger.exception("Could not close advisory lock connection")
        try:
            await engine.dispose()
        except Exception:
            logger.exception("Could not dispose advisory lock engine")
