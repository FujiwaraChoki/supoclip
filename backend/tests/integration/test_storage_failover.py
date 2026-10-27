"""Database-backed authorization and cached health during a provider outage."""
import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import text

from src.config import Config, get_config
from src.media_replication import ReplicatedMediaClient
from src.media_storage import MediaStorage
from tests.fixtures.factories import create_clip, create_source, create_task, create_user
from tests.unit.test_media_replication import Store


@pytest.fixture
def fallback_storage(tmp_path, monkeypatch):
    from src.api.routes import tasks

    config = Config()
    config.media_storage = "s3"
    config.media_s3_bucket = "original"
    config.temp_dir = str(tmp_path)
    primary, fallback = Store("aws"), Store("r2")
    primary.failure = "InvalidAccessKeyId"
    fallback.objects["media/clips/clip.mp4"] = b"video"
    client = ReplicatedMediaClient(primary, fallback, "original", "replica", tmp_path, failover=True)
    client.generate_presigned_url = Mock(wraps=client.generate_presigned_url)
    storage = MediaStorage(config, client)
    monkeypatch.setattr(tasks, "MediaStorage", lambda: storage)
    return storage


async def seeded_clip(db, *, owner="user-1"):
    user = await create_user(db, user_id=owner)
    source = await create_source(db)
    task = await create_task(db, user_id=user["id"], source_id=source["id"])
    clip = await create_clip(db, task_id=task["id"])
    await db.execute(text("UPDATE generated_clips SET file_path=:path WHERE id=:id"),
                     {"path": "s3://original/media/clips/clip.mp4", "id": clip["id"]})
    await db.commit()
    return task, clip


@pytest.mark.parametrize("download", [False, True])
async def test_owned_playback_and_download_fail_over_after_aws_rejects_credentials(
    client, db_session, auth_headers, fallback_storage, download,
):
    task, clip = await seeded_clip(db_session)
    response = await client.get(f"/tasks/{task['id']}/clips/{clip['id']}/file",
                                params={"download": str(download).lower()}, headers=auth_headers)
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://r2.example/replica/")
    assert response.headers["cache-control"] == "private, no-store"
    assert fallback_storage.client.generate_presigned_url.call_args.kwargs["Params"]["ResponseContentDisposition"].startswith(
        "attachment" if download else "inline"
    )


async def test_failover_preserves_task_ownership(client, db_session, auth_headers, fallback_storage):
    await create_user(db_session, user_id="user-1")
    task, clip = await seeded_clip(db_session, owner="someone-else")
    response = await client.get(f"/tasks/{task['id']}/clips/{clip['id']}/file", headers=auth_headers)
    assert response.status_code in {403, 404}
    fallback_storage.client.generate_presigned_url.assert_not_called()


async def test_shared_playback_also_uses_fallback(client, db_session, fallback_storage):
    task, clip = await seeded_clip(db_session)
    token = "storage-failover-test-share-token"
    await db_session.execute(text("UPDATE tasks SET share_enabled=true,share_token=:token WHERE id=:id"),
                             {"token": token, "id": task["id"]})
    await db_session.commit()
    response = await client.get(f"/tasks/shared/{token}/clips/{clip['id']}/file")
    assert response.status_code == 307
    assert "r2.example" in response.headers["location"]
    await db_session.execute(text("UPDATE tasks SET share_enabled=false WHERE id=:id"), {"id": task["id"]})
    await db_session.commit()
    fallback_storage.client.generate_presigned_url.reset_mock()
    response = await client.get(f"/tasks/shared/{token}/clips/{clip['id']}/file")
    assert response.status_code == 404
    fallback_storage.client.generate_presigned_url.assert_not_called()


@pytest.mark.parametrize("status,age,expected", [("healthy", 0, 200), ("degraded", 0, 503), ("healthy", 200, 503)])
async def test_storage_health_uses_cached_probe_and_rejects_stale_results(client, tmp_path, status, age, expected):
    config = get_config()
    config.media_fallback_enabled = True
    config.temp_dir = str(tmp_path)
    directory = tmp_path / "media-replication"
    directory.mkdir()
    (directory / "health.json").write_text(json.dumps({"status": status, "checked_at": time.time() - age}))
    response = await client.get("/health/storage")
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    if age:
        assert response.json()["reason"] == "stale_probe"
