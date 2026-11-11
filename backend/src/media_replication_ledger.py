"""Where each media object's replication stands, visible to every host.

One row per object key that a store has not acknowledged yet, plus the latest
storage health report. Hosts share PostgreSQL but no disk, so this is what lets
any of them route a read, retry a replica or answer /health/storage.

Rows are plain dicts named after the table's columns. Every call is synchronous
and safe from any thread: the storage client is called from worker threads.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timedelta, timezone
import json
import threading
import uuid

from sqlalchemy import text

from . import sync_db
from .sync_db import LockBusy

__all__ = ["LockBusy", "MemoryLedger", "PostgresLedger", "read_storage_health"]

COLUMNS = ("object_key, operation, source_store, pending, sha256, size, extra, op_id, "
           "attempts, next_attempt_at, created_at")
# One drainer across all hosts. A namespace of its own, so no object key can collide.
DRAIN_NAMESPACE = 113471


def backoff_seconds(attempts: int) -> int:
    """Wait after a failed attempt: 60s, doubling, capped at one hour."""
    return min(3600, 60 * 2 ** min(attempts, 6))


def _new_row(key, operation, pending, source, sha256, size, extra):
    return {"object_key": key, "operation": operation, "source_store": source,
            "pending": list(pending), "sha256": sha256, "size": size, "extra": dict(extra or {}),
            "op_id": uuid.uuid4().hex, "attempts": 0, "next_attempt_at": None, "created_at": None}


class PostgresLedger:
    """The production ledger. Stateless: every instance sees the same rows."""

    _UPSERT = f"""
        INSERT INTO media_replication_ops ({COLUMNS})
        VALUES ($1, $2, $3, $4::text[], $5, $6, $7::jsonb, $8, $9,
                COALESCE($10::timestamptz, NOW()), COALESCE($11::timestamptz, NOW()))
        ON CONFLICT (object_key) DO UPDATE SET
            operation = EXCLUDED.operation, source_store = EXCLUDED.source_store,
            pending = EXCLUDED.pending, sha256 = EXCLUDED.sha256, size = EXCLUDED.size,
            extra = EXCLUDED.extra, op_id = EXCLUDED.op_id, attempts = EXCLUDED.attempts,
            next_attempt_at = EXCLUDED.next_attempt_at, created_at = EXCLUDED.created_at
        RETURNING {COLUMNS}
    """
    # The delete and the update test the same snapshot with opposite conditions,
    # so one statement touches the row once. The final read also sees that
    # snapshot: it reports another revision's row, or nothing once ours is removed.
    _CONFIRM = """
        WITH removed AS (
            DELETE FROM media_replication_ops
            WHERE object_key = $1 AND op_id = $3 AND pending <@ ARRAY[$2::text]
            RETURNING object_key
        ), updated AS (
            UPDATE media_replication_ops SET pending = array_remove(pending, $2::text)
            WHERE object_key = $1 AND op_id = $3 AND NOT (pending <@ ARRAY[$2::text])
            RETURNING pending
        )
        SELECT COALESCE(
            (SELECT pending FROM updated),
            (SELECT pending FROM media_replication_ops
             WHERE object_key = $1 AND NOT EXISTS (SELECT 1 FROM removed)),
            ARRAY[]::text[])
    """

    @staticmethod
    def _row(record):
        if record is None or record["op_id"] is None:
            return None
        row = {name: record[name] for name in COLUMNS.split(", ")}
        row["pending"] = list(row["pending"])
        row["extra"] = json.loads(row["extra"])
        return row

    def _write(self, row):
        return self._row(sync_db.fetchrow(
            self._UPSERT, row["object_key"], row["operation"], row["source_store"], list(row["pending"]),
            row["sha256"], row["size"], json.dumps(row["extra"] or {}, allow_nan=False), row["op_id"],
            row["attempts"], row["next_attempt_at"], row["created_at"]))

    def lock(self, key, *, blocking=True):
        return sync_db.advisory_lock(key, blocking=blocking)

    def drain_lock(self):
        return sync_db.advisory_lock("drain", blocking=False, namespace=DRAIN_NAMESPACE)

    def get(self, key):
        return self._row(sync_db.fetchrow(
            f"SELECT {COLUMNS} FROM media_replication_ops WHERE object_key = $1", key))

    def begin(self, key, operation, *, pending, source=None, sha256=None, size=None, extra=None):
        return self._write(_new_row(key, operation, pending, source, sha256, size, extra))

    def restore(self, row):
        return self._write(row)

    def clear(self, key, *, op_id=None):
        sync_db.execute("DELETE FROM media_replication_ops WHERE object_key = $1 "
                        "AND ($2::text IS NULL OR op_id = $2)", key, op_id)

    def confirm(self, key, store, *, op_id):
        return list(sync_db.fetchval(self._CONFIRM, key, store, op_id))

    def due(self, limit):
        return [self._row(record) for record in sync_db.fetch(
            f"SELECT {COLUMNS} FROM media_replication_ops WHERE next_attempt_at <= NOW() "
            "ORDER BY next_attempt_at, created_at LIMIT $1", limit)]

    def defer(self, key, *, op_id):
        sync_db.execute(
            "UPDATE media_replication_ops SET attempts = attempts + 1, next_attempt_at = NOW() + "
            "make_interval(secs => LEAST(3600, 60 * POWER(2, LEAST(attempts, 6)))) "
            "WHERE object_key = $1 AND op_id = $2", key, op_id)

    def status(self):
        record = sync_db.fetchrow(
            "SELECT COUNT(*) AS pending, COALESCE(EXTRACT(EPOCH FROM NOW() - MIN(created_at)), 0) AS oldest "
            "FROM media_replication_ops")
        return {"pending": int(record["pending"]), "oldest_pending_seconds": max(0, int(record["oldest"]))}

    def routing(self, key):
        # Runs for every signed URL: the object's row and the health report in one round trip.
        record = sync_db.fetchrow(
            f"SELECT {', '.join('o.' + name for name in COLUMNS.split(', '))}, h.report AS health "
            "FROM (SELECT 1) AS one "
            "LEFT JOIN media_replication_ops o ON o.object_key = $1 "
            "LEFT JOIN media_storage_health h ON h.id", key)
        return self._row(record), json.loads(record["health"]) if record["health"] else None

    def write_health(self, report):
        # The report carries the probe's own clock, which is what readers judge freshness by.
        sync_db.execute(
            "INSERT INTO media_storage_health (id, report, checked_at) "
            "VALUES (TRUE, $1::jsonb, COALESCE(to_timestamp($2::double precision), NOW())) "
            "ON CONFLICT (id) DO UPDATE SET report = EXCLUDED.report, checked_at = EXCLUDED.checked_at",
            json.dumps(report, allow_nan=False), report.get("checked_at"))


class MemoryLedger:
    """The same contract inside one process, for unit tests. Never used in production."""

    def __init__(self):
        self._guard = threading.Lock()
        self._rows = {}
        self._locks = {}
        self._health = None

    @contextmanager
    def lock(self, key, *, blocking=True):
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking):
            raise LockBusy("Lock is held by another caller")
        try:
            yield
        finally:
            lock.release()

    def drain_lock(self):
        # NUL cannot appear in an S3 object key.
        return self.lock("\x00drain", blocking=False)

    def _write(self, row):
        now = datetime.now(timezone.utc)
        stored = copy.deepcopy({**row, "pending": list(row["pending"]),
                                "next_attempt_at": row["next_attempt_at"] or now,
                                "created_at": row["created_at"] or now})
        with self._guard:
            self._rows[row["object_key"]] = stored
        return copy.deepcopy(stored)

    def get(self, key):
        with self._guard:
            return copy.deepcopy(self._rows.get(key))

    def begin(self, key, operation, *, pending, source=None, sha256=None, size=None, extra=None):
        return self._write(_new_row(key, operation, pending, source, sha256, size, extra))

    def restore(self, row):
        return self._write(row)

    def clear(self, key, *, op_id=None):
        with self._guard:
            if op_id is None or self._rows.get(key, {}).get("op_id") == op_id:
                self._rows.pop(key, None)

    def confirm(self, key, store, *, op_id):
        with self._guard:
            row = self._rows.get(key)
            if row is None:
                return []
            if row["op_id"] == op_id:
                row["pending"] = [name for name in row["pending"] if name != store]
                if not row["pending"]:
                    del self._rows[key]
            return list(row["pending"])

    def due(self, limit):
        now = datetime.now(timezone.utc)
        with self._guard:
            rows = sorted((row for row in self._rows.values() if row["next_attempt_at"] <= now),
                          key=lambda row: (row["next_attempt_at"], row["created_at"]))
            return copy.deepcopy(rows[:limit])

    def defer(self, key, *, op_id):
        with self._guard:
            row = self._rows.get(key)
            if row is not None and row["op_id"] == op_id:
                row["next_attempt_at"] = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(row["attempts"]))
                row["attempts"] += 1

    def status(self):
        now = datetime.now(timezone.utc)
        with self._guard:
            oldest = min((row["created_at"] for row in self._rows.values()), default=now)
            return {"pending": len(self._rows), "oldest_pending_seconds": max(0, int((now - oldest).total_seconds()))}

    def routing(self, key):
        with self._guard:
            return copy.deepcopy(self._rows.get(key)), copy.deepcopy(self._health)

    def write_health(self, report):
        stored = json.loads(json.dumps(report, allow_nan=False))
        with self._guard:
            self._health = stored


async def read_storage_health(db) -> dict | None:
    """The latest probe report, for request handlers that already use a session."""
    report = (await db.execute(text("SELECT report FROM media_storage_health WHERE id"))).scalar()
    return json.loads(report) if isinstance(report, str) else report
