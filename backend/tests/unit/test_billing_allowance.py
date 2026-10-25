"""Exercise production ledger SQL on SQLite, without async-driver dependencies."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text

from src.config import Config
from src.repositories.minute_usage_repository import record_minute_usage
from src.repositories.task_repository import TaskRepository
from src.services.billing_service import BillingLimitExceeded, BillingService
from src.services.video_service import VideoService

PERIOD_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 10, 1, tzinfo=timezone.utc)
CREATED_AT = PERIOD_START + timedelta(days=1)


class _SqlSession:
    def __init__(self, connection):
        self.connection = connection
        self.info = {}

    async def execute(self, statement, parameters=None):
        return self.connection.execute(statement, parameters or {})

    async def commit(self):
        self.connection.commit()

    async def rollback(self):
        self.connection.rollback()


@pytest.fixture
def allowance_db():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.connection.create_function("NOW", 0, lambda: CREATED_AT.isoformat(" "))
        connection.connection.create_function("current_timestamp", 0, lambda: CREATED_AT.isoformat(" "))
        connection.execute(text("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, user_id TEXT, source_id TEXT, status TEXT,
                created_at TIMESTAMP, updated_at TIMESTAMP,
                source_duration_seconds INTEGER, source_metadata JSON,
                font_family TEXT, font_size INTEGER, font_color TEXT,
                caption_template TEXT, include_broll BOOLEAN, processing_mode TEXT
            )
        """))
        connection.execute(text("CREATE TABLE sources (id TEXT PRIMARY KEY, url TEXT)"))
        connection.execute(text("CREATE TABLE generated_clips (id TEXT PRIMARY KEY, task_id TEXT)"))
        # Use the actual ledger DDL, adapting only PostgreSQL's bigint cast.
        migration = Path("src/migrations/sql/20261025_0002_minute_usage_ledger.sql").read_text()
        for statement in migration.split(";")[:2]:
            connection.execute(text(statement.replace("::bigint", "")))
        yield connection
    engine.dispose()


def _insert_task(connection, task_id="task-1", status="queued", user_id="user-1", created_at=CREATED_AT, duration=60):
    connection.execute(text("""
        INSERT INTO tasks (id, user_id, source_id, status, created_at, source_duration_seconds)
        VALUES (:id, :user_id, 'source-1', :status, :created_at, :duration)
    """), {"id": task_id, "user_id": user_id, "status": status, "created_at": created_at, "duration": duration})


async def _charge(connection, task_id="task-1", user_id="user-1", duration=60, created_at=CREATED_AT):
    await record_minute_usage(_SqlSession(connection), user_id, task_id, duration)
    connection.execute(text("UPDATE minute_usage_events SET created_at = :created_at WHERE task_id = :task_id"),
                       {"created_at": created_at, "task_id": task_id})


def _service(connection, monkeypatch, limit=30, status="active"):
    config = Config()
    config.monetization_enabled = True
    config.pro_plan_minute_limit = limit
    service = BillingService(_SqlSession(connection), config)
    monkeypatch.setattr(service, "_load_user_billing_row", AsyncMock(return_value={
        "plan": "pro", "subscription_status": status,
        "billing_period_start": PERIOD_START, "billing_period_end": PERIOD_END,
    }))
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("refunded", [False, True])
async def test_deleting_task_and_clips_keeps_usage_and_limit(allowance_db, monkeypatch, refunded):
    _insert_task(allowance_db, duration=61)
    await _charge(allowance_db, duration=61)
    service = _service(allowance_db, monkeypatch, limit=2)
    if refunded:
        await TaskRepository.update_task_status(service.db, "task-1", "error")
    before = await service.get_usage_summary("user-1")
    allowance_db.execute(text("DELETE FROM generated_clips"))
    await TaskRepository.delete_task(service.db, "task-1")
    assert allowance_db.execute(text("SELECT COUNT(*) FROM tasks")).scalar() == 0
    after = await service.get_usage_summary("user-1")
    assert before["usage_minutes"] == after["usage_minutes"] == (0 if refunded else 2)
    assert before["usage_count"] == after["usage_count"] == (0 if refunded else 1)
    event = allowance_db.execute(text("SELECT refunded_at FROM minute_usage_events")).one()
    assert (event.refunded_at is not None) == refunded
    if not refunded:
        with pytest.raises(BillingLimitExceeded):
            await service.assert_can_create_task("user-1", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "failed", "cancelled", "canceled", "completed"])
@pytest.mark.parametrize("clip_count", [0, 1, 3])
async def test_terminal_status_refunds_only_failed_tasks_without_clips(allowance_db, status, clip_count):
    _insert_task(allowance_db)
    await _charge(allowance_db)
    for index in range(clip_count):
        allowance_db.execute(text("INSERT INTO generated_clips VALUES (:id, 'task-1')"), {"id": f"clip-{index}"})
    service = BillingService(_SqlSession(allowance_db))
    assert await service._count_usage("user-1", PERIOD_START, PERIOD_END) == (1, 1)
    assert await TaskRepository.update_task_status(service.db, "task-1", status)
    refunded = status != "completed" and clip_count == 0
    assert await service._count_usage("user-1", PERIOD_START, PERIOD_END) == ((0, 0) if refunded else (1, 1))
    event = allowance_db.execute(text("SELECT refunded_at FROM minute_usage_events")).one()
    assert (event.refunded_at is not None) == refunded
    # Repeated failure notifications do not change the original refund time.
    await TaskRepository.update_task_status(service.db, "task-1", status)
    assert allowance_db.execute(text("SELECT refunded_at FROM minute_usage_events")).scalar() == event.refunded_at


@pytest.mark.asyncio
async def test_duplicate_charge_and_retry_at_full_limit_are_idempotent(allowance_db, monkeypatch):
    _insert_task(allowance_db, status="error")
    await _charge(allowance_db)
    service = _service(allowance_db, monkeypatch, limit=1)
    # Duplicate delivery cannot change the original duration or timestamp either.
    await record_minute_usage(service.db, "user-1", "task-1", 600)
    for _ in range(2):
        await service.assert_can_resume_task("user-1", "task-1")
    assert await service._count_usage("user-1", PERIOD_START, PERIOD_END) == (1, 1)
    assert allowance_db.execute(text("SELECT source_duration_seconds FROM minute_usage_events")).scalar() == 60


@pytest.mark.asyncio
async def test_usage_is_scoped_to_event_user_and_period(allowance_db):
    await _charge(allowance_db, "other", user_id="other-user")
    await _charge(allowance_db, "before", created_at=PERIOD_START - timedelta(seconds=1))
    await _charge(allowance_db, "after", created_at=PERIOD_END + timedelta(seconds=1))
    await _charge(allowance_db, "start", created_at=PERIOD_START, duration=61)
    await _charge(allowance_db, "end", created_at=PERIOD_END, duration=1800)
    assert await BillingService(_SqlSession(allowance_db))._count_usage("user-1", PERIOD_START, PERIOD_END) == (2, 32)


@pytest.mark.asyncio
async def test_limit_uses_ledger_even_when_tasks_claim_no_usage(allowance_db, monkeypatch):
    _insert_task(allowance_db, status="error", duration=None)
    await _charge(allowance_db, "deleted-task", duration=20 * 60)
    service = _service(allowance_db, monkeypatch)
    assert (await service.assert_can_create_task("user-1", 600))["remaining_minutes"] == 10
    with pytest.raises(BillingLimitExceeded) as exc:
        await service.assert_can_create_task("user-1", 601)
    assert exc.value.summary["required_minutes"] == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("url,source", [("upload://video.mp4", "upload"), ("https://youtu.be/abcdefghijk", "youtube")])
async def test_missing_duration_measured_before_legacy_resume_charge(allowance_db, monkeypatch, url, source):
    _insert_task(allowance_db, status="error", duration=None)
    allowance_db.execute(text("INSERT INTO sources VALUES ('source-1', :url)"), {"url": url})
    probe = MagicMock(return_value=60.2)
    metadata = AsyncMock(return_value={"duration": 60.2})
    monkeypatch.setattr(VideoService, "probe_upload_duration", probe)
    monkeypatch.setattr("src.services.video_service.async_get_youtube_video_info", metadata)
    service = _service(allowance_db, monkeypatch)
    await service.assert_can_resume_task("user-1", "task-1")
    assert allowance_db.execute(text("SELECT source_duration_seconds FROM tasks")).scalar() == 61
    assert allowance_db.execute(text("SELECT minutes FROM minute_usage_events")).scalar() == 2
    if source == "upload":
        probe.assert_called_once_with(url, "user-1")
        metadata.assert_not_awaited()
    else:
        metadata.assert_awaited_once_with(url)
        probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [None, 0, -1, float("nan"), float("inf"), True])
async def test_unmeasurable_duration_cannot_create_charge(allowance_db, monkeypatch, duration):
    _insert_task(allowance_db, status="error", duration=None)
    allowance_db.execute(text("INSERT INTO sources VALUES ('source-1', 'upload://video.mp4')"))
    monkeypatch.setattr(VideoService, "probe_upload_duration", MagicMock(return_value=duration))
    service = _service(allowance_db, monkeypatch)
    with pytest.raises(ValueError):
        await service.assert_can_resume_task("user-1", "task-1")
    assert allowance_db.execute(text("SELECT COUNT(*) FROM minute_usage_events")).scalar() == 0


@pytest.mark.asyncio
async def test_uncharged_resume_must_fit_whole_video(allowance_db, monkeypatch):
    _insert_task(allowance_db, status="error", duration=61)
    service = _service(allowance_db, monkeypatch, limit=1)
    with pytest.raises(BillingLimitExceeded):
        await service.assert_can_resume_task("user-1", "task-1")
    assert allowance_db.execute(text("SELECT COUNT(*) FROM minute_usage_events")).scalar() == 0


@pytest.mark.asyncio
async def test_inactive_plan_cannot_retry_an_already_charged_task(allowance_db, monkeypatch):
    _insert_task(allowance_db, status="error")
    await _charge(allowance_db)
    service = _service(allowance_db, monkeypatch, status="canceled")
    with pytest.raises(BillingLimitExceeded):
        await service.assert_can_resume_task("user-1", "task-1")


@pytest.mark.asyncio
async def test_task_and_charge_commit_atomically(allowance_db):
    session = _SqlSession(allowance_db)
    task_id = await TaskRepository.create_task(session, "user-1", "source-1", source_duration_seconds=61, charge_minutes=True)
    assert allowance_db.execute(text("SELECT minutes FROM minute_usage_events WHERE task_id = :id"), {"id": task_id}).scalar() == 2
    # Force a ledger write failure and ensure no uncharged task is accepted.
    allowance_db.execute(text("DROP TABLE minute_usage_events"))
    allowance_db.commit()
    with pytest.raises(Exception, match="minute_usage_events"):
        await TaskRepository.create_task(session, "user-1", "source-1", source_duration_seconds=60, charge_minutes=True)
    assert allowance_db.execute(text("SELECT COUNT(*) FROM tasks")).scalar() == 1


@pytest.mark.asyncio
async def test_task_insert_error_is_not_retried_without_duration():
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("schema missing required column")
    with pytest.raises(RuntimeError):
        await TaskRepository.create_task(session, "user-1", "source-1", source_duration_seconds=60, charge_minutes=True)
    session.execute.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_backfill_selection_rounding_timestamps_and_idempotency(allowance_db):
    # Execute the real INSERT with a supplied window. PostgreSQL window math is
    # also exercised against PGlite by the mirrored migration integration test.
    allowance_db.execute(text("CREATE TABLE windows (user_id TEXT, window_start TIMESTAMP, window_end TIMESTAMP)"))
    allowance_db.execute(text("INSERT INTO windows VALUES ('user-1', :start, :end)"), {"start": PERIOD_START, "end": PERIOD_END})
    _insert_task(allowance_db, "known", duration=61)
    _insert_task(allowance_db, "unknown", duration=None)
    _insert_task(allowance_db, "failed", status="error", duration=600)
    _insert_task(allowance_db, "partial", status="cancelled", duration=121)
    _insert_task(allowance_db, "old", created_at=PERIOD_START - timedelta(seconds=1))
    allowance_db.execute(text("INSERT INTO generated_clips VALUES ('clip-1', 'partial'), ('clip-2', 'partial')"))
    migration = Path("src/migrations/sql/20261025_0002_minute_usage_ledger.sql").read_text()
    insert = migration[migration.index("INSERT INTO minute_usage_events"):].replace("::bigint", "")
    allowance_db.execute(text(insert))
    allowance_db.execute(text(insert))
    rows = allowance_db.execute(text("SELECT task_id, minutes, created_at FROM minute_usage_events ORDER BY task_id")).all()
    assert [(row.task_id, row.minutes) for row in rows] == [("known", 2), ("partial", 3)]
    assert all(str(row.created_at) == str(CREATED_AT) for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("original_charge", [CREATED_AT, PERIOD_START - timedelta(days=1)])
async def test_refunded_retry_recharges_the_same_row_once(allowance_db, monkeypatch, original_charge):
    _insert_task(allowance_db)
    await _charge(allowance_db, duration=61, created_at=original_charge)
    service = _service(allowance_db, monkeypatch, limit=2)
    await TaskRepository.update_task_status(service.db, "task-1", "error")
    original = allowance_db.execute(text("SELECT id FROM minute_usage_events")).scalar()
    assert (await service.get_usage_summary("user-1"))["usage_minutes"] == 0
    await service.assert_can_resume_task("user-1", "task-1")
    await TaskRepository.update_task_status(service.db, "task-1", "queued")
    await service.assert_can_resume_task("user-1", "task-1")
    await service.assert_can_resume_task("user-1", "task-1", require_active_plan=False)
    assert await service._count_usage("user-1", PERIOD_START, PERIOD_END) == (1, 2)
    event = allowance_db.execute(text("SELECT id, refunded_at, created_at FROM minute_usage_events")).one()
    assert event.id == original
    assert event.refunded_at is None
    assert str(event.created_at) == str(CREATED_AT)


@pytest.mark.asyncio
async def test_refunded_retry_must_pass_allowance_before_reactivation(allowance_db, monkeypatch):
    _insert_task(allowance_db)
    await _charge(allowance_db, duration=61)
    service = _service(allowance_db, monkeypatch, limit=1)
    await TaskRepository.update_task_status(service.db, "task-1", "error")
    # An unrelated duplicate ledger submission must not silently undo the refund.
    await record_minute_usage(service.db, "user-1", "task-1", 61)
    with pytest.raises(BillingLimitExceeded):
        await service.assert_can_resume_task("user-1", "task-1")
    assert allowance_db.execute(text("SELECT refunded_at FROM minute_usage_events")).scalar() is not None
    assert await service._count_usage("user-1", PERIOD_START, PERIOD_END) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("refunded", [False, True])
async def test_worker_with_lapsed_plan_can_only_reuse_a_non_refunded_charge(allowance_db, monkeypatch, refunded):
    _insert_task(allowance_db)
    await _charge(allowance_db)
    service = _service(allowance_db, monkeypatch, status="canceled")
    if refunded:
        await TaskRepository.update_task_status(service.db, "task-1", "error")
        await TaskRepository.update_task_status(service.db, "task-1", "queued")
        with pytest.raises(BillingLimitExceeded):
            await service.assert_can_resume_task("user-1", "task-1", require_active_plan=False)
    else:
        await service.assert_can_resume_task("user-1", "task-1", require_active_plan=False)
    assert await service._count_usage("user-1", PERIOD_START, PERIOD_END) == ((0, 0) if refunded else (1, 1))


@pytest.mark.asyncio
async def test_terminal_status_rolls_back_if_refund_write_fails():
    session = AsyncMock()
    session.execute.side_effect = [MagicMock(rowcount=1), RuntimeError("ledger unavailable")]
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await TaskRepository.update_task_status(session, "task-1", "error")
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_terminal_update_cannot_refund_another_attempt():
    session = AsyncMock()
    session.info = {}
    session.execute.return_value = MagicMock(rowcount=0)
    assert not await TaskRepository.update_task_status(session, "task-1", "error", expected_statuses=["processing"])
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_while_measuring_legacy_source_cannot_recharge(allowance_db, monkeypatch):
    from src.repositories.edit_transaction import TaskCancelled

    _insert_task(allowance_db, status="queued", duration=None)
    allowance_db.execute(text("INSERT INTO sources VALUES ('source-1', 'upload://video.mp4')"))
    service = _service(allowance_db, monkeypatch)

    async def measure(*_args):
        await TaskRepository.update_task_status(service.db, "task-1", "cancelled")
        return 60

    monkeypatch.setattr(VideoService, "measure_source_duration", measure)
    with pytest.raises(TaskCancelled):
        await service.assert_can_resume_task("user-1", "task-1", require_active_plan=False)
    assert allowance_db.execute(text("SELECT COUNT(*) FROM minute_usage_events")).scalar() == 0
