"""Serialize paid generation admissions across API processes and task retries."""

from contextlib import asynccontextmanager

from sqlalchemy import text


class GenerationAllowanceBusy(Exception):
    pass


@asynccontextmanager
async def generation_allowance_guard(db, user_id: str, *, enabled: bool):
    """Keep ownership across repository commits without holding a transaction open."""
    if not enabled:
        yield
        return

    # Admission starts after read-only authentication/source queries. Release
    # their connection before taking a separate session lock.
    await db.close()
    connection = await db.bind.connect()
    try:
        locked = await connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:user_id, 913462))"),
            {"user_id": user_id},
        )
        await connection.commit()
        if not locked:
            raise GenerationAllowanceBusy(
                "Another generation request is in progress. Please retry shortly."
            )
        yield
        await connection.scalar(
            text("SELECT pg_advisory_unlock(hashtextextended(:user_id, 913462))"),
            {"user_id": user_id},
        )
        await connection.commit()
    except BaseException:
        # Close the physical connection if explicit unlock cannot be confirmed.
        await connection.invalidate()
        raise
    finally:
        await connection.close()
