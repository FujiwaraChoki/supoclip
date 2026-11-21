"""Retry a ``SELECT *`` read once when its cached plan went stale.

asyncpg prepares each statement once per connection. When a deploy adds, drops or retypes a column
while older processes still serve, their cached plan for a ``SELECT t.*`` no longer matches the
table and PostgreSQL rejects it ("cached plan must not change result type"). SQLAlchemy's asyncpg
dialect then invalidates its statement caches but cannot retry, because the error aborts the
transaction. Running the read in a savepoint keeps the caller's transaction usable, so a second
attempt re-prepares the statement against the new table.
"""

import logging
from typing import Any, Mapping, Optional

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def is_stale_cached_plan(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    candidates = (orig, getattr(orig, "__cause__", None), exc.__cause__)
    return any(type(error).__name__ == "InvalidCachedStatementError" for error in candidates if error is not None)


async def execute_read(db: AsyncSession, statement: Any, params: Optional[Mapping[str, Any]] = None):
    """``db.execute`` in a savepoint, retried once if the connection's cached plan is stale."""
    for attempt in (1, 2):
        try:
            async with db.begin_nested():
                return await db.execute(statement, params)
        except DBAPIError as exc:
            if attempt == 2 or not is_stale_cached_plan(exc):
                raise
            logger.warning("Cached query plan went stale after a schema change; retrying once")
