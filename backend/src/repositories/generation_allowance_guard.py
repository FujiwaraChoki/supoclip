"""Serialize paid generation admissions across API processes and task retries."""

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .session_lock_connection import session_lock_connection


class GenerationAllowanceBusy(Exception):
    pass


@asynccontextmanager
async def generation_allowance_guard(db, user_id: str, *, enabled: bool):
    """Keep ownership across commits; wait briefly without using the request pool."""
    if not enabled:
        yield
        return

    # Admission starts after read-only authentication/source queries. Release
    # their connection before waiting for another admission to finish.
    await db.close()
    async with session_lock_connection(db) as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        try:
            await connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:user_id, 913462))"),
                {"user_id": user_id},
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise GenerationAllowanceBusy(
                    "Another generation request is in progress. Please retry shortly."
                ) from exc
            raise
        await connection.commit()
        yield
        # The dedicated physical connection closes at context exit, releasing
        # ownership without making a successful submission depend on an unlock.
