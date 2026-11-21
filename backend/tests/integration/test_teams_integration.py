"""End-to-end backend integration tests for Team workspaces against real PostgreSQL & Redis."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import time
import uuid

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

from src.config import get_config
from src.repositories.minute_usage_repository import (
    reactivate_minute_usage,
    record_minute_usage,
    refund_failed_minute_usage,
)
from src.services.ai_edit_allowance_service import (
    AIEditAllowanceService,
    AIEditLimitExceeded,
    refund_ai_edit_usage,
)
from src.services.billing_service import BillingService
from src.workers.video_admission import (
    _concurrency_limit,
    _slot_owner,
    acquire_video_slot,
    release_video_slot,
)
from tests.fixtures.factories import create_clip, create_source, create_task, create_user


def _sign(user_id: str, team_id: str | None = None, *, secret: str = "test-backend-auth-secret") -> dict[str, str]:
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


async def _seed_team(
    db_session,
    *,
    team_id: str,
    name: str,
    owner_id: str,
    plan: str = "team",
    status: str = "active",
    seat_limit: int = 5,
    members: list[tuple[str, str]] | None = None,
):
    now = datetime.now(timezone.utc)
    await db_session.execute(
        text(
            """
            INSERT INTO teams (
                id, name, slug, owner_id, plan, subscription_status, subscription_provider,
                seat_limit, billing_period_start, billing_period_end, created_at, updated_at
            ) VALUES (
                :id, :name, :slug, :owner_id, :plan, :status, 'stripe',
                :seat_limit, :start, :end, NOW(), NOW()
            )
            """
        ),
        {
            "id": team_id,
            "name": name,
            "slug": f"{team_id}-{uuid.uuid4().hex[:6]}",
            "owner_id": owner_id,
            "plan": plan,
            "status": status,
            "seat_limit": seat_limit,
            "start": now - timedelta(days=2),
            "end": now + timedelta(days=28),
        },
    )
    rows = members or [(owner_id, "owner")]
    for uid, role in rows:
        await db_session.execute(
            text(
                """
                INSERT INTO team_members (team_id, user_id, role, created_at)
                VALUES (:team_id, :user_id, :role, NOW())
                """
            ),
            {"team_id": team_id, "user_id": uid, "role": role},
        )
    await db_session.commit()


@pytest.mark.asyncio
async def test_team_task_library_and_cross_tenant_isolation_http(client, db_session):
    """Tasks inside Team A are shared among Team A members, invisible to personal workspace & Team B."""
    uid_owner = f"u-owner-{uuid.uuid4().hex[:8]}"
    uid_editor = f"u-editor-{uuid.uuid4().hex[:8]}"
    uid_outsider = f"u-outsider-{uuid.uuid4().hex[:8]}"
    team_a = f"team-a-{uuid.uuid4().hex[:8]}"
    team_b = f"team-b-{uuid.uuid4().hex[:8]}"

    await create_user(db_session, user_id=uid_owner, email=f"{uid_owner}@example.com", name="Owner A")
    await create_user(db_session, user_id=uid_editor, email=f"{uid_editor}@example.com", name="Editor A")
    await create_user(db_session, user_id=uid_outsider, email=f"{uid_outsider}@example.com", name="Outsider B")

    await _seed_team(
        db_session,
        team_id=team_a,
        name="Alpha Crew",
        owner_id=uid_owner,
        members=[(uid_owner, "owner"), (uid_editor, "member")],
    )
    await _seed_team(
        db_session,
        team_id=team_b,
        name="Beta Crew",
        owner_id=uid_outsider,
        members=[(uid_outsider, "owner")],
    )

    src_team = await create_source(db_session, title="Team A Video")
    src_personal = await create_source(db_session, title="Owner Personal Video")

    team_task = await create_task(db_session, user_id=uid_owner, source_id=src_team["id"], status="completed")
    await db_session.execute(
        text("UPDATE tasks SET team_id = :team_id WHERE id = :task_id"),
        {"team_id": team_a, "task_id": team_task["id"]},
    )
    personal_task = await create_task(db_session, user_id=uid_owner, source_id=src_personal["id"], status="completed")
    await db_session.commit()

    # 1. Teammate in Team A sees Team A's task in list & detail (including created_by_name), NOT owner's personal task.
    list_a = await client.get("/tasks/", headers=_sign(uid_editor, team_a))
    assert list_a.status_code == 200
    tasks_a = list_a.json()["tasks"]
    ids_a = {t["id"] for t in tasks_a}
    assert team_task["id"] in ids_a
    assert personal_task["id"] not in ids_a

    detail_a = await client.get(f"/tasks/{team_task['id']}", headers=_sign(uid_editor, team_a))
    assert detail_a.status_code == 200
    assert detail_a.json()["team_id"] == team_a

    # 2. Owner in Personal workspace sees only their personal task, NOT Team A's task in list.
    list_personal = await client.get("/tasks/", headers=_sign(uid_owner))
    assert list_personal.status_code == 200
    ids_personal = {t["id"] for t in list_personal.json()["tasks"]}
    assert personal_task["id"] in ids_personal
    assert team_task["id"] not in ids_personal

    # 3. Outsider cannot access Team A's task directly or forge Team A header.
    outsider_detail = await client.get(f"/tasks/{team_task['id']}", headers=_sign(uid_outsider, team_b))
    assert outsider_detail.status_code == 403

    forged_team = await client.get("/tasks/", headers=_sign(uid_outsider, team_a))
    assert forged_team.status_code == 403

    # 4. Tampering with signed x-supoclip-team-id header is rejected with 401.
    tampered = _sign(uid_editor, team_a)
    tampered["x-supoclip-team-id"] = team_b
    tampered_res = await client.get("/tasks/", headers=tampered)
    assert tampered_res.status_code == 401

    # 5. Removing editor from team immediately revokes access to Team A tasks.
    await db_session.execute(
        text("DELETE FROM team_members WHERE team_id = :team_id AND user_id = :user_id"),
        {"team_id": team_a, "user_id": uid_editor},
    )
    await db_session.commit()
    revoked_res = await client.get(f"/tasks/{team_task['id']}", headers=_sign(uid_editor, team_a))
    assert revoked_res.status_code == 403


@pytest.mark.asyncio
async def test_team_api_keys_scoping_roles_and_isolation(client, db_session):
    """Team API keys require owner/admin to manage, are isolated to their team, and break on removal."""
    uid_owner = f"u-ko-{uuid.uuid4().hex[:8]}"
    uid_member = f"u-km-{uuid.uuid4().hex[:8]}"
    team_a = f"team-ka-{uuid.uuid4().hex[:8]}"

    await create_user(db_session, user_id=uid_owner, email=f"{uid_owner}@example.com", name="Owner")
    await create_user(db_session, user_id=uid_member, email=f"{uid_member}@example.com", name="Member")
    await db_session.execute(
        text('UPDATE users SET "emailVerified" = TRUE WHERE id IN (:u1, :u2)'),
        {"u1": uid_owner, "u2": uid_member},
    )
    await _seed_team(
        db_session,
        team_id=team_a,
        name="Key Crew",
        owner_id=uid_owner,
        members=[(uid_owner, "owner"), (uid_member, "member")],
    )

    # Member cannot mint a team API key.
    member_create = await client.post(
        "/api-keys/", json={"name": "Member Key"}, headers=_sign(uid_member, team_a)
    )
    assert member_create.status_code == 403

    # Owner mints a team-scoped API key.
    owner_create = await client.post(
        "/api-keys/", json={"name": "CI Team Key"}, headers=_sign(uid_owner, team_a)
    )
    assert owner_create.status_code == 200
    raw_team_key = owner_create.json()["api_key"]["key"]
    key_id = owner_create.json()["api_key"]["id"]

    # Seed one team task and one personal task for the same owner.
    src_1 = await create_source(db_session, title="Team Key Task")
    src_2 = await create_source(db_session, title="Personal Key Task")
    team_task = await create_task(db_session, user_id=uid_owner, source_id=src_1["id"], status="completed")
    await db_session.execute(
        text("UPDATE tasks SET team_id = :t WHERE id = :id"),
        {"t": team_a, "id": team_task["id"]},
    )
    personal_task = await create_task(db_session, user_id=uid_owner, source_id=src_2["id"], status="completed")
    await db_session.commit()

    # Team API key can read team task, CANNOT read owner's personal task!
    key_headers = {"Authorization": f"Bearer {raw_team_key}"}
    assert (await client.get(f"/tasks/{team_task['id']}", headers=key_headers)).status_code == 200
    assert (await client.get(f"/tasks/{personal_task['id']}", headers=key_headers)).status_code == 403

    # Member cannot revoke the team API key; owner can.
    assert (await client.delete(f"/api-keys/{key_id}", headers=_sign(uid_member, team_a))).status_code == 403
    assert (await client.delete(f"/api-keys/{key_id}", headers=_sign(uid_owner, team_a))).status_code == 200
    assert (await client.get(f"/tasks/{team_task['id']}", headers=key_headers)).status_code == 401


@pytest.mark.asyncio
async def test_team_pooled_billing_ai_edits_and_usage_endpoint_real_postgres(client, db_session, app):
    """Pooled minutes (with extra-seat capacity), refunds, AI edits, and /teams/{id}/usage on real Postgres."""
    uid_owner = f"u-bo-{uuid.uuid4().hex[:8]}"
    uid_member = f"u-bm-{uuid.uuid4().hex[:8]}"
    team_id = f"team-bill-{uuid.uuid4().hex[:8]}"

    await create_user(db_session, user_id=uid_owner, email=f"{uid_owner}@example.com", name="Owner")
    await create_user(db_session, user_id=uid_member, email=f"{uid_member}@example.com", name="Member")
    await _seed_team(
        db_session,
        team_id=team_id,
        name="Billing Crew",
        owner_id=uid_owner,
        plan="team",
        status="active",
        seat_limit=5,  # 3600 + 2*900 = 5400 min
        members=[(uid_owner, "owner"), (uid_member, "member")],
    )

    config = get_config()
    config.self_host = False
    config.monetization_enabled = True
    config.team_included_seats = 3
    config.team_plan_minute_limit = 3600
    config.team_extra_seat_minute_limit = 900
    config.team_plan_ai_edit_limit = 3  # Low limit for testing exhaustion

    src = await create_source(db_session, title="Long Stream")
    t1 = await create_task(db_session, user_id=uid_owner, source_id=src["id"], status="completed")
    t2 = await create_task(db_session, user_id=uid_member, source_id=src["id"], status="error")
    t3 = await create_task(db_session, user_id=uid_member, source_id=src["id"], status="error")
    for tid in (t1["id"], t2["id"], t3["id"]):
        await db_session.execute(text("UPDATE tasks SET team_id = :team WHERE id = :id"), {"team": team_id, "id": tid})
    # t3 produced a clip before failing, so it must NOT be refunded; t2 has no clips so it MUST refund.
    await create_clip(db_session, task_id=t3["id"])

    await record_minute_usage(db_session, uid_owner, t1["id"], 6000, team_id=team_id)   # 100 min
    await record_minute_usage(db_session, uid_member, t2["id"], 3000, team_id=team_id)  # 50 min
    await record_minute_usage(db_session, uid_member, t3["id"], 1800, team_id=team_id)  # 30 min
    await refund_failed_minute_usage(db_session, t2["id"])
    await refund_failed_minute_usage(db_session, t3["id"])
    await db_session.commit()

    # Check GET /teams/{team_id}/usage HTTP route
    usage_resp = await client.get(f"/teams/{team_id}/usage", headers=_sign(uid_member, team_id))
    assert usage_resp.status_code == 200
    billing = usage_resp.json()["billing"]
    assert billing["workspace_type"] == "team"
    assert billing["minute_limit"] == 5400
    assert billing["usage_minutes"] == 130  # 100 + 30 (50 refunded)
    assert billing["remaining_minutes"] == 5270
    assert billing["output_short_side"] == 1080
    assert billing["can_schedule_posts"] is True

    # Reactivating t2 restores the 50 min charge
    await reactivate_minute_usage(db_session, uid_member, t2["id"], team_id=team_id)
    await db_session.commit()
    summary = await BillingService(db_session, config).get_usage_summary(uid_owner, team_id=team_id)
    assert summary["usage_minutes"] == 180

    # Pooled AI edit allowance across teammates (limit = 3)
    ai_service = AIEditAllowanceService(db_session, config)
    j1, j2, j3, j4 = [f"job-{uuid.uuid4().hex[:8]}" for _ in range(4)]
    await ai_service.reserve(uid_owner, j1, team_id=team_id)
    await ai_service.reserve(uid_member, j2, team_id=team_id)
    await ai_service.reserve(uid_member, j3, team_id=team_id)
    await db_session.commit()

    with pytest.raises(AIEditLimitExceeded):
        await ai_service.reserve(uid_owner, j4, team_id=team_id)

    # Refunding one failed AI edit re-opens a slot in the team pool
    await refund_ai_edit_usage(j3)
    reserved = await ai_service.reserve(uid_owner, j4, team_id=team_id)
    assert reserved["used"] == 3
    assert reserved["remaining"] == 0

    # Non-manager member cannot trigger /teams/invite-email
    invite_denied = await client.post(
        "/teams/invite-email",
        json={
            "team_id": team_id,
            "recipient_email": "new@example.com",
            "team_name": "Billing Crew",
            "role": "member",
            "invite_url": "https://supoclip.example.com/invite/tok",
        },
        headers=_sign(uid_member, team_id),
    )
    assert invite_denied.status_code == 403


@pytest.mark.asyncio
async def test_team_redis_video_concurrency_slots_real_redis():
    """Real Redis Lua slot admission uses team:{team_id} bucket with higher team concurrency cap."""
    config = get_config()
    config.video_job_user_concurrency = 1
    config.video_job_team_concurrency = 3

    redis_client = aioredis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)
    team_id = f"team-redis-{uuid.uuid4().hex[:8]}"
    user_id = f"u-redis-{uuid.uuid4().hex[:8]}"
    limit = _concurrency_limit(config, team_id)
    assert limit == 3
    assert _slot_owner(user_id, team_id) == f"team:{team_id}"

    try:
        assert await acquire_video_slot(redis_client, user_id, "task-1", limit, team_id=team_id) is True
        assert await acquire_video_slot(redis_client, user_id, "task-2", limit, team_id=team_id) is True
        assert await acquire_video_slot(redis_client, user_id, "task-3", limit, team_id=team_id) is True
        # 4th concurrent job in same team is deferred
        assert await acquire_video_slot(redis_client, user_id, "task-4", limit, team_id=team_id) is False
        # Personal slot for the same user is completely independent
        assert await acquire_video_slot(redis_client, user_id, "personal-1", 1, team_id=None) is True
        # Releasing one team slot lets task-4 enter
        await release_video_slot(redis_client, user_id, "task-2", team_id=team_id)
        assert await acquire_video_slot(redis_client, user_id, "task-4", limit, team_id=team_id) is True
    finally:
        await redis_client.delete(f"user_video_active:team:{team_id}", f"user_video_active:{user_id}")
        await redis_client.aclose()


@pytest.mark.asyncio
async def test_deleting_team_work_follows_team_membership(client, db_session):
    """Delete goes through the same workspace check as every other change to a task."""
    uid_owner = f"u-do-{uuid.uuid4().hex[:8]}"
    uid_editor = f"u-de-{uuid.uuid4().hex[:8]}"
    team_a = f"team-da-{uuid.uuid4().hex[:8]}"
    for uid in (uid_owner, uid_editor):
        await create_user(db_session, user_id=uid, email=f"{uid}@example.com", name=uid)
    await db_session.execute(
        text('UPDATE users SET "emailVerified" = TRUE WHERE id = :u'), {"u": uid_owner}
    )
    await _seed_team(
        db_session,
        team_id=team_a,
        name="Delete Crew",
        owner_id=uid_owner,
        members=[(uid_owner, "owner"), (uid_editor, "member")],
    )

    async def team_task(creator: str) -> str:
        source = await create_source(db_session, title="Team video")
        task = await create_task(db_session, user_id=creator, source_id=source["id"], status="completed")
        await db_session.execute(
            text("UPDATE tasks SET team_id = :t WHERE id = :id"), {"t": team_a, "id": task["id"]}
        )
        await db_session.commit()
        return task["id"]

    editors_first = await team_task(uid_editor)
    editors_second = await team_task(uid_editor)
    source = await create_source(db_session, title="Owner personal video")
    owners_personal = (
        await create_task(db_session, user_id=uid_owner, source_id=source["id"], status="completed")
    )["id"]
    await db_session.commit()

    # A team key reaches its team only, even for the tasks of the person who made the key.
    created = await client.post("/api-keys/", json={"name": "Team key"}, headers=_sign(uid_owner, team_a))
    team_key = {"Authorization": f"Bearer {created.json()['api_key']['key']}"}
    assert (await client.delete(f"/tasks/{owners_personal}", headers=team_key)).status_code == 403

    # The owner can delete a teammate's task in the team.
    assert (await client.delete(f"/tasks/{editors_first}", headers=_sign(uid_owner, team_a))).status_code == 200

    # Once removed from the team, its creator can no longer delete what they made there.
    await db_session.execute(
        text("DELETE FROM team_members WHERE team_id = :t AND user_id = :u"),
        {"t": team_a, "u": uid_editor},
    )
    await db_session.commit()
    assert (await client.delete(f"/tasks/{editors_second}", headers=_sign(uid_editor))).status_code == 403
    remaining = await db_session.execute(
        text("SELECT id FROM tasks WHERE id IN (:a, :b, :c)"),
        {"a": editors_first, "b": editors_second, "c": owners_personal},
    )
    assert {row.id for row in remaining} == {editors_second, owners_personal}


@pytest.mark.asyncio
async def test_team_key_stops_everywhere_when_its_creator_leaves(client, db_session):
    """Routes that only need the user (uploads, fonts, workflows) refuse a dead team key too."""
    uid_owner = f"u-lo-{uuid.uuid4().hex[:8]}"
    uid_admin = f"u-la-{uuid.uuid4().hex[:8]}"
    team_a = f"team-la-{uuid.uuid4().hex[:8]}"
    for uid in (uid_owner, uid_admin):
        await create_user(db_session, user_id=uid, email=f"{uid}@example.com", name=uid)
    await db_session.execute(
        text('UPDATE users SET "emailVerified" = TRUE WHERE id = :u'), {"u": uid_admin}
    )
    await _seed_team(
        db_session,
        team_id=team_a,
        name="Leaving Crew",
        owner_id=uid_owner,
        members=[(uid_owner, "owner"), (uid_admin, "admin")],
    )
    created = await client.post("/api-keys/", json={"name": "Admin key"}, headers=_sign(uid_admin, team_a))
    assert created.status_code == 200
    team_key = {"Authorization": f"Bearer {created.json()['api_key']['key']}"}
    assert (await client.get("/tasks/workflows", headers=team_key)).status_code == 200

    await db_session.execute(
        text("DELETE FROM team_members WHERE team_id = :t AND user_id = :u"),
        {"t": team_a, "u": uid_admin},
    )
    await db_session.commit()

    assert (await client.get("/tasks/", headers=team_key)).status_code == 403
    assert (await client.get("/tasks/workflows", headers=team_key)).status_code == 403
    assert (await client.get("/fonts", headers=team_key)).status_code == 403


@pytest.mark.asyncio
async def test_one_channel_connected_personally_and_in_a_team_stays_two_accounts(db_session):
    """The same person connects the same channel in both workspaces: neither overwrites the other."""
    from src.repositories.social_repository import SocialRepository

    uid = f"u-so-{uuid.uuid4().hex[:8]}"
    team_a = f"team-so-{uuid.uuid4().hex[:8]}"
    channel = f"channel-{uuid.uuid4().hex[:8]}"
    await create_user(db_session, user_id=uid, email=f"{uid}@example.com", name="Owner")
    await _seed_team(db_session, team_id=team_a, name="Social Crew", owner_id=uid)

    async def connect(token: str, team_id: str | None = None):
        return await SocialRepository.upsert_account(
            db_session,
            user_id=uid,
            provider="youtube",
            external_account_id=channel,
            display_name="Demo Channel",
            username="demo",
            avatar_url=None,
            access_token_encrypted=token,
            refresh_token_encrypted=None,
            token_expires_at=None,
            scopes=None,
            metadata={},
            **({"team_id": team_id} if team_id else {}),
        )

    personal = await connect("personal-token-1")
    team = await connect("team-token-1", team_a)
    assert personal["id"] != team["id"]
    # The team owns its account; it does not hang off the member who connected it.
    assert (team["user_id"], team["team_id"]) == (None, team_a)
    assert (personal["user_id"], personal["team_id"]) == (uid, None)

    # Reconnecting in either workspace refreshes that workspace's row only.
    assert (await connect("personal-token-2"))["id"] == personal["id"]
    assert (await connect("team-token-2", team_a))["id"] == team["id"]
    tokens = await db_session.execute(
        text(
            "SELECT id, access_token_encrypted FROM social_accounts "
            "WHERE provider = 'youtube' AND external_account_id = :channel"
        ),
        {"channel": channel},
    )
    assert {row.id: row.access_token_encrypted for row in tokens} == {
        personal["id"]: "personal-token-2",
        team["id"]: "team-token-2",
    }

    assert [a["id"] for a in await SocialRepository.list_accounts(db_session, uid)] == [personal["id"]]
    assert [a["id"] for a in await SocialRepository.list_accounts(db_session, uid, team_id=team_a)] == [team["id"]]

    # The connecting member's account can go without taking the team's connection with it.
    await db_session.execute(
        text("UPDATE teams SET owner_id = :other WHERE id = :t"),
        {"other": (await create_user(db_session, email=f"other-{uid}@example.com"))["id"], "t": team_a},
    )
    await db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    await db_session.commit()
    left = await db_session.execute(
        text("SELECT id FROM social_accounts WHERE external_account_id = :channel"), {"channel": channel}
    )
    assert [row.id for row in left] == [team["id"]]


@pytest.mark.asyncio
async def test_team_ai_edit_counts_against_the_member_who_asked(db_session):
    """Any member can open any team clip; a member's AI edit limit follows the member."""
    from types import SimpleNamespace

    from src.api.routes.editor import _ai_edit_payer, _ai_limit_message

    creator, editor = "member-who-made-it", "member-who-edits-it"
    team_task = {"id": "t", "user_id": creator, "team_id": "team-a"}
    personal_task = {"id": "p", "user_id": creator, "team_id": None}
    assert _ai_edit_payer(SimpleNamespace(user_id=editor), team_task) == editor
    assert _ai_edit_payer(SimpleNamespace(user_id=creator), personal_task) == creator

    uid_owner = f"u-ao-{uuid.uuid4().hex[:8]}"
    uid_paused = f"u-ap-{uuid.uuid4().hex[:8]}"
    team_a = f"team-ai-{uuid.uuid4().hex[:8]}"
    for uid in (uid_owner, uid_paused):
        await create_user(db_session, user_id=uid, email=f"{uid}@example.com", name=uid)
    await _seed_team(
        db_session,
        team_id=team_a,
        name="AI Crew",
        owner_id=uid_owner,
        members=[(uid_owner, "owner"), (uid_paused, "member")],
    )
    await db_session.execute(
        text("UPDATE team_members SET monthly_ai_edit_limit = 0 WHERE team_id = :t AND user_id = :u"),
        {"t": team_a, "u": uid_paused},
    )
    await db_session.commit()

    config = get_config()
    previous = config.monetization_enabled
    config.monetization_enabled = True
    try:
        allowance = AIEditAllowanceService(db_session, config)
        # The paused member is refused on a clip the (unlimited) owner made ...
        with pytest.raises(AIEditLimitExceeded) as refused:
            await allowance.reserve(uid_paused, uuid.uuid4().hex, team_id=team_a)
        assert refused.value.usage["memberLimitReached"] is True
        assert "paused" in _ai_limit_message(refused.value.usage)
        # ... and the owner's own edit is still allowed and counted to the owner.
        job_id = uuid.uuid4().hex
        await allowance.reserve(uid_owner, job_id, team_id=team_a)
        row = (
            await db_session.execute(
                text("SELECT user_id, team_id FROM ai_edit_usage_events WHERE job_id = :j"), {"j": job_id}
            )
        ).one()
        assert (row.user_id, row.team_id) == (uid_owner, team_a)
    finally:
        config.monetization_enabled = previous
