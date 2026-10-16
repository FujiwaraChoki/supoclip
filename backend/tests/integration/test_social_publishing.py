from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.services import social_service
from src.services.social_service import SocialService
from src.social.base import PublishResult, SocialProviderError
from tests.fixtures.factories import create_clip, create_source, create_task, create_user
from tests.unit.test_social_service import FakeProvider, FakeQueue


@pytest.fixture
async def social_setup(client, db_session, auth_headers, monkeypatch, tmp_path):
    provider = FakeProvider()
    monkeypatch.setattr(social_service, "get_provider", lambda name, config=None: provider)
    monkeypatch.setenv("APP_SETTINGS_ENCRYPTION_KEY", "social-integration-test-key")
    await create_user(db_session, user_id="user-1")
    source = await create_source(db_session)
    task = await create_task(db_session, user_id="user-1", source_id=source["id"])
    clip = await create_clip(db_session, task_id=task["id"])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test clip content")
    await db_session.execute(
        text("UPDATE generated_clips SET file_path = :path WHERE id = :id"),
        {"path": str(media), "id": clip["id"]},
    )
    await db_session.commit()

    started = await client.post("/social/connections/youtube/authorize", headers=auth_headers)
    assert started.status_code == 200
    state = started.json()["state"]
    connected = await client.post(
        "/social/connections/youtube/callback", headers=auth_headers,
        json={"code": "good-code", "state": state},
    )
    assert connected.status_code == 200
    account = connected.json()["connection"]
    assert "access_token_encrypted" not in account
    # Real PostgreSQL state consumption is one-use.
    replay = await client.post(
        "/social/connections/youtube/callback", headers=auth_headers,
        json={"code": "good-code", "state": state},
    )
    assert replay.status_code == 400
    return provider, {
        "task_id": task["id"], "clip_id": clip["id"],
        "social_account_id": account["id"], "privacy_level": "private",
    }


@pytest.mark.asyncio
async def test_schedule_publish_and_metrics_with_real_repository(
    social_setup, client, db_session, auth_headers,
):
    provider, payload = social_setup
    payload["scheduled_for"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    response = await client.post("/social/posts", headers=auth_headers, json=payload)
    assert response.status_code == 200
    post = response.json()["post"]
    assert post["status"] == "scheduled"
    service = SocialService(db_session, queue_adapter=FakeQueue())
    assert await service.dispatch_scheduled_posts() == []
    await db_session.execute(
        text("UPDATE social_posts SET scheduled_for = NOW() - INTERVAL '1 minute' WHERE id = :id"),
        {"id": post["id"]},
    )
    await db_session.commit()
    assert await service.dispatch_scheduled_posts() == [post["id"]]
    assert await service.dispatch_scheduled_posts() == []
    published = await service.publish_post(post["id"])
    assert published["status"] == "published"
    refreshed = await client.post(f"/social/posts/{post['id']}/refresh-metrics", headers=auth_headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["post"]["metrics"]["views"] == 100
    detail = await client.get(f"/social/posts/{post['id']}", headers=auth_headers)
    assert len(detail.json()["post"]["metrics_history"]) == 1
    assert len(provider.publish_calls) == 1


@pytest.mark.asyncio
async def test_pending_private_publish_survives_transient_status_failure(
    social_setup, client, db_session, auth_headers,
):
    provider, payload = social_setup
    response = await client.post("/social/posts", headers=auth_headers, json=payload)
    post = response.json()["post"]
    provider.publish_result = PublishResult(None, None, pending_handle="accepted")
    service = SocialService(db_session, queue_adapter=FakeQueue())
    await service.publish_post(post["id"])

    def unavailable(token, handle):
        raise SocialProviderError("temporarily unavailable", retryable=True)

    provider.resolve_pending = unavailable
    assert await service.resolve_pending_posts() == 1
    provider.resolve_pending = lambda token, handle: PublishResult(None, None)
    assert await service.resolve_pending_posts() == 1
    assert await service.resolve_pending_posts() == 0
    result = await service.get_post("user-1", post["id"])
    assert result["status"] == "published"
    assert result["external_post_id"] is None
    assert result["pending_handle"] is None
    assert result["published_at"] is not None
    assert len(provider.publish_calls) == 1


@pytest.mark.asyncio
async def test_post_ownership_and_media_token_expiration(
    social_setup, client, db_session, auth_headers,
):
    _, payload = social_setup
    response = await client.post("/social/posts", headers=auth_headers, json=payload)
    post = response.json()["post"]
    service = SocialService(db_session, queue_adapter=FakeQueue())
    assert await service.repo.get_user_post(db_session, "another-user", post["id"]) is None
    assert (await client.get(f"/social/posts/{post['id']}")).status_code == 401
    media_url = f"/social/media/{post['media_token']}"
    assert (await client.get(media_url)).status_code == 404
    await service.publish_post(post["id"])
    media = await client.get(media_url)
    assert media.status_code == 200
    assert media.content == b"test clip content"
    await service.repo.update_post(
        db_session, post["id"],
        media_token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert (await client.get(media_url)).status_code == 404


@pytest.mark.asyncio
async def test_publish_claim_is_atomic_with_real_repository(
    social_setup, client, db_session, auth_headers,
):
    provider, payload = social_setup
    response = await client.post("/social/posts", headers=auth_headers, json=payload)
    post = response.json()["post"]
    service = SocialService(db_session, queue_adapter=FakeQueue())
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    claimed = await service.repo.claim_post(db_session, post["id"], media_token_expires_at=expires)
    assert claimed["status"] == "publishing"
    assert claimed["attempts"] == 1
    assert claimed["claimed_at"] is not None
    assert await service.repo.claim_post(
        db_session, post["id"], media_token_expires_at=expires
    ) is None

    # A second job for the same post must not upload.
    skipped = await service.publish_post(post["id"])
    assert skipped["status"] == "publishing"
    assert provider.publish_calls == []
    # Nor can the post be cancelled or deleted underneath the worker.
    assert (await client.post(f"/social/posts/{post['id']}/cancel", headers=auth_headers)).status_code == 400
    assert (await client.delete(f"/social/posts/{post['id']}", headers=auth_headers)).status_code == 400


@pytest.mark.asyncio
async def test_retry_and_cancel_are_conditional_with_real_repository(
    social_setup, client, db_session, auth_headers,
):
    _, payload = social_setup
    payload["scheduled_for"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    post = (await client.post("/social/posts", headers=auth_headers, json=payload)).json()["post"]
    cancel_url = f"/social/posts/{post['id']}/cancel"
    retry_url = f"/social/posts/{post['id']}/retry"

    assert (await client.post(cancel_url, headers=auth_headers)).status_code == 200
    assert (await client.post(cancel_url, headers=auth_headers)).status_code == 400

    service = SocialService(db_session, queue_adapter=FakeQueue())
    assert await service.repo.requeue_post(
        db_session, "user-1", post["id"], media_token="unused-token"
    ) == 0
    # The second click of a double click finds the post already queued.
    assert await service.repo.requeue_post(
        db_session, "user-1", post["id"], media_token="unused-token"
    ) is None
    assert (await client.post(retry_url, headers=auth_headers)).status_code == 400
    detail = (await client.get(f"/social/posts/{post['id']}", headers=auth_headers)).json()["post"]
    assert detail["status"] == "queued"
    assert detail["scheduled_for"] is None
    assert detail["media_token"] == post["media_token"]


@pytest.mark.asyncio
async def test_stale_publishing_sweep_with_real_repository(
    social_setup, client, db_session, auth_headers,
):
    provider, payload = social_setup
    ids = []
    for _ in range(3):
        response = await client.post("/social/posts", headers=auth_headers, json=payload)
        ids.append(response.json()["post"]["id"])
    stale, polling, fresh = ids
    service = SocialService(db_session, queue_adapter=FakeQueue())
    for post_id in ids:
        assert await service.repo.claim_post(db_session, post_id, media_token_expires_at=None)
    await db_session.execute(
        text(
            "UPDATE social_posts SET claimed_at = NOW() - INTERVAL '3 hours' "
            "WHERE id IN (:stale, :polling)"
        ),
        {"stale": stale, "polling": polling},
    )
    await db_session.execute(
        text("UPDATE social_posts SET pending_handle = 'accepted' WHERE id = :id"),
        {"id": polling},
    )
    await db_session.commit()

    assert await service.sweep_stale_publishing() == [stale]
    assert await service.sweep_stale_publishing() == []
    swept = await service.get_post("user-1", stale)
    assert swept["status"] == "failed"
    assert swept["error_message"] == social_service.OUTCOME_UNKNOWN_MESSAGE
    assert (await service.get_post("user-1", polling))["status"] == "publishing"
    assert (await service.get_post("user-1", fresh))["status"] == "publishing"
    assert provider.publish_calls == []
    # Failed posts stay dismissable.
    assert (await client.delete(f"/social/posts/{stale}", headers=auth_headers)).status_code == 200
