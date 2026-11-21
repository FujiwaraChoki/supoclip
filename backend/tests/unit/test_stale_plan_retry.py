"""A read whose cached plan went stale after a schema change is retried once."""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import NotSupportedError, ProgrammingError

from src.repositories.source_repository import SourceRepository
from src.repositories.stale_plan import is_stale_cached_plan
from src.repositories.task_repository import TaskRepository


def stale_plan_error() -> NotSupportedError:
    orig = AsyncAdapt_asyncpg_dbapi.InvalidCachedStatementError(
        "cached statement plan is invalid due to a database schema or configuration change"
    )
    return NotSupportedError("SELECT t.* FROM tasks t", {}, orig)


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Savepoint:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, *_rest):
        self.db.savepoints.append("rollback" if exc_type else "release")
        return False


class _FlakyDb:
    """Fails its first ``failures`` statements with ``error``, then returns ``row``."""

    def __init__(self, row, error, failures=1):
        self.row, self.error, self.failures = row, error, failures
        self.calls = 0
        self.savepoints: list[str] = []
        self.info: dict = {}

    def begin_nested(self):
        return _Savepoint(self)

    async def execute(self, statement, params=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return _Result(self.row)


def task_row():
    return SimpleNamespace(
        id="task-1", user_id="user-1", source_id="source-1", source_title="Title", source_type="youtube",
        source_url="https://youtu.be/x", status="completed", progress=100, progress_message="Done",
        generated_clips_ids=[], font_family="THEBOLDFONT", font_size=32, font_color="#FFFFFF",
        caption_template="default", include_broll=False, processing_mode="fast", cache_hit=False,
        error_code=None, stage_timings_json=None, started_at=None, completed_at=None,
        completion_notification_sent_at=None, source_duration_seconds=600, share_enabled=False,
        workflow=None, workflow_options=None, workflow_plan=None,
        created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
    )


def test_recognises_only_the_stale_plan_error():
    assert is_stale_cached_plan(stale_plan_error())
    assert not is_stale_cached_plan(ProgrammingError("SELECT 1", {}, Exception("syntax error")))


async def test_get_task_by_id_retries_a_stale_cached_plan_once():
    db = _FlakyDb(task_row(), stale_plan_error())

    task = await TaskRepository.get_task_by_id(db, "task-1")

    assert task["id"] == "task-1"
    assert db.calls == 2
    # The failed attempt is rolled back to its savepoint, so the caller's transaction stays usable.
    assert db.savepoints == ["rollback", "release"]


async def test_get_source_by_id_retries_a_stale_cached_plan_once():
    row = SimpleNamespace(id="source-1", type="youtube", title="Title", url="https://youtu.be/x",
                          created_at=None, updated_at=None)
    db = _FlakyDb(row, stale_plan_error())

    source = await SourceRepository.get_source_by_id(db, "source-1")

    assert source["id"] == "source-1"
    assert db.calls == 2


async def test_a_plan_still_stale_on_retry_is_raised():
    db = _FlakyDb(task_row(), stale_plan_error(), failures=2)

    with pytest.raises(NotSupportedError):
        await TaskRepository.get_task_by_id(db, "task-1")
    assert db.calls == 2


async def test_other_errors_are_not_retried():
    db = _FlakyDb(task_row(), ProgrammingError("SELECT 1", {}, Exception("syntax error")))

    with pytest.raises(ProgrammingError):
        await TaskRepository.get_task_by_id(db, "task-1")
    assert db.calls == 1
