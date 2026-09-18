from contextlib import asynccontextmanager

import pytest


@pytest.fixture
def isolated_clip_edits(monkeypatch):
    """Keep service unit tests independent of the PostgreSQL edit boundary.

    Real transaction behavior is covered in integration/test_clip_edit_atomicity.py.
    """
    @asynccontextmanager
    async def transaction(_db, _task_id):
        yield

    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )
