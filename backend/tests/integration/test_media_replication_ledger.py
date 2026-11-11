"""The replication ledger against a real PostgreSQL, as two hosts would share it."""
import asyncio
import threading

import pytest
from sqlalchemy import text

from src import sync_db
from src.media_replication import ReplicatedMediaClient
from src.media_replication_ledger import LockBusy, PostgresLedger, read_storage_health
from tests.unit.test_media_replication import KEY, LedgerContract, Store, sha


@pytest.fixture
def ledger(initialized_database):
    def empty():
        sync_db.execute("DELETE FROM media_replication_ops")
        sync_db.execute("DELETE FROM media_storage_health")

    empty()
    yield PostgresLedger()
    empty()
    sync_db.reset()


class TestPostgresLedger(LedgerContract):
    pass


def test_every_ledger_instance_sees_the_same_rows(ledger):
    row = ledger.begin(KEY, "delete", pending=["primary", "fallback"])
    assert PostgresLedger().get(KEY) == row
    assert PostgresLedger().confirm(KEY, "primary", op_id=row["op_id"]) == ["fallback"]
    assert ledger.get(KEY)["pending"] == ["fallback"]


def test_lock_holds_a_connection_of_its_own(ledger):
    # Sessions holding this key's lock in this database, other than the one asking.
    holders = """
        SELECT COUNT(*) FROM pg_locks
        WHERE locktype = 'advisory' AND granted AND pid <> pg_backend_pid()
          AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND ((classid::bigint << 32) | objid::bigint) = hashtextextended($1, 113470)
    """
    with ledger.lock(KEY):
        # Short queries keep working while a long upload holds the lock.
        assert sync_db.fetchval(holders, KEY) == 1
        assert ledger.status()["pending"] == 0
    assert sync_db.fetchval(holders, KEY) == 0


def test_lock_excludes_another_thread_as_it_would_another_host(ledger):
    outcome = []

    def other_host():
        try:
            with PostgresLedger().lock(KEY, blocking=False):
                outcome.append("acquired")
        except LockBusy:
            outcome.append("busy")

    with ledger.lock(KEY):
        thread = threading.Thread(target=other_host)
        thread.start()
        thread.join(10)
    assert outcome == ["busy"]
    other_host()
    assert outcome == ["busy", "acquired"]


def test_queries_run_from_a_plain_thread(ledger):
    results = []
    thread = threading.Thread(target=lambda: results.append(sync_db.fetchval("SELECT $1::int + 1", 41)))
    thread.start()
    thread.join(10)
    assert results == [42]
    assert sync_db.fetchrow("SELECT 1 AS one, $1::text AS name", "clip")["name"] == "clip"
    assert [record["n"] for record in sync_db.fetch("SELECT generate_series(1, 3) AS n")] == [1, 2, 3]
    assert sync_db.execute("SELECT 1") == "SELECT 1"


async def test_queries_run_inside_a_running_event_loop(ledger):
    # The storage client is sometimes called directly on the loop's own thread.
    assert asyncio.get_running_loop().is_running()
    assert sync_db.fetchval("SELECT 42") == 42
    row = ledger.begin(KEY, "delete", pending=["primary"])
    with ledger.lock(KEY):
        assert ledger.get(KEY) == row
    # And, as in production, from the worker threads next to boto calls.
    assert await asyncio.to_thread(ledger.get, KEY) == row
    assert await asyncio.gather(*(asyncio.to_thread(sync_db.fetchval, "SELECT $1::int", n) for n in range(8))) == list(range(8))


def test_connections_are_replaced_after_reset_and_after_a_dropped_session(ledger):
    assert sync_db.fetchval("SELECT 1") == 1
    sync_db.reset()
    sync_db.reset()
    assert sync_db.fetchval("SELECT 1") == 1
    # The server ends the session, as a failover or restart would. Whether the
    # statement ran on that session or beside it, the caller never sees the loss.
    pid = sync_db.fetchval("SELECT pg_backend_pid()")
    assert sync_db.fetchval("SELECT pg_terminate_backend($1)", pid) in (True, False)
    assert sync_db.fetchval("SELECT 1") == 1
    assert sync_db.fetchval("SELECT pg_backend_pid()") != pid


def test_statement_errors_are_not_retried_or_hidden(ledger):
    import asyncpg

    with pytest.raises(asyncpg.PostgresError):
        ledger.begin(KEY, "rename", pending=["primary"])
    assert ledger.get(KEY) is None


async def test_health_report_is_read_through_a_request_session(ledger, db_session):
    assert await read_storage_health(db_session) is None
    report = {"status": "healthy", "checked_at": 1794000000.5, "providers": {"primary": {"status": "healthy"}},
              "replication": {"pending": 0, "oldest_pending_seconds": 0}}
    ledger.write_health(report)
    await db_session.rollback()
    assert await read_storage_health(db_session) == report
    stored = (await db_session.execute(text(
        "SELECT EXTRACT(EPOCH FROM checked_at) FROM media_storage_health"))).scalar()
    assert float(stored) == 1794000000.5


@pytest.mark.parametrize("failed", ["primary", "fallback"])
def test_upload_through_an_outage_is_drained_by_another_host(ledger, tmp_path, failed):
    primary, fallback = Store("aws"), Store("r2")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    # No ledger passed: this is the client exactly as MediaStorage builds it.
    writer = ReplicatedMediaClient(primary, fallback, "original", "replica", tmp_path / "render-host", failover=True)
    writer.stores[failed][0].failure = "ServiceUnavailable"
    writer.upload_file(str(video), "original", KEY, ExtraArgs={"ContentType": "video/mp4"})
    row = ledger.get(KEY)
    assert (row["operation"], row["pending"], row["sha256"], row["size"]) == ("upload", [failed], sha(b"video"), 5)
    api = ReplicatedMediaClient(primary, fallback, "original", "replica", tmp_path / "api-host", failover=True)
    assert api.pending_status()["pending"] == 1
    # Reads on a host that never saw the upload go to the store that has it.
    assert api._read_names(KEY)[-1] == failed
    restored = tmp_path / "restored.mp4"
    api.download_file("original", KEY, str(restored))
    assert restored.read_bytes() == b"video"
    # Still down: the retry is deferred, not lost.
    assert api.sync_pending()["pending"] == 1
    assert ledger.get(KEY)["attempts"] == 1
    writer.stores[failed][0].failure = None
    sync_db.execute("UPDATE media_replication_ops SET next_attempt_at = NOW()")
    assert api.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects == {KEY: b"video"}
    assert primary.metadata[KEY] == fallback.metadata[KEY]
    assert ledger.get(KEY) is None
    assert not list((api.root / "scratch").glob("*"))


def test_only_one_host_drains_at_a_time(ledger, tmp_path):
    primary, fallback = Store("aws"), Store("r2")
    client = ReplicatedMediaClient(primary, fallback, "original", "replica", tmp_path, failover=True)
    primary.objects[KEY] = b"browser-upload"
    ledger.begin(KEY, "copy", source="primary", pending=["fallback"])
    with ledger.drain_lock():
        assert client.sync_pending()["pending"] == 1
        assert not fallback.objects
    assert client.sync_pending()["pending"] == 0
    assert fallback.objects == primary.objects


def test_delete_through_an_outage_leaves_a_tombstone_any_host_applies(ledger, tmp_path):
    primary, fallback = Store("aws"), Store("r2")
    primary.objects[KEY] = fallback.objects[KEY] = b"video"
    client = ReplicatedMediaClient(primary, fallback, "original", "replica", tmp_path / "api-host", failover=True)
    fallback.failure = "ServiceUnavailable"
    with pytest.raises(Exception, match="ServiceUnavailable"):
        client.delete_object(Bucket="original", Key=KEY)
    assert ledger.get(KEY)["pending"] == ["fallback"]
    fallback.failure = None
    other = ReplicatedMediaClient(primary, fallback, "original", "replica", tmp_path / "cron-host", failover=True)
    assert other.sync_pending()["pending"] == 0
    assert primary.objects == fallback.objects == {}
