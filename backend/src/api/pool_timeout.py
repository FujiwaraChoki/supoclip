"""Answer 503 + Retry-After when a request can't get a database connection in time."""

import logging

from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy.exc import TimeoutError as PoolTimeout

logger = logging.getLogger(__name__)

POOL_BUSY_RETRY_AFTER_SECONDS = 5


def pool_busy(request) -> HTTPException:
    """The 503 for a pool checkout timeout. Call it inside the ``except`` block."""
    logger.error(
        "No database connection free for %s %s",
        request.method,
        request.url.path,
        exc_info=True,
    )
    return HTTPException(
        503,
        "The server is busy. Try again in a few seconds.",
        headers={"Retry-After": str(POOL_BUSY_RETRY_AFTER_SECONDS)},
    )


class PoolTimeoutRoute(APIRoute):
    """A full connection pool is a moment of overload, not a broken request.

    Only SQLAlchemy's pool checkout timeout is mapped. Every other error keeps
    its own status. It is still logged with its traceback, so a starved pool
    stays visible in error tracking.
    """

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def route_handler(request):
            try:
                return await handler(request)
            except PoolTimeout:
                raise pool_busy(request) from None

        return route_handler
