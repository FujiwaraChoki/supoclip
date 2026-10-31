from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.repositories import clip_repository as repo


@pytest.mark.asyncio
async def test_update_clip_clears_package_and_copy_on_timing_or_text_change(monkeypatch):
    monkeypatch.setattr(repo, "persist_clip", AsyncMock(side_effect=lambda db, p, t: p))
    monkeypatch.setattr(repo, "queue_clip_delete", AsyncMock())
    monkeypatch.setattr(repo, "commit_unless_editing", AsyncMock())
    previous = MagicMock()
    previous.mappings.return_value.first.return_value = {
        "id": "c", "task_id": "t", "filename": "f", "file_path": "p",
    }
    db = SimpleNamespace(execute=AsyncMock(side_effect=[previous, None]))
    await repo.ClipRepository.update_clip(db, "c", "f", "p", "00:01", "00:02", 1.0, "text")
    sql = str(db.execute.await_args_list[1].args[0])
    for column in ("clip_package", "copy_kit"):
        block = sql.split(f"{column} = CASE")[1].split("END")[0]
        assert "text IS DISTINCT FROM :text" in block
        assert "start_time IS DISTINCT FROM :start_time" in block
        assert "ABS(duration" in block


def test_regenerate_upsert_clears_copy_kit():
    import inspect

    source = inspect.getsource(repo.ClipRepository.create_clip)
    assert "clip_package = NULL" in source and "copy_kit = NULL" in source
