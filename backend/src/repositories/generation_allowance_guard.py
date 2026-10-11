"""Serialize paid generation admissions across API processes and task retries."""

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


class GenerationAllowanceBusy(Exception):
    pass


@asynccontextmanager
async def generation_allowance_guard(db, user_id: str, *, enabled: bool):
    """Keep ownership across repository commits; briefly wait for other admissions."""
    if not enabled:
        yield
        return

    # Admission starts after read-only authentication/source queries. Release
    # their connection before taking a separate session lock.
    await db.close()
    connection = await db.bind.connect()
    try:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        try:
            await connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:user_id, 913462))"),
                {"user_id": user_id},
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                # A timeout never acquired the lock. Restore the transaction
                # before returning this connection to the pool.
                await connection.rollback()
                raise GenerationAllowanceBusy(
                    "Another generation request is in progress. Please retry shortly."
                ) from exc
            raise
        await connection.commit()
        yield
        await connection.scalar(
            text("SELECT pg_advisory_unlock(hashtextextended(:user_id, 913462))"),
            {"user_id": user_id},
        )
        await connection.commit()
    except GenerationAllowanceBusy:
        raise
    except BaseException:
        # Close the physical connection if explicit unlock cannot be confirmed,
        # including cancellation during acquisition.
        await connection.invalidate()
        raise
    finally:
        await connection.close()
