"""The dynamic edits migration (§3.4): two nullable columns and a flag row that never names a user (AI-C1).

The database tests build throwaway databases on the TEST_DATABASE_URL server, so they prove the migration on an
empty ``users`` table and on a database booted from ``init.sql`` (which already seeds the ``tiktok_publishing`` flag).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src import database
from src import feature_flags

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "src/migrations/sql/20261114_0001_dynamic_edits.sql"
INIT_SQL = BACKEND.parent / "init.sql"

EXPECTED_STATEMENTS = [
    # ALTER TABLE takes ACCESS EXCLUSIVE: behind a long edit transaction it would queue and block every query
    # on tasks, so it gives up after 5 s instead (the feedback migrations' pattern).
    "SET LOCAL lock_timeout = '5s'",
    "SET LOCAL statement_timeout = '30s'",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS edit_style JSONB",
    "ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS fx_meta JSONB",
    "INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass) VALUES ('dynamic_edits', "
    "'Edit intensity, SFX, music and animated caption styles', false, false, true) ON CONFLICT (key) DO NOTHING",
    "SET LOCAL lock_timeout = DEFAULT",
    "SET LOCAL statement_timeout = DEFAULT",
]


def _statements(sql: str) -> list[str]:
    """Split like ``init_db`` does, dropping comment lines so the statements compare cleanly."""
    out = []
    for part in sql.split(";"):
        lines = [line for line in part.strip().splitlines() if not line.strip().startswith("--")]
        statement = " ".join(line.strip() for line in lines).strip()
        if statement:
            out.append(statement)
    return out


# --------------------------------------------------------------------------- file shape (no database)


def test_migration_is_the_three_statements_of_the_design_under_a_lock_timeout():
    assert _statements(MIGRATION.read_text()) == EXPECTED_STATEMENTS
    # The runner's own splitter sees the same statements, the timeouts first and reset last.
    from src.sql_statements import split_sql_statements

    assert _statements(";".join(split_sql_statements(MIGRATION.read_text()))) == EXPECTED_STATEMENTS


def test_migration_runs_under_init_db_statement_splitting():
    sql = MIGRATION.read_text()
    assert "DO $$" not in sql and "$$" not in sql   # init_db splits on ';' and cannot run DO blocks
    assert "updated_by" not in "".join(EXPECTED_STATEMENTS)
    raw_parts = [part.strip() for part in sql.split(";") if part.strip()]
    assert len(raw_parts) == len(EXPECTED_STATEMENTS)


def test_migration_sorts_after_every_earlier_migration():
    names = sorted(path.name for path in MIGRATION.parent.glob("*.sql"))
    earlier = [name for name in names if name < MIGRATION.name]
    assert "20261107_0001_tiktok_publishing_flag.sql" in earlier
    assert re.fullmatch(r"\d{8}_\d{4}_[a-z_]+\.sql", MIGRATION.name)


def test_init_sql_mirrors_the_migration():
    init_sql = INIT_SQL.read_text()
    tasks_table = init_sql[init_sql.index("CREATE TABLE tasks ("):]
    tasks_table = tasks_table[: tasks_table.index(");")]
    clips_table = init_sql[init_sql.index("CREATE TABLE generated_clips ("):]
    clips_table = clips_table[: clips_table.index(");")]
    assert re.search(r"^\s*edit_style JSONB,$", tasks_table, re.MULTILINE)
    assert re.search(r"^\s*fx_meta JSONB,$", clips_table, re.MULTILINE)
    # Nullable with no default: NULL is "legacy".
    assert "edit_style JSONB NOT NULL" not in init_sql and "fx_meta JSONB DEFAULT" not in init_sql
    tiktok = init_sql.index("'tiktok_publishing',")
    dynamic = init_sql.index("'dynamic_edits',")
    assert tiktok < dynamic
    flag_insert = init_sql[init_sql.rindex("INSERT INTO feature_flags", 0, dynamic):]
    flag_insert = flag_insert[: flag_insert.index(");") + 2]
    assert "updated_by" not in flag_insert
    assert re.sub(r"\s+", " ", flag_insert) == (
        "INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass) VALUES ( "
        "'dynamic_edits', 'Edit intensity, SFX, music and animated caption styles', false, false, true );"
    )


def test_default_flag_is_off_with_the_admin_bypass():
    flag = feature_flags.DEFAULT_FLAGS[feature_flags.DYNAMIC_EDITS]
    assert feature_flags.DYNAMIC_EDITS == "dynamic_edits"
    assert (flag.web_enabled, flag.ios_enabled, flag.admins_bypass) == (False, False, True)


# --------------------------------------------------------------------------- real databases


@pytest.fixture
async def scratch_database():
    """A fresh, empty database on the test server; dropped afterwards."""
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL or TEST_DATABASE_URL must be set for database tests")
    asyncpg = pytest.importorskip("asyncpg")
    base = make_url(url)
    admin_dsn = base.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    name = f"wp11_mig_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    scratch = base.set(database=name)
    try:
        yield scratch
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _run_init_db(url) -> None:
    """``database.init_db`` against ``url``, then put the suite's database configuration back."""
    saved = (database._database_url_override, database._engine_override, database._session_maker_override)
    engine = create_async_engine(url.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        database.configure_database(engine=engine)
        await database.init_db()
    finally:
        database.configure_database(database_url=saved[0], engine=saved[1], session_maker=saved[2])
        await engine.dispose()


async def _inspect(url) -> dict:
    engine = create_async_engine(url.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            columns = (
                await conn.execute(
                    text(
                        """
                        SELECT table_name, column_name, data_type, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE (table_name = 'tasks' AND column_name = 'edit_style')
                           OR (table_name = 'generated_clips' AND column_name = 'fx_meta')
                        """
                    )
                )
            ).all()
            flags = (
                await conn.execute(
                    text(
                        "SELECT key, description, web_enabled, ios_enabled, admins_bypass, updated_by "
                        "FROM feature_flags ORDER BY key"
                    )
                )
            ).mappings().all()
            users = (await conn.execute(text("SELECT COUNT(*) FROM users"))).scalar()
            applied = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM schema_migrations WHERE version = :version"),
                    {"version": MIGRATION.name},
                )
            ).scalar() if await _has_table(conn, "schema_migrations") else 0
    finally:
        await engine.dispose()
    return {"columns": {row.column_name: row for row in columns}, "flags": {row["key"]: row for row in flags},
            "users": users, "applied": applied}


async def _has_table(conn, name: str) -> bool:
    return bool((await conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name})).scalar())


def _assert_dynamic_edits_schema(state: dict) -> None:
    columns = state["columns"]
    assert set(columns) == {"edit_style", "fx_meta"}
    for column in columns.values():
        assert column.data_type == "jsonb"
        assert column.is_nullable == "YES"
        assert column.column_default is None
    flag = state["flags"]["dynamic_edits"]
    assert flag["description"] == "Edit intensity, SFX, music and animated caption styles"
    assert (flag["web_enabled"], flag["ios_enabled"], flag["admins_bypass"]) == (False, False, True)
    assert flag["updated_by"] is None


async def test_migration_applies_on_an_empty_database(scratch_database):
    await _run_init_db(scratch_database)

    state = await _inspect(scratch_database)
    assert state["users"] == 0   # nothing for an updated_by FK to point at
    assert state["applied"] == 1
    _assert_dynamic_edits_schema(state)


async def test_init_sql_boots_and_the_migration_is_a_no_op_on_top(scratch_database):
    asyncpg = pytest.importorskip("asyncpg")
    connection = await asyncpg.connect(scratch_database.set(drivername="postgresql").render_as_string(
        hide_password=False))
    try:
        await connection.execute(INIT_SQL.read_text())
    finally:
        await connection.close()

    booted = await _inspect(scratch_database)
    _assert_dynamic_edits_schema(booted)
    assert booted["flags"]["tiktok_publishing"]["admins_bypass"] is False

    # What a deploy does next: the backend runs every migration on the init.sql database.
    await _run_init_db(scratch_database)
    migrated = await _inspect(scratch_database)
    _assert_dynamic_edits_schema(migrated)
    assert migrated["applied"] == 1
    assert set(migrated["flags"]) == set(booted["flags"])


async def test_migration_keeps_an_admin_choice_and_reruns_cleanly(db_session):
    """On the suite's database (already migrated, with the tiktok flag): re-running changes nothing."""
    await db_session.execute(text("UPDATE feature_flags SET web_enabled = true WHERE key = 'dynamic_edits'"))
    try:
        for _ in range(2):
            for statement in _statements(MIGRATION.read_text()):
                await db_session.execute(text(statement))
        row = (
            await db_session.execute(
                text("SELECT web_enabled FROM feature_flags WHERE key = 'dynamic_edits'")
            )
        ).one()
        assert row.web_enabled is True
        tiktok = (
            await db_session.execute(text("SELECT COUNT(*) FROM feature_flags WHERE key = 'tiktok_publishing'"))
        ).scalar()
        assert tiktok == 1
    finally:
        await db_session.rollback()
