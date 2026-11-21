"""Unit tests for multi-tenant Team workspaces, auth, pooled billing, AI edits, isolation, and email."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from starlette.requests import Request

from src.api.routes import social as social_routes
from src.api.routes import tasks as task_routes
from src.auth_headers import (
    AuthContext,
    get_signed_identity,
    resolve_auth_context,
)
from src.config import Config
from src.repositories.minute_usage_repository import (
    reactivate_minute_usage,
    record_minute_usage,
    refund_failed_minute_usage,
)
from src.services.ai_edit_allowance_service import AIEditAllowanceService
from src.services.billing_service import BillingLimitExceeded, BillingService
from src.services.team_email_service import build_team_invitation_email
from src.workers.video_admission import _concurrency_limit, _slot_owner


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (k.lower().encode("utf-8"), v.encode("utf-8"))
                for k, v in headers.items()
            ],
        }
    )


def _signed_headers(secret: str, user_id: str, team_id: str | None = None) -> dict[str, str]:
    ts = str(int(time.time()))
    payload = f"{user_id}:{team_id}:{ts}" if team_id else f"{user_id}:{ts}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": ts,
        "x-supoclip-signature": sig,
    }
    if team_id:
        headers["x-supoclip-team-id"] = team_id
    return headers


class _SqlSession:
    """Minimal async wrapper around a SQLite connection for SQL tests."""

    def __init__(self, connection):
        self.connection = connection
        self.info = {}

    async def execute(self, statement, parameters=None):
        return self.connection.execute(statement, parameters or {})

    async def commit(self):
        self.connection.commit()

    async def rollback(self):
        self.connection.rollback()


# ---------------------------------------------------------------------------
# 1. Signed Team Header & Membership Verification
# ---------------------------------------------------------------------------


def test_signed_team_header_accepts_valid_and_rejects_tampering():
    config = Config()
    config.backend_auth_secret = "team-secret"

    req = _request(_signed_headers("team-secret", "user-1", "team-alpha"))
    assert get_signed_identity(req, config) == ("user-1", "team-alpha")

    # Tampering with team_id invalidates the HMAC signature.
    tampered = _signed_headers("team-secret", "user-1", "team-alpha")
    tampered["x-supoclip-team-id"] = "team-beta"
    with pytest.raises(HTTPException) as exc:
        get_signed_identity(_request(tampered), config)
    assert exc.value.status_code == 401

    # Attaching an unsigned team header to a user-only signature fails.
    user_only = _signed_headers("team-secret", "user-1")
    user_only["x-supoclip-team-id"] = "team-alpha"
    with pytest.raises(HTTPException) as exc:
        get_signed_identity(_request(user_only), config)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_resolve_auth_context_verifies_active_team_membership(monkeypatch):
    config = Config()
    config.backend_auth_secret = "team-secret"
    req = _request(_signed_headers("team-secret", "user-1", "team-alpha"))

    # Active member succeeds and gets verified role.
    monkeypatch.setattr(
        "src.repositories.team_repository.TeamRepository.get_member_role",
        AsyncMock(return_value="admin"),
    )
    ctx = await resolve_auth_context(req, object(), config)  # type: ignore[arg-type]
    assert ctx == AuthContext(user_id="user-1", team_id="team-alpha", team_role="admin", via_api_key=False)

    # Non-member is always blocked with 403 even with a valid signature.
    monkeypatch.setattr(
        "src.repositories.team_repository.TeamRepository.get_member_role",
        AsyncMock(return_value=None),
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_auth_context(req, object(), config)  # type: ignore[arg-type]
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_auth_context_verifies_team_api_key_membership(monkeypatch):
    config = Config()
    req = _request({"authorization": "Bearer sk_team_test_key"})

    monkeypatch.setattr(
        "src.repositories.api_key_repository.ApiKeyRepository.resolve_key_context",
        AsyncMock(return_value={"user_id": "user-1", "team_id": "team-alpha"}),
    )
    monkeypatch.setattr(
        "src.repositories.team_repository.TeamRepository.get_member_role",
        AsyncMock(return_value="member"),
    )

    ctx = await resolve_auth_context(req, object(), config)  # type: ignore[arg-type]
    assert ctx == AuthContext(user_id="user-1", team_id="team-alpha", team_role="member", via_api_key=True)

    # If the user was removed from the team, their team-scoped API key is rejected.
    monkeypatch.setattr(
        "src.repositories.team_repository.TeamRepository.get_member_role",
        AsyncMock(return_value=None),
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_auth_context(req, object(), config)  # type: ignore[arg-type]
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 2. Pooled Team Billing & Minute Ledger Isolation / Refunding
# ---------------------------------------------------------------------------


def test_team_minute_limit_scales_with_additional_seats():
    config = Config()
    config.team_included_seats = 3
    config.team_plan_minute_limit = 3600
    config.team_extra_seat_minute_limit = 900

    assert config.team_minute_limit_for_seats(1) == 3600
    assert config.team_minute_limit_for_seats(3) == 3600
    assert config.team_minute_limit_for_seats(5) == 5400
    assert config.max_youtube_video_duration_for_plan("team", "active") == 10800


@pytest.mark.asyncio
async def test_team_pooled_billing_allowance_isolation_and_refund(monkeypatch):
    period_start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 12, 1, tzinfo=timezone.utc)
    created_at = period_start + timedelta(days=2)

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.connection.create_function("NOW", 0, lambda: created_at.isoformat(" "))
        conn.execute(text("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, user_id TEXT, team_id TEXT, status TEXT
            )
        """))
        conn.execute(text("CREATE TABLE generated_clips (id TEXT PRIMARY KEY, task_id TEXT)"))
        conn.execute(text("""
            CREATE TABLE team_members (
                team_id TEXT, user_id TEXT, role TEXT,
                monthly_minute_limit INTEGER, monthly_ai_edit_limit INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE minute_usage_events (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                team_id TEXT,
                task_id TEXT UNIQUE,
                minutes INTEGER NOT NULL,
                source_duration_seconds INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                refunded_at TIMESTAMP
            )
        """))

        db = _SqlSession(conn)
        config = Config()
        config.self_host = False
        config.monetization_enabled = True
        config.pro_plan_minute_limit = 600
        config.team_included_seats = 3
        config.team_plan_minute_limit = 3600
        config.team_extra_seat_minute_limit = 900

        service = BillingService(db, config)  # type: ignore[arg-type]
        monkeypatch.setattr(
            service,
            "_load_team_billing_row",
            AsyncMock(
                return_value={
                    "name": "Clip Crew",
                    "plan": "team",
                    "subscription_status": "active",
                    "subscription_provider": "stripe",
                    "seat_limit": 5,  # 3 base (3600) + 2 extra (1800) = 5400 min
                    "billing_period_start": period_start,
                    "billing_period_end": period_end,
                    "subscription_cancel_at": None,
                }
            ),
        )
        monkeypatch.setattr(
            service,
            "_load_user_billing_row",
            AsyncMock(
                return_value={
                    "plan": "pro",
                    "subscription_status": "active",
                    "subscription_provider": "stripe",
                    "billing_period_start": period_start,
                    "billing_period_end": period_end,
                    "trial_ends_at": None,
                    "subscription_cancel_at": None,
                    "legacy_hd": False,
                }
            ),
        )

        # Two different teammates create tasks inside team-alpha (120 min + 60 min = 180 min)
        # while user-1 also creates a 45-min personal task.
        conn.execute(text("INSERT INTO tasks VALUES ('t-team-1', 'user-1', 'team-alpha', 'completed')"))
        conn.execute(text("INSERT INTO tasks VALUES ('t-team-2', 'user-2', 'team-alpha', 'error')"))
        conn.execute(text("INSERT INTO tasks VALUES ('t-personal', 'user-1', NULL, 'completed')"))

        await record_minute_usage(db, "user-1", "t-team-1", 7200, team_id="team-alpha")
        await record_minute_usage(db, "user-2", "t-team-2", 3600, team_id="team-alpha")
        await record_minute_usage(db, "user-1", "t-personal", 2700)
        conn.execute(text("UPDATE minute_usage_events SET created_at = :ts"), {"ts": created_at})

        # Team pool counts only team-alpha events (180 min of 5400 min), 1080p, scheduling enabled.
        team_summary = await service.get_usage_summary("user-1", team_id="team-alpha")
        assert team_summary["workspace_type"] == "team"
        assert team_summary["team_id"] == "team-alpha"
        assert team_summary["team_name"] == "Clip Crew"
        assert team_summary["seat_limit"] == 5
        assert team_summary["minute_limit"] == 5400
        assert team_summary["usage_minutes"] == 180
        assert team_summary["remaining_minutes"] == 5220
        assert team_summary["output_short_side"] == 1080
        assert team_summary["can_schedule_posts"] is True

        # Personal plan counts only personal events (45 min of 600 min) — never team usage.
        personal_summary = await service.get_usage_summary("user-1")
        assert personal_summary["workspace_type"] == "personal"
        assert personal_summary["usage_minutes"] == 45
        assert personal_summary["minute_limit"] == 600

        # Failing team task with no clips refunds its 60 minutes back to the team pool.
        await refund_failed_minute_usage(db, "t-team-2")
        after_refund = await service.get_usage_summary("user-2", team_id="team-alpha")
        assert after_refund["usage_minutes"] == 120
        assert after_refund["remaining_minutes"] == 5280

        # Retrying reactivates the refunded team charge.
        await reactivate_minute_usage(db, "user-2", "t-team-2", team_id="team-alpha")
        conn.execute(text("UPDATE minute_usage_events SET created_at = :ts"), {"ts": created_at})
        after_retry = await service.get_usage_summary("user-2", team_id="team-alpha")
        assert after_retry["usage_minutes"] == 180

        # Requesting more minutes than remaining raises BillingLimitExceeded.
        with pytest.raises(BillingLimitExceeded):
            await service.assert_can_create_task("user-1", 5221 * 60, team_id="team-alpha")
    engine.dispose()


# ---------------------------------------------------------------------------
# 3. Pooled AI Edit Allowance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_ai_edit_allowance_uses_team_pool(monkeypatch):
    period_start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 12, 1, tzinfo=timezone.utc)

    class _FakeDb:
        def __init__(self, used: int):
            self.used = used

        async def execute(self, *_args, **_kwargs):
            # fetchone: the member has no row of limits (None = no personal cap).
            return SimpleNamespace(
                scalar_one=lambda: self.used, scalar=lambda: self.used, fetchone=lambda: None
            )

    config = Config()
    config.self_host = False
    config.monetization_enabled = True
    config.team_plan_ai_edit_limit = 300

    service = AIEditAllowanceService(_FakeDb(42), config)  # type: ignore[arg-type]
    monkeypatch.setattr(
        "src.services.billing_service.BillingService._load_team_billing_row",
        AsyncMock(
            return_value={
                "plan": "team",
                "subscription_status": "active",
                "subscription_provider": "stripe",
                "billing_period_start": period_start,
                "billing_period_end": period_end,
            }
        ),
    )

    summary = await service.summary("user-1", team_id="team-alpha")
    assert summary["plan"] == "team"
    assert summary["limit"] == 300
    assert summary["used"] == 42
    assert summary["remaining"] == 258
    assert summary["allowed"] is True


# ---------------------------------------------------------------------------
# 4. Cross-Team Tenant Isolation (Tasks & Social Roles)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_tenant_isolation_blocks_outsiders_and_cross_team_keys(monkeypatch):
    team_task = {"id": "t-1", "user_id": "owner-1", "team_id": "team-alpha", "status": "completed"}
    personal_task = {"id": "t-2", "user_id": "owner-1", "team_id": None, "status": "completed"}

    # Active teammate can access team task.
    monkeypatch.setattr(
        "src.api.routes.tasks.TeamRepository.is_member",
        AsyncMock(return_value=True),
    )
    teammate_ctx = AuthContext(user_id="editor-2", team_id="team-alpha", team_role="member")
    assert await task_routes._can_access_task(teammate_ctx, object(), team_task) is True  # type: ignore[arg-type]

    # Non-member cannot access team task.
    monkeypatch.setattr(
        "src.api.routes.tasks.TeamRepository.is_member",
        AsyncMock(return_value=False),
    )
    outsider_ctx = AuthContext(user_id="outsider", team_id=None)
    assert await task_routes._can_access_task(outsider_ctx, object(), team_task) is False  # type: ignore[arg-type]

    # Team-scoped API key cannot access another team's task OR personal tasks.
    other_team_key = AuthContext(
        user_id="owner-1", team_id="team-beta", team_role="owner", via_api_key=True
    )
    assert await task_routes._can_access_task(other_team_key, object(), team_task) is False  # type: ignore[arg-type]
    assert await task_routes._can_access_task(other_team_key, object(), personal_task) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_social_connection_management_blocks_team_members(monkeypatch):
    monkeypatch.setattr(
        "src.api.routes.social._service",
        lambda _req, _db: SimpleNamespace(has_access=AsyncMock(return_value=True)),
    )

    member_ctx = AuthContext(user_id="u-1", team_id="team-alpha", team_role="member")
    monkeypatch.setattr("src.api.routes.social._auth_context", AsyncMock(return_value=member_ctx))
    with pytest.raises(HTTPException) as exc:
        await social_routes._allowed_context(object(), object(), require_team_manager=True)  # type: ignore[arg-type]
    assert exc.value.status_code == 403

    # Owners, admins, and personal workspaces pass.
    for ctx in (
        AuthContext(user_id="u-2", team_id="team-alpha", team_role="admin"),
        AuthContext(user_id="u-3", team_id="team-alpha", team_role="owner"),
        AuthContext(user_id="u-4", team_id=None),
    ):
        monkeypatch.setattr("src.api.routes.social._auth_context", AsyncMock(return_value=ctx))
        resolved = await social_routes._allowed_context(object(), object(), require_team_manager=True)  # type: ignore[arg-type]
        assert resolved == ctx


# ---------------------------------------------------------------------------
# 5. Team Concurrency Slot & Invite Email Formatting
# ---------------------------------------------------------------------------


def test_video_admission_uses_team_concurrency_bucket():
    config = Config()
    config.video_job_user_concurrency = 2
    config.video_job_team_concurrency = 4

    assert _slot_owner("user-1", None) == "user-1"
    assert _concurrency_limit(config, None) == 2

    assert _slot_owner("user-1", "team-alpha") == "team:team-alpha"
    assert _concurrency_limit(config, "team-alpha") == 4


def test_team_email_service_builds_invitation_email():
    content = build_team_invitation_email(
        team_name="Demo Clip Crew",
        inviter_name="Alex",
        role="admin",
        invite_url="https://supoclip.example.com/invite/sample-token",
    )
    assert "Demo Clip Crew" in content.subject
    assert "Alex" in content.html
    assert "an admin" in content.html
    assert "https://supoclip.example.com/invite/sample-token" in content.html
    assert "https://supoclip.example.com/invite/sample-token" in content.text


# ---------------------------------------------------------------------------
# 6. Per-Member Generation Limits (Video Minutes & AI Edits)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_per_member_minute_limits_enforced_and_refunded(monkeypatch):
    period_start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 12, 1, tzinfo=timezone.utc)
    created_at = period_start + timedelta(days=3)

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.connection.create_function("NOW", 0, lambda: created_at.isoformat(" "))
        conn.execute(text("CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id TEXT, team_id TEXT, status TEXT)"))
        conn.execute(text("CREATE TABLE generated_clips (id TEXT PRIMARY KEY, task_id TEXT)"))
        conn.execute(text("""
            CREATE TABLE team_members (
                team_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                monthly_minute_limit INTEGER,
                monthly_ai_edit_limit INTEGER,
                PRIMARY KEY (team_id, user_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE minute_usage_events (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                team_id TEXT,
                task_id TEXT UNIQUE,
                minutes INTEGER NOT NULL,
                source_duration_seconds INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                refunded_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE ai_edit_usage_events (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                team_id TEXT,
                created_at TIMESTAMP NOT NULL,
                refunded_at TIMESTAMP
            )
        """))
        conn.execute(text("INSERT INTO team_members VALUES ('team-alpha', 'user-owner', 'owner', 10, 1)"))
        conn.execute(text("INSERT INTO team_members VALUES ('team-alpha', 'user-editor', 'member', 100, 2)"))
        conn.execute(text("INSERT INTO team_members VALUES ('team-alpha', 'user-paused', 'member', 0, 0)"))

        db = _SqlSession(conn)
        config = Config()
        config.self_host = False
        config.monetization_enabled = True
        config.team_included_seats = 3
        config.team_plan_minute_limit = 3600
        config.team_extra_seat_minute_limit = 900
        config.team_plan_ai_edit_limit = 300

        service = BillingService(db, config)  # type: ignore[arg-type]
        team_row = {
            "name": "Clip Crew",
            "plan": "team",
            "subscription_status": "active",
            "subscription_provider": "stripe",
            "seat_limit": 3,
            "billing_period_start": period_start,
            "billing_period_end": period_end,
            "subscription_cancel_at": None,
        }
        monkeypatch.setattr(service, "_load_team_billing_row", AsyncMock(return_value=team_row))

        # Owner is never capped even if a limit column were somehow populated.
        owner_summary = await service.get_usage_summary("user-owner", team_id="team-alpha")
        assert owner_summary["member_minute_limit"] is None
        assert owner_summary["can_create_task"] is True

        # Editor uses 70 of their 100-minute personal cap while team pool has 3530 min left.
        conn.execute(text("INSERT INTO tasks VALUES ('t-ed-1', 'user-editor', 'team-alpha', 'error')"))
        await record_minute_usage(db, "user-editor", "t-ed-1", 70 * 60, team_id="team-alpha")
        conn.execute(text("UPDATE minute_usage_events SET created_at = :ts"), {"ts": created_at})

        editor_summary = await service.get_usage_summary("user-editor", team_id="team-alpha")
        assert editor_summary["member_minute_limit"] == 100
        assert editor_summary["member_usage_minutes"] == 70
        assert editor_summary["member_remaining_minutes"] == 30
        assert editor_summary["can_create_task"] is True

        # 30-min video fits; 35-min video is blocked by member limit (NOT upgrade paywall).
        await service.assert_can_create_task("user-editor", 30 * 60, team_id="team-alpha")
        with pytest.raises(BillingLimitExceeded) as exc_info:
            await service.assert_can_create_task("user-editor", 35 * 60, team_id="team-alpha")
        assert exc_info.value.summary["member_limit_reached"] is True
        assert exc_info.value.summary["upgrade_required"] is False
        assert "monthly team limit" in (exc_info.value.summary["reason"] or "")

        # Paused seat (0 min limit) is immediately blocked without upgrade prompt.
        paused_summary = await service.get_usage_summary("user-paused", team_id="team-alpha")
        assert paused_summary["can_create_task"] is False
        assert paused_summary["member_limit_reached"] is True
        assert paused_summary["upgrade_required"] is False
        assert "paused" in (paused_summary["reason"] or "").lower()

        # Refunding failed task restores editor's personal allowance.
        await refund_failed_minute_usage(db, "t-ed-1")
        await service.assert_can_create_task("user-editor", 90 * 60, team_id="team-alpha")

        # Per-member AI edit limits are enforced alongside team pool.
        ai_service = AIEditAllowanceService(db, config)  # type: ignore[arg-type]
        monkeypatch.setattr(
            "src.services.billing_service.BillingService._load_team_billing_row",
            AsyncMock(return_value=team_row),
        )
        conn.execute(
            text("INSERT INTO ai_edit_usage_events VALUES ('job-1', 'user-editor', 'team-alpha', :ts, NULL)"),
            {"ts": created_at},
        )
        conn.execute(
            text("INSERT INTO ai_edit_usage_events VALUES ('job-2', 'user-editor', 'team-alpha', :ts, NULL)"),
            {"ts": created_at},
        )
        ai_editor = await ai_service.summary("user-editor", team_id="team-alpha")
        assert ai_editor["used"] == 2
        assert ai_editor["limit"] == 300
        assert ai_editor["memberLimit"] == 2
        assert ai_editor["memberUsed"] == 2
        assert ai_editor["memberRemaining"] == 0
        assert ai_editor["memberLimitReached"] is True
        assert ai_editor["allowed"] is False

        # Refunding one AI edit re-allows the member.
        conn.execute(text("UPDATE ai_edit_usage_events SET refunded_at = :ts WHERE job_id = 'job-2'"), {"ts": created_at})
        ai_editor_after = await ai_service.summary("user-editor", team_id="team-alpha")
        assert ai_editor_after["memberUsed"] == 1
        assert ai_editor_after["memberRemaining"] == 1
        assert ai_editor_after["allowed"] is True

        # Per-member usage aggregation map returns accurate counts.
        usage_map = await service.get_team_member_usage_map("team-alpha")
        assert usage_map["user-editor"]["ai_edits_used"] == 1
    engine.dispose()
