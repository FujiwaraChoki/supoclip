"""Cross-process ownership for worker runs and resume requests.

Session advisory locks survive ordinary transaction commits, have no expiring
lease, and disappear when the dedicated database connection closes.
"""

from contextlib import asynccontextmanager
from functools import wraps

from sqlalchemy import text

from .session_lock_connection import session_lock_connection


class TaskRunBusy(Exception):
    pass


@asynccontextmanager
async def task_run_guard(db, task_id: str):
    async with session_lock_connection(db) as connection:
        locked = await connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:task_id, 913461))"),
            {"task_id": task_id},
        )
        await connection.commit()
        if not locked:
            raise TaskRunBusy("The previous worker is still stopping. Retry resume in a moment.")
        yield



def exclusive_task_run(method):
    @wraps(method)
    async def wrapped(self, task_id, *args, **kwargs):
        async with self._task_run_guard(task_id):
            current = await self.task_repo.get_task_by_id(self.db, task_id)
            if not current:
                raise ValueError("Task not found")
            if current["status"] in {"completed", "cancelled", "error"}:
                return {"task_id": task_id, "status": current["status"], "skipped": True}
            return await method(self, task_id, *args, **kwargs)
    return wrapped
