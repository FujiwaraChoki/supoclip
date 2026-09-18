"""Cross-process ownership for worker runs and resume requests.

Session advisory locks survive ordinary transaction commits, have no expiring
lease, and disappear when the dedicated database connection closes.
"""

from contextlib import asynccontextmanager
from functools import wraps

from sqlalchemy import text


class TaskRunBusy(Exception):
    pass


@asynccontextmanager
async def task_run_guard(db, task_id: str):
    connection = await db.bind.connect()
    try:
        locked = await connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:task_id, 913461))"),
            {"task_id": task_id},
        )
        await connection.commit()
        if not locked:
            raise TaskRunBusy("The previous worker is still stopping. Retry resume in a moment.")
        yield
    finally:
        # Never return a session-level lock to the pool. Invalidation closes the
        # physical connection and also releases ownership on error/cancellation.
        await connection.invalidate()
        await connection.close()


def exclusive_task_run(method):
    @wraps(method)
    async def wrapped(self, task_id, *args, **kwargs):
        async with self._task_run_guard(task_id):
            current = await self.task_repo.get_task_by_id(self.db, task_id)
            if not current:
                raise ValueError("Task not found")
            if current["status"] in {"completed", "cancelled"}:
                return {"task_id": task_id, "status": current["status"], "skipped": True}
            return await method(self, task_id, *args, **kwargs)
    return wrapped
