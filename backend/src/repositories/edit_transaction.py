"""Transaction boundary shared by edits to a task's clips."""

from contextlib import asynccontextmanager
from functools import wraps

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_EDIT_TASK = "supoclip_edit_task"


async def commit_unless_editing(db: AsyncSession) -> None:
    """Repositories retain standalone commits, but never release an edit lock."""
    if not db.info.get(_EDIT_TASK):
        await db.commit()


class TaskCancelled(Exception):
    def __init__(self):
        super().__init__("Task cancelled")


@asynccontextmanager
async def task_edit_transaction(db: AsyncSession, task_id: str, *, processing: bool = False):
    """Read and mutate one task under a row lock until its edit commits."""
    active_task = db.info.get(_EDIT_TASK)
    if active_task:
        if active_task != task_id:
            raise ValueError("Cannot edit two tasks in the same transaction")
        yield
        return

    try:
        result = await db.execute(
            text("SELECT status FROM tasks WHERE id = :task_id FOR UPDATE"),
            {"task_id": task_id},
        )
        status = result.scalar_one_or_none()
        if status is None:
            raise ValueError("Task not found")
        if processing and status != "processing":
            raise TaskCancelled()
        if not processing and status in {"queued", "processing"}:
            raise ValueError("Wait for processing to finish before editing this task")
        db.info[_EDIT_TASK] = task_id
        yield
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    finally:
        db.info.pop(_EDIT_TASK, None)


def serialized_task_edit(method):
    """Wrap a service operation whose first argument is its owning task ID."""
    @wraps(method)
    async def wrapped(self, task_id, *args, **kwargs):
        async with task_edit_transaction(self.db, task_id):
            return await method(self, task_id, *args, **kwargs)
    return wrapped
