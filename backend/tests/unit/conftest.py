from contextlib import asynccontextmanager

import pytest
from unittest.mock import AsyncMock


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

    monkeypatch.setattr(
        "src.repositories.task_repository.TaskRepository.load_source_metadata",
        AsyncMock(return_value={}),
    )


@pytest.fixture(autouse=True)
def disable_paid_youtube_fallback(monkeypatch):
    """Local dotenv credentials must never turn a unit test into a paid request."""
    monkeypatch.setenv("RAPIDAPI_YOUTUBE_API_KEY", "")
