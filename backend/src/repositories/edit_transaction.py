"""Transaction boundary shared by edits to a task's clips."""

from contextlib import asynccontextmanager
from functools import wraps

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from ..media_storage import media_work

_EDIT_TASK = "supoclip_edit_task"
_LONG_EDIT = "supoclip_long_edit"


def allow_long_edit(db: AsyncSession) -> None:
    """Let this session's edit transactions sit idle for as long as their render takes.

    Every pooled session has ``idle_in_transaction_session_timeout`` (database.py), and an edit holds its row
    lock with no statement while it downloads and renders. Only for a caller that enforces its own time budget
    (``fx_clip_job``): ``task_edit_transaction`` then turns the timeout off for that one transaction.
    """
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        info[_LONG_EDIT] = True


async def commit_unless_editing(db: AsyncSession) -> None:
    """Repositories retain standalone commits, but never release an edit lock."""
    if not db.info.get(_EDIT_TASK):
        await db.commit()


class TaskCancelled(Exception):
    def __init__(self):
        super().__init__("Task cancelled")


class TaskEditInProgress(Exception):
    def __init__(self):
        super().__init__("This task's clips are being edited. Try again in a moment.")


async def hold_off_task_edits(db: AsyncSession, task_id: str) -> None:
    """Keep clip edits out of this task until the caller's transaction ends.

    Rows that point at a clip (exports) take this before re-reading the clip.
    ``FOR KEY SHARE`` conflicts only with an edit's ``FOR UPDATE``, so progress
    updates and other readers are not blocked. ``NOWAIT``: a regeneration holds
    the edit lock across its render, and waiting on it would outlast the
    request, then find the clip gone.
    """
    try:
        await db.execute(
            text("SELECT 1 FROM tasks WHERE id = :task_id FOR KEY SHARE NOWAIT"),
            {"task_id": task_id},
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            await db.rollback()
            raise TaskEditInProgress() from exc
        raise


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
        if db.info.get(_LONG_EDIT) is True:
            # SET LOCAL lasts until this transaction commits or rolls back, so the connection goes back to
            # the pool with the engine's timeout whatever happens to the edit.
            await db.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))
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
    @media_work
    async def wrapped(self, task_id, *args, **kwargs):
        async with task_edit_transaction(self.db, task_id):
            return await method(self, task_id, *args, **kwargs)
    return wrapped
