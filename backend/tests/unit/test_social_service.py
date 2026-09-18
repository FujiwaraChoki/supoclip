from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from src.config import Config
from src.services import social_service as social_module
from src.services.social_service import (
    SocialNotFound,
    SocialService,
    SocialValidationError,
    _parse_datetime,
    _parse_schedule,
    format_performance_context,
    normalize_hashtags,
)
from src.social.base import (
    AccountProfile,
    OAuthTokens,
    PostMetrics,
    PublishResult,
    SocialProvider,
    SocialProviderError,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------
class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, function_name, *args, **kwargs):
        self.jobs.append((function_name, args, kwargs))
        return f"job-{len(self.jobs)}"


class FakeRepo:
    """In-memory stand-in for SocialRepository (only what the service touches)."""

    def __init__(self):
        self.accounts = {}
        self.posts = {}
        self.states = {}
        self.metrics = []
        self.hook_rows = []
        self.duration_rows = []
        self.top_posts = []
        self.provider_rows = []

    # accounts
    async def list_accounts(self, db, user_id, include_revoked=False):
        return [a for a in self.accounts.values() if a["user_id"] == user_id]

    async def get_account_with_tokens(self, db, account_id):
        return dict(self.accounts[account_id]) if account_id in self.accounts else None

    async def get_user_account(self, db, user_id, account_id):
        account = self.accounts.get(account_id)
        if not account or account["user_id"] != user_id:
            return None
        return {k: v for k, v in account.items() if not k.endswith("_encrypted")}

    async def upsert_account(self, db, **fields):
        account_id = f"acc-{len(self.accounts) + 1}"
        record = {"id": account_id, "status": "active", "revoked_at": None, **fields}
        self.accounts[account_id] = record
        return {k: v for k, v in record.items() if not k.endswith("_encrypted")}

    async def update_account_tokens(self, db, account_id, **fields):
        self.accounts[account_id].update(fields)

    async def set_account_status(self, db, account_id, status, last_error):
        self.accounts[account_id]["status"] = status
        self.accounts[account_id]["last_error"] = last_error

    async def revoke_account(self, db, user_id, account_id):
        account = self.accounts.get(account_id)
        if not account or account["user_id"] != user_id:
            return False
        account["status"] = "revoked"
        account["revoked_at"] = datetime.now(timezone.utc)
        return True

    # oauth state
    async def create_oauth_state(self, db, **fields):
        self.states[fields["state"]] = fields

    async def consume_oauth_state(self, db, state):
        return self.states.pop(state, None)

    # posts
    async def create_post(self, db, **fields):
        post_id = f"post-{len(self.posts) + 1}"
        snapshot = fields.pop("clip_snapshot")
        record = {
            "id": post_id,
            "attempts": 0,
            "pending_handle": None,
            "external_post_id": None,
            "external_url": None,
            "error_message": None,
            "metrics": None,
            "published_at": None,
            "media_token_expires_at": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "hook_type": snapshot.get("hook_type"),
            "hook_title": snapshot.get("hook_title"),
            "clip_duration": snapshot.get("duration"),
            **fields,
        }
        self.posts[post_id] = record
        return dict(record)

    async def get_post(self, db, post_id):
        return dict(self.posts[post_id]) if post_id in self.posts else None

    async def get_user_post(self, db, user_id, post_id):
        post = await self.get_post(db, post_id)
        return post if post and post["user_id"] == user_id else None

    async def update_post(self, db, post_id, **fields):
        self.posts[post_id].update(fields)
        self.posts[post_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

    async def list_metrics_history(self, db, post_id, limit=100):
        return [m for m in self.metrics if m["post_id"] == post_id]

    async def insert_metrics(self, db, post_id, metrics, raw):
        self.metrics.append({"post_id": post_id, **metrics})
        self.posts[post_id]["metrics"] = metrics
        self.posts[post_id]["last_metrics_at"] = datetime.now(timezone.utc).isoformat()

    async def get_post_by_media_token(self, db, token):
        for post in self.posts.values():
            if post.get("media_token") == token:
                return dict(post)
        return None

    # performance
    async def get_performance_by_hook_type(self, db, user_id):
        return self.hook_rows

    async def get_duration_performance(self, db, user_id):
        return self.duration_rows

    async def get_top_posts(self, db, user_id, limit=5):
        return self.top_posts[:limit]

    async def get_performance_by_provider(self, db, user_id):
        return self.provider_rows


class FakeClipRepo:
    def __init__(self, clips):
        self.clips = clips

    async def get_clip_by_id(self, db, clip_id):
        return self.clips.get(clip_id)


class FakeTaskRepo:
    def __init__(self, tasks):
        self.tasks = tasks

    async def get_task_by_id(self, db, task_id):
        return self.tasks.get(task_id)


class FakeProvider(SocialProvider):
    name = "youtube"
    display_name = "Fake YouTube"

    def __init__(self, *_args, **_kwargs):
        super().__init__(session=SimpleNamespace())
        self.publish_calls = []
        self.publish_result = PublishResult(external_post_id="ext-1", external_url=None)
        self.publish_error = None
        self.refresh_calls = 0
        self.metrics = PostMetrics(views=100, likes=5, comments=1, shares=2)

    @property
    def is_configured(self):
        return True

    def build_authorize_url(self, *, state, redirect_uri, code_challenge=None):
        return f"https://auth.example/?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, *, code, redirect_uri, code_verifier=None):
        assert code == "good-code"
        return OAuthTokens(access_token="access", refresh_token="refresh", scopes="s")

    def refresh_tokens(self, refresh_token):
        self.refresh_calls += 1
        return OAuthTokens(
            access_token="refreshed",
            refresh_token=refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def fetch_profile(self, access_token):
        return AccountProfile(external_account_id="chan-1", display_name="Channel", username="chan")

    def publish(self, access_token, request):
        self.publish_calls.append((access_token, request))
        if self.publish_error:
            raise self.publish_error
        return self.publish_result

    def fetch_metrics(self, access_token, external_post_id, account_metadata):
        return self.metrics


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(social_module, "get_provider", lambda name, config=None: fake)
    monkeypatch.setenv("APP_SETTINGS_ENCRYPTION_KEY", "unit-test-encryption-key-123456")
    return fake


@pytest.fixture
def service(provider, tmp_path):
    config = Config()
    config.youtube_oauth_client_id = "id"
    config.youtube_oauth_client_secret = "secret"
    config.social_oauth_redirect_base_url = "https://app.example"
    config.social_public_media_base_url = "https://app.example"
    config.social_performance_min_posts = 3
    config.social_publish_max_attempts = 3

    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"video")

    svc = SocialService(db=None, config=config, queue_adapter=FakeQueue())
    svc.repo = FakeRepo()
    svc.clip_repo = FakeClipRepo(
        {
            "clip-1": {
                "id": "clip-1",
                "task_id": "task-1",
                "file_path": str(clip_path),
                "hook_title": "Why this matters",
                "hook_type": "question",
                "text": "Transcript text",
                "duration": 31.5,
                "virality_score": 80,
                "clip_order": 1,
            }
        }
    )
    svc.task_repo = FakeTaskRepo({"task-1": {"id": "task-1", "user_id": "user-1"}})
    return svc


async def _connect(service):
    start = await service.start_connection("user-1", "youtube")
    return await service.complete_connection(
        "user-1", "youtube", code="good-code", state=start["state"]
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def test_normalize_hashtags_cleans_and_dedupes():
    assert normalize_hashtags(["#Podcast", "podcast", " clips ", "", "a b", "#"]) == [
        "Podcast",
        "clips",
        "ab",
    ]
    assert normalize_hashtags("one, #two three") == ["one", "two", "three"]
    assert normalize_hashtags(None) == []


def test_parse_schedule_accepts_iso_and_rejects_garbage():
    parsed = _parse_schedule("2026-09-01T10:00:00Z")
    assert parsed == datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    assert _parse_datetime("2026-09-01T10:00:00") == datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(SocialValidationError):
        _parse_schedule("tomorrow-ish")


def test_format_performance_context_is_compact_and_grounded():
    text = format_performance_context(
        [
            {"hook_type": "question", "posts": 4, "median_views": 12000, "avg_engagement_rate": 0.041},
            {"hook_type": "story", "posts": 2, "median_views": 3000, "avg_engagement_rate": None},
        ],
        [{"bucket": "20_to_35s", "posts": 5, "avg_views": 9000}],
        [{"hook_title": "The one trick", "hook_type": "question", "provider": "tiktok", "metrics": {"views": 20000}}],
        6,
    )
    assert "Based on 6 published clips" in text
    assert "- question: 4 posts, median 12,000 views, 4.1% engagement" in text
    assert "- story: 2 posts, median 3,000 views" in text
    assert "20 to 35s" in text
    assert '"The one trick" (hook_type=question, tiktok, 20,000 views)' in text
    assert len(text) <= 2000


# ----------------------------------------------------------------------
# Connections
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_connection_handshake_binds_state_to_user(service):
    start = await service.start_connection("user-1", "youtube")
    assert start["authorize_url"].startswith("https://auth.example/")
    assert "redirect_uri=https://app.example/api/social/callback/youtube" in start["authorize_url"]

    # Another user cannot complete this handshake.
    with pytest.raises(SocialValidationError):
        await service.complete_connection("user-2", "youtube", code="good-code", state=start["state"])

    # The state was consumed by the failed attempt; start again for the real user.
    start = await service.start_connection("user-1", "youtube")
    account = await service.complete_connection(
        "user-1", "youtube", code="good-code", state=start["state"]
    )
    assert account["external_account_id"] == "chan-1"
    stored = service.repo.accounts[account["id"]]
    assert stored["access_token_encrypted"].startswith("v1:")
    assert "access" not in stored["access_token_encrypted"]


@pytest.mark.asyncio
async def test_unconfigured_provider_is_rejected(service, provider, monkeypatch):
    monkeypatch.setattr(type(provider), "is_configured", property(lambda self: False))
    with pytest.raises(social_module.SocialProviderUnavailable):
        await service.start_connection("user-1", "youtube")
    with pytest.raises(SocialNotFound):
        await service.start_connection("user-1", "myspace")


# ----------------------------------------------------------------------
# Posts
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_post_queues_immediately_and_snapshots_clip(service):
    account = await _connect(service)
    post = await service.create_post(
        "user-1",
        task_id="task-1",
        clip_id="clip-1",
        social_account_id=account["id"],
        title=None,
        caption=None,
        hashtags=["#one", "two"],
        privacy_level="public",
        scheduled_for=None,
    )
    assert post["status"] == "queued"
    assert post["title"] == "Why this matters"
    assert post["caption"] == "Transcript text"
    assert post["hashtags"] == ["one", "two"]
    assert post["hook_type"] == "question"
    assert post["clip_duration"] == 31.5
    assert post["media_token"]
    assert service.queue_adapter.jobs == [("publish_social_post", (post["id"],), {})]


@pytest.mark.asyncio
async def test_create_post_schedules_future_posts_without_enqueueing(service):
    account = await _connect(service)
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    post = await service.create_post(
        "user-1",
        task_id="task-1",
        clip_id="clip-1",
        social_account_id=account["id"],
        title="t",
        caption="c",
        hashtags=None,
        privacy_level="private",
        scheduled_for=when,
    )
    assert post["status"] == "scheduled"
    assert post["scheduled_for"] is not None
    assert service.queue_adapter.jobs == []

    cancelled = await service.cancel_post("user-1", post["id"])
    assert cancelled["status"] == "cancelled"
    with pytest.raises(SocialValidationError):
        await service.cancel_post("user-1", post["id"])


@pytest.mark.asyncio
async def test_create_post_validates_ownership_and_privacy(service):
    account = await _connect(service)
    with pytest.raises(SocialNotFound):
        await service.create_post(
            "user-2", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
            title=None, caption=None, hashtags=None, privacy_level="public", scheduled_for=None,
        )
    with pytest.raises(SocialValidationError):
        await service.create_post(
            "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
            title=None, caption=None, hashtags=None, privacy_level="secret", scheduled_for=None,
        )


@pytest.mark.asyncio
async def test_publish_post_marks_published_with_fallback_url(service, provider):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=["x"], privacy_level="public", scheduled_for=None,
    )
    published = await service.publish_post(post["id"])
    assert published["status"] == "published"
    assert published["external_post_id"] == "ext-1"
    assert published["external_url"] == "https://www.youtube.com/shorts/ext-1"
    assert published["attempts"] == 1
    access_token, request = provider.publish_calls[0]
    assert access_token == "access"
    assert request.public_media_url == f"https://app.example/api/social/media/{post['media_token']}"
    assert request.hashtags == ["x"]


@pytest.mark.asyncio
async def test_publish_retries_transient_errors_then_fails(service, provider):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="public", scheduled_for=None,
    )
    provider.publish_error = SocialProviderError("quota", retryable=True)

    first = await service.publish_post(post["id"])
    assert first["status"] == "queued"
    assert first["attempts"] == 1
    retry_job = service.queue_adapter.jobs[-1]
    assert retry_job[0] == "publish_social_post"
    assert retry_job[2]["_defer_by"] == timedelta(seconds=300)

    await service.publish_post(post["id"])
    final = await service.publish_post(post["id"])
    assert final["status"] == "failed"
    assert final["attempts"] == 3
    assert "quota" in final["error_message"]


@pytest.mark.asyncio
async def test_publish_reauth_error_flags_account(service, provider):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="public", scheduled_for=None,
    )
    provider.publish_error = SocialProviderError("token revoked", reauth=True, retryable=True)
    failed = await service.publish_post(post["id"])
    assert failed["status"] == "failed"
    assert service.repo.accounts[account["id"]]["status"] == "reauth_required"

    with pytest.raises(SocialValidationError, match="Reconnect"):
        await service.create_post(
            "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
            title="t", caption="c", hashtags=[], privacy_level="public", scheduled_for=None,
        )


@pytest.mark.asyncio
async def test_pending_publish_is_resolved_later(service, provider):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="public", scheduled_for=None,
    )
    provider.publish_result = PublishResult(external_post_id=None, external_url=None, pending_handle="pub-9")
    pending = await service.publish_post(post["id"])
    assert pending["status"] == "publishing"
    assert pending["pending_handle"] == "pub-9"

    provider.resolve_pending = lambda token, handle: PublishResult(external_post_id="done-1", external_url=None)
    resolved = await service.publish_post(post["id"])
    assert resolved["status"] == "published"
    assert resolved["external_post_id"] == "done-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_private_post_completes_without_public_id(service, provider, pending):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="private", scheduled_for=None,
    )
    completed = PublishResult(external_post_id=None, external_url=None)
    provider.publish_result = (
        PublishResult(external_post_id=None, external_url=None, pending_handle="pub-private")
        if pending else completed
    )
    result = await service.publish_post(post["id"])
    if pending:
        provider.resolve_pending = lambda token, handle: completed
        result = await service.publish_post(post["id"])
    assert result["status"] == "published"
    assert result["pending_handle"] is None
    assert result["external_post_id"] is None
    assert result["external_url"] is None
    assert result["published_at"] is not None
    assert len(provider.publish_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    SocialProviderError("temporary status failure", retryable=True),
    requests.Timeout("status request timed out"),
])
async def test_pending_status_failure_never_reuploads(service, provider, error):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="private", scheduled_for=None,
    )
    provider.publish_result = PublishResult(None, None, pending_handle="accepted-upload")
    pending = await service.publish_post(post["id"])

    def fail_poll(token, handle):
        raise error

    provider.resolve_pending = fail_poll
    retrying = await service.publish_post(post["id"])
    assert retrying["status"] == "publishing"
    assert retrying["updated_at"] == pending["updated_at"]
    assert len(service.queue_adapter.jobs) == 1
    provider.resolve_pending = lambda token, handle: PublishResult("finished", None)
    result = await service.publish_post(post["id"])
    assert result["status"] == "published"
    assert result["external_post_id"] == "finished"
    assert len(provider.publish_calls) == 1


@pytest.mark.asyncio
async def test_timed_out_pending_upload_can_be_rechecked_without_reupload(service, provider):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="private", scheduled_for=None,
    )
    provider.publish_result = PublishResult(None, None, pending_handle="accepted-upload")
    await service.publish_post(post["id"])
    service.repo.posts[post["id"]]["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=46)
    ).isoformat()
    failed = await service.publish_post(post["id"])
    assert failed["status"] == "failed"
    retried = await service.retry_post("user-1", post["id"])
    assert retried["pending_handle"] == "accepted-upload"
    provider.resolve_pending = lambda token, handle: PublishResult("finished", None)
    result = await service.publish_post(post["id"])
    assert result["status"] == "published"
    assert len(provider.publish_calls) == 1


@pytest.mark.asyncio
async def test_expiring_token_is_refreshed_before_publishing(service, provider):
    account = await _connect(service)
    service.repo.accounts[account["id"]]["token_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=30)
    ).isoformat()
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="public", scheduled_for=None,
    )
    await service.publish_post(post["id"])
    assert provider.refresh_calls == 1
    assert provider.publish_calls[0][0] == "refreshed"


@pytest.mark.asyncio
async def test_metrics_refresh_and_public_media_token(service, provider):
    account = await _connect(service)
    post = await service.create_post(
        "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
        title="t", caption="c", hashtags=[], privacy_level="public", scheduled_for=None,
    )
    with pytest.raises(SocialValidationError):
        await service.refresh_post_metrics(post, user_id="user-1")

    published = await service.publish_post(post["id"])
    # Token is valid right after publishing (Instagram may still be fetching).
    assert await service.get_public_media_path(post["media_token"]) is not None
    assert await service.get_public_media_path("nope") is None

    refreshed = await service.refresh_post_metrics(published, user_id="user-1")
    assert refreshed["metrics"] == {"views": 100, "likes": 5, "comments": 1, "shares": 2, "saves": None}
    detail = await service.get_post("user-1", post["id"])
    assert len(detail["metrics_history"]) == 1


@pytest.mark.asyncio
async def test_disconnect_cancels_and_blocks_reuse(service):
    account = await _connect(service)
    assert await service.disconnect("user-1", account["id"]) is True
    with pytest.raises(SocialNotFound):
        await service.create_post(
            "user-1", task_id="task-1", clip_id="clip-1", social_account_id=account["id"],
            title="t", caption="c", hashtags=[], privacy_level="public", scheduled_for=None,
        )


# ----------------------------------------------------------------------
# Performance loop
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_performance_context_requires_minimum_posts(service):
    service.repo.hook_rows = [{"hook_type": "question", "posts": 2, "median_views": 100, "avg_engagement_rate": 0.1}]
    assert await service.build_performance_context("user-1") is None
    summary = await service.get_performance_summary("user-1")
    assert summary["personalization_active"] is False
    assert summary["min_posts_for_personalization"] == 3

    service.repo.hook_rows[0]["posts"] = 3
    context = await service.build_performance_context("user-1")
    assert context is not None
    assert "question: 3 posts" in context
    summary = await service.get_performance_summary("user-1")
    assert summary["personalization_active"] is True
