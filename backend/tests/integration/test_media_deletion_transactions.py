"""Database/S3 boundaries: rollback safety, deletion retries and shared sources."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import text

from src.config import Config, set_config_override
from src.media_storage import MediaStorage, owner_key
from src.repositories.clip_repository import ClipRepository
from src.repositories.edit_transaction import task_edit_transaction
from src.repositories.media_repository import queue_delete
from src.services.task_service import TaskService
from src.workers.media_cleanup import drain_deletions
from tests.fixtures.factories import create_source, create_task, create_user
from tests.unit.test_media_storage import MemoryS3


@pytest.fixture
async def media_backend(tmp_path, monkeypatch, db_session):
    await db_session.execute(text("DELETE FROM media_deletions"))
    await db_session.commit()
    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = "private-media"
    config.temp_dir = str(tmp_path)
    config.backend_auth_secret = "test-backend-auth-secret"
    config.allow_unsigned_backend_auth = False
    set_config_override(config)
    client = MemoryS3()
    monkeypatch.setattr(MediaStorage, "client", property(lambda self: client))
    yield MediaStorage(config, client)
    set_config_override(None)


async def saved_clip(db, storage):
    user = await create_user(db)
    source = await create_source(db, source_type="video_url", url="upload://source.mp4")
    task = await create_task(db, user_id=user["id"], source_id=source["id"], status="completed")
    path = Path(storage.config.temp_dir) / "clips" / "first.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"original clip")
    clip_id = await ClipRepository.create_clip(db, task["id"], path.name, str(path),
        "00:00", "00:10", 10, "caption", 1, "test", 1)
    await db.commit()
    clip = await ClipRepository.get_clip_by_id(db, clip_id)
    return task, clip, path


async def test_replacement_deletes_previous_media_only_after_commit(db_session, media_backend):
    storage = media_backend
    task, clip, local = await saved_clip(db_session, storage)
    await drain_deletions(db_session, storage)
    assert not local.exists()
    old_key = storage.parse(clip["file_path"])
    assert storage.client.objects[old_key] == b"original clip"
    replacement = local.with_name("second.mp4")
    replacement.write_bytes(b"new clip")
    with pytest.raises(RuntimeError):
        async with task_edit_transaction(db_session, task["id"]):
            await ClipRepository.update_clip(db_session, clip["id"], replacement.name,
                str(replacement), "00:00", "00:10", 10, "new caption")
            raise RuntimeError("rollback")
    await drain_deletions(db_session, storage)
    assert old_key in storage.client.objects
    assert (await ClipRepository.get_clip_by_id(db_session, clip["id"]))["file_path"] == clip["file_path"]
    async with task_edit_transaction(db_session, task["id"]):
        await ClipRepository.update_clip(db_session, clip["id"], replacement.name,
            str(replacement), "00:00", "00:10", 10, "new caption")
        assert old_key in storage.client.objects
    current = await ClipRepository.get_clip_by_id(db_session, clip["id"])
    await drain_deletions(db_session, storage)
    assert old_key not in storage.client.objects
    assert storage.parse(current["file_path"]) in storage.client.objects
    assert not replacement.exists()


async def test_failed_s3_delete_is_retained_for_retry(db_session, media_backend):
    storage = media_backend
    reference = storage.reference(storage.key("clips", "orphan", "clip.mp4"))
    storage.client.objects[storage.parse(reference)] = b"video"
    await queue_delete(db_session, reference)
    await db_session.commit()
    original_delete = storage.client.delete_object
    storage.client.delete_object = Mock(side_effect=OSError("S3 unavailable"))
    await drain_deletions(db_session, storage)
    assert (await db_session.execute(text("SELECT COUNT(*) FROM media_deletions WHERE file_path = :path"), {"path": reference})).scalar() == 1
    storage.client.delete_object = original_delete
    await db_session.execute(text("UPDATE media_deletions SET next_attempt_at = NOW()"))
    await db_session.commit()
    await drain_deletions(db_session, storage)
    assert storage.parse(reference) not in storage.client.objects
    assert (await db_session.execute(text("SELECT COUNT(*) FROM media_deletions WHERE file_path = :path"), {"path": reference})).scalar() == 0


async def test_shared_upload_is_kept_until_its_last_task_is_deleted(db_session, media_backend):
    storage = media_backend
    user = await create_user(db_session)
    url = f"upload://s3/{owner_key(user['id'])}/upload/video.mp4"
    key = storage.parse(storage.source_reference(url))
    storage.client.objects[key] = b"uploaded original"
    source = await create_source(db_session, source_type="video_url", url=url)
    first = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    second = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    await db_session.commit()
    service = TaskService(db_session)
    await service.delete_task(first["id"])
    await drain_deletions(db_session, storage)
    assert key in storage.client.objects
    await service.delete_task(second["id"])
    await drain_deletions(db_session, storage)
    assert key not in storage.client.objects


async def test_s3_playback_checks_ownership_before_signing(client, db_session, media_backend, auth_headers):
    storage = media_backend
    owner = await create_user(db_session, user_id="user-1")
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"], status="completed")
    path = Path(storage.config.temp_dir) / "clip.mp4"
    path.write_bytes(b"clip")
    clip_id = await ClipRepository.create_clip(db_session, task["id"], path.name, str(path),
        "00:00", "00:01", 1, "caption", 1, "test", 1)
    await db_session.commit()
    signer = Mock(return_value="https://private-media.test/signed-clip")
    storage.client.generate_presigned_url = signer
    url = f"/tasks/{task['id']}/clips/{clip_id}/file"
    assert (await client.get(url)).status_code == 401
    signer.assert_not_called()
    response = await client.get(url, headers=auth_headers)
    assert response.status_code == 307
    assert response.headers["location"] == "https://private-media.test/signed-clip"
    assert response.headers["cache-control"] == "private, no-store"
    assert signer.call_args.kwargs["Params"]["ResponseContentDisposition"].startswith("inline;")
    response = await client.get(url + "?download=true", headers=auth_headers)
    assert response.status_code == 307
    assert signer.call_args.kwargs["Params"]["ResponseContentDisposition"].startswith("attachment;")
    other = await create_user(db_session)
    other_task = await create_task(db_session, user_id=other["id"], source_id=source["id"], status="completed")
    signer.reset_mock()
    assert (await client.get(f"/tasks/{other_task['id']}/clips/{clip_id}/file", headers=auth_headers)).status_code == 403
    signer.assert_not_called()


async def test_migration_preserves_clip_ids_and_source_retry_metadata(db_session, media_backend, monkeypatch):
    from scripts.migrate_media_to_s3 import migrate
    from unittest.mock import AsyncMock

    storage = media_backend
    owner = await create_user(db_session)
    upload = Path(storage.config.temp_dir) / "uploads" / "legacy.mp4"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"uploaded source")
    upload.with_suffix(".transcript_cache.json").write_text('{"words": []}')
    source = await create_source(db_session, source_type="video_url", url="upload://legacy.mp4")
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"], status="completed")
    clip_path = Path(storage.config.temp_dir) / "clips" / "legacy-clip.mp4"
    clip_path.parent.mkdir()
    clip_path.write_bytes(b"rendered clip")
    clip_path.with_suffix(".source_map.json").write_text('{"source_ranges": [{"start": 1, "end": 3}]}')
    # Model an existing local installation before enabling S3.
    storage.config.media_storage = "local"
    clip_id = await ClipRepository.create_clip(db_session, task["id"], clip_path.name, str(clip_path),
        "00:01", "00:03", 2, "caption", 1, "test", 1)
    await db_session.commit()
    storage.config.media_storage = "s3"
    monkeypatch.setattr(TaskService, "_load_task_source_settings", AsyncMock(return_value={
        "output_format": "original", "add_subtitles": False, "cut_long_pauses": True,
        "pause_threshold_ms": 900, "remove_filler_words": False, "filtered_words": [],
    }))
    await migrate(task_id=task["id"])
    assert (await ClipRepository.get_clip_by_id(db_session, clip_id))["file_path"] == str(clip_path)
    assert storage.client.objects == {}
    migration = await migrate(apply=True, task_id=task["id"])
    clip = await ClipRepository.get_clip_by_id(db_session, clip_id)
    assert clip["id"] == clip_id and clip["file_path"].startswith("s3://")
    record = await TaskService(db_session).task_repo.get_task_by_id(db_session, task["id"])
    assert record["source_url"].startswith("upload://s3/")
    metadata = (await db_session.execute(text("SELECT source_metadata FROM tasks WHERE id = :id"), {"id": task["id"]})).scalar()
    assert metadata["url"] == record["source_url"]
    assert metadata["output_format"] == "original" and metadata["add_subtitles"] is False
    assert metadata["pause_threshold_ms"] == 900
    await drain_deletions(db_session, storage)
    assert clip_path.exists() and upload.exists()
    await migrate(release_cleanup=migration["cleanup_token"])
    await drain_deletions(db_session, storage)
    assert not clip_path.exists() and not upload.exists()
    assert storage.parse(clip["file_path"]) in storage.client.objects
    assert storage.parse(storage.source_reference(record["source_url"])) in storage.client.objects


async def test_full_migration_does_not_limit_sources_to_its_last_clip_task(db_session, media_backend):
    from scripts.migrate_media_to_s3 import migrate

    storage = media_backend
    owner = await create_user(db_session)
    root = Path(storage.config.temp_dir)
    (root / "uploads").mkdir()
    (root / "clips").mkdir()
    task_ids = []
    storage.config.media_storage = "local"
    for index in range(2):
        (root / "uploads" / f"legacy-{index}.mp4").write_bytes(b"original")
        source = await create_source(db_session, source_type="video_url", url=f"upload://legacy-{index}.mp4")
        task = await create_task(db_session, user_id=owner["id"], source_id=source["id"], status="completed")
        path = root / "clips" / f"clip-{index}.mp4"
        path.write_bytes(b"clip")
        await ClipRepository.create_clip(db_session, task["id"], path.name, str(path), "00:00", "00:01", 1, "caption", 1, "test", 1)
        await db_session.commit()
        task_ids.append(task["id"])
    storage.config.media_storage = "s3"
    await migrate(apply=True)
    for task_id in task_ids:
        record = await TaskService(db_session).task_repo.get_task_by_id(db_session, task_id)
        assert record["source_url"].startswith("upload://s3/")


async def test_one_invalid_reference_does_not_block_other_deletions(db_session, media_backend):
    storage = media_backend
    bad = "s3://old-bucket/media/clips/orphan/clip.mp4"
    good = storage.reference(storage.key("clips", "orphan", "upload", "clip.mp4"))
    storage.client.objects[storage.parse(good)] = b"video"
    await queue_delete(db_session, bad)
    await queue_delete(db_session, good)
    await db_session.commit()
    await drain_deletions(db_session, storage)
    assert storage.parse(good) not in storage.client.objects
    result = (await db_session.execute(text("SELECT file_path, attempts, next_attempt_at > NOW() AS deferred FROM media_deletions"))).one()
    assert result.file_path == bad
    assert result.attempts == 1
    assert result.deferred
