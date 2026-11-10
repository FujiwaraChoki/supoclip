"""sync_db without a database: where it connects, and what it does when a connection fails."""
from types import SimpleNamespace
import threading
from urllib.parse import quote
import uuid

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from src import database, sync_db

# Made up for each run, with a character that must be percent-decoded. Nothing
# here is a real credential, and no literal looks like one to a secret scanner.
DATABASE_SECRET = f"a@{uuid.uuid4().hex}"


class Pool:
    def __init__(self, options):
        self.options = options
        self.failures = []
        self.closed = False
        self.statements = []

    async def fetchval(self, sql, *args):
        self.statements.append((sql, args))
        if self.failures:
            raise self.failures.pop(0)
        return self.options["database"]

    async def close(self):
        self.closed = True


class Connection:
    def __init__(self, granted=True):
        self.granted = granted
        self.statements = []
        self.closed = self.terminated = False

    async def fetchval(self, sql, *args, **kwargs):
        self.statements.append((sql, args))
        return self.granted

    async def execute(self, sql, *args, **kwargs):
        self.statements.append((sql, args))

    async def close(self, **kwargs):
        self.closed = True

    def terminate(self):
        self.terminated = True


@pytest.fixture
def fake(monkeypatch):
    sync_db.reset()
    state = SimpleNamespace(pools=[], connections=[], granted=True)

    async def create_pool(**options):
        state.pools.append(Pool(options))
        return state.pools[-1]

    async def connect(**options):
        state.connections.append(Connection(state.granted))
        state.connections[-1].options = options
        return state.connections[-1]

    def use(url):
        monkeypatch.setattr(database, "get_engine", lambda: SimpleNamespace(url=make_url(url)))

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(asyncpg, "connect", connect)
    state.use = use
    use(f"postgresql+asyncpg://app:{quote(DATABASE_SECRET, safe='')}@db.internal:6543/clips?ssl=require")
    yield state
    sync_db.reset()


def test_connects_where_the_configured_engine_points_never_the_environment(fake, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://prod:{uuid.uuid4().hex}@production/supoclip")
    assert sync_db.fetchval("SELECT 1") == "clips"
    options = fake.pools[0].options
    assert {name: options[name] for name in ("host", "port", "user", "password", "database", "ssl")} == {
        "host": "db.internal", "port": 6543, "user": "app", "password": DATABASE_SECRET, "database": "clips", "ssl": "require"}
    assert options["max_size"] == 2


def test_unset_parts_of_the_url_are_left_to_the_driver_defaults(fake):
    fake.use("postgresql+asyncpg://localhost/clips")
    sync_db.fetchval("SELECT 1")
    options = fake.pools[0].options
    assert options["host"] == "localhost" and options["database"] == "clips"
    assert not {"port", "user", "password", "ssl"} & set(options)


def test_reconfigured_engine_drops_the_cached_connections(fake):
    assert sync_db.fetchval("SELECT 1") == "clips"
    assert sync_db.fetchval("SELECT 1") == "clips"
    assert len(fake.pools) == 1
    fake.use("postgresql+asyncpg://app@db.internal/disposable")
    assert sync_db.fetchval("SELECT 1") == "disposable"
    assert len(fake.pools) == 2 and fake.pools[0].closed and not fake.pools[1].closed


def test_dropped_connection_is_retried_once(fake):
    sync_db.fetchval("SELECT 1")
    fake.pools[0].failures = [asyncpg.ConnectionDoesNotExistError("connection was closed")]
    assert sync_db.fetchval("SELECT 2", 7) == "clips"
    assert fake.pools[0].statements[-2:] == [("SELECT 2", (7,)), ("SELECT 2", (7,))]
    fake.pools[0].failures = [ConnectionResetError(), ConnectionResetError()]
    with pytest.raises(ConnectionResetError):
        sync_db.fetchval("SELECT 3")


def test_rejected_statement_is_not_retried(fake):
    sync_db.fetchval("SELECT 1")
    fake.pools[0].failures = [asyncpg.UniqueViolationError("duplicate key")]
    with pytest.raises(asyncpg.UniqueViolationError):
        sync_db.fetchval("INSERT")
    assert [sql for sql, _ in fake.pools[0].statements] == ["SELECT 1", "INSERT"]


def test_reset_closes_connections_and_is_safe_to_repeat(fake):
    sync_db.fetchval("SELECT 1")
    sync_db.reset()
    sync_db.reset()
    assert fake.pools[0].closed
    assert sync_db.fetchval("SELECT 1") == "clips"
    assert len(fake.pools) == 2


def test_lock_uses_its_own_connection_and_closes_it(fake):
    sync_db.fetchval("SELECT 1")
    with sync_db.advisory_lock("media/clip.mp4"):
        connection = fake.connections[0]
        assert connection.options["database"] == "clips" and not connection.closed
        assert connection.statements == [("SELECT pg_advisory_lock(hashtextextended($1, $2))", ("media/clip.mp4", 113470))]
        assert not fake.pools[0].statements[1:]
    assert connection.closed
    assert connection.statements[-1][0].startswith("SELECT pg_advisory_unlock(")


def test_lock_connection_is_closed_when_the_holder_fails(fake):
    with pytest.raises(RuntimeError):
        with sync_db.advisory_lock("media/clip.mp4"):
            raise RuntimeError("upload failed")
    assert fake.connections[0].closed


def test_busy_lock_raises_and_leaves_no_connection_behind(fake):
    fake.granted = False
    with pytest.raises(BlockingIOError) as raised:
        with sync_db.advisory_lock("media/clip.mp4", blocking=False, namespace=7):
            raise AssertionError("entered without the lock")
    assert isinstance(raised.value, sync_db.LockBusy)
    connection = fake.connections[0]
    assert connection.terminated
    assert connection.statements == [("SELECT pg_try_advisory_lock(hashtextextended($1, $2))", ("media/clip.mp4", 7))]


def test_callers_on_many_threads_share_the_one_loop(fake):
    results = []
    threads = [threading.Thread(target=lambda: results.append(sync_db.fetchval("SELECT 1"))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert results == ["clips"] * 8
    assert len(fake.pools) == 1
