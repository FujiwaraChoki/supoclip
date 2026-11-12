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


@pytest.fixture(autouse=True)
def single_model_chain(monkeypatch):
    """A unit test must never reach a second model of the chain.

    The chain exists for a running deployment. In a test the stand-in agent
    of the primary model is the only one that may be called, so a failing
    model cannot turn into a request to a provider.
    """
    from src import ai

    monkeypatch.setattr(
        ai, "llm_chain", lambda runtime_config=None: (ai.configured_llm(runtime_config),)
    )


@pytest.fixture(autouse=True)
def editor_store():
    """Studio sessions and jobs in memory; unit tests have no PostgreSQL."""
    from src import editor_state
    from tests.fixtures.editor_store import MemoryEditorStore

    store = MemoryEditorStore()
    editor_state.set_store(store)
    try:
        yield store
    finally:
        editor_state.set_store(None)
