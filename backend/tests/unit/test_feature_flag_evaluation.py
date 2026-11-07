"""Feature flag evaluation, platform detection and caching (no database)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src import feature_flags
from src.config import Config, set_config_override
from src.feature_flags import FeatureFlag, client_platform, evaluate
from src.workflows.contracts import WORKFLOWS
from src.workflows.options import allowed_workflows, workflow_allowed_for


def request_with(headers: dict[str, str]) -> Request:
    return Request(
        {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    )


class FakeDb:
    """Answers the flags query and the is_admin lookup; counts both."""

    def __init__(self, flags=None, admins=(), fail=False):
        self.flags = flags or []
        self.admins = set(admins)
        self.fail = fail
        self.flag_reads = 0
        self.user_reads = 0

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM feature_flags" in sql:
            self.flag_reads += 1
            if self.fail:
                raise RuntimeError('relation "feature_flags" does not exist')
            return [SimpleNamespace(**row) for row in self.flags]
        if "FROM users" in sql:
            self.user_reads += 1
            user_id = params["user_id"]
            row = {"is_admin": user_id in self.admins, "email": None, "email_verified": False}

            class _Result:
                def fetchone(self_inner):
                    return SimpleNamespace(**row)

            return _Result()
        raise AssertionError(sql)


def workflows_row(web=False, ios=False, bypass=True):
    return {"key": "workflows", "web_enabled": web, "ios_enabled": ios, "admins_bypass": bypass, "description": ""}


@pytest.fixture(autouse=True)
def fresh_cache():
    feature_flags.clear_cache()
    yield
    feature_flags.clear_cache()


@pytest.fixture
def workflows_for_all():
    config = Config()
    config.workflows_access = "all"
    set_config_override(config)
    yield config
    set_config_override(None)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, "web"),
        ({"x-supoclip-client": "ios"}, "ios"),
        ({"X-SupoClip-Client": " iOS "}, "ios"),
        ({"x-supoclip-client": "web"}, "web"),
        ({"x-supoclip-client": "android"}, "web"),
        ({"authorization": "Bearer sk_test"}, "web"),
    ],
)
def test_platform_comes_from_the_client_header(headers, expected):
    assert client_platform(request_with(headers)) == expected


def test_no_request_counts_as_web():
    assert client_platform(None) == "web"


@pytest.mark.parametrize(
    ("flag", "platform", "is_admin", "expected"),
    [
        (FeatureFlag("x", web_enabled=True), "web", False, True),
        (FeatureFlag("x", web_enabled=True), "ios", False, False),
        (FeatureFlag("x", ios_enabled=True), "ios", False, True),
        (FeatureFlag("x"), "web", True, True),
        (FeatureFlag("x", admins_bypass=False), "web", True, False),
        (FeatureFlag("x"), "ios", False, False),
    ],
)
def test_evaluate(flag, platform, is_admin, expected):
    assert evaluate(flag, platform, is_admin) is expected


def test_unknown_flags_default_to_closed_with_the_admin_bypass():
    assert feature_flags.DEFAULT_FLAGS["workflows"] == FeatureFlag("workflows", False, False, True)


@pytest.mark.asyncio
async def test_on_platform_skips_the_admin_lookup():
    db = FakeDb([workflows_row(web=True)])
    assert await feature_flags.is_enabled(db, "workflows", platform="web", user_id="u") is True
    assert db.user_reads == 0


@pytest.mark.asyncio
async def test_off_platform_lets_admins_through():
    db = FakeDb([workflows_row()], admins={"boss"})
    assert await feature_flags.is_enabled(db, "workflows", platform="web", user_id="boss") is True
    assert await feature_flags.is_enabled(db, "workflows", platform="ios", user_id="someone") is False
    assert await feature_flags.is_enabled(db, "workflows", platform="web", user_id=None) is False


@pytest.mark.asyncio
async def test_flags_are_cached():
    db = FakeDb([workflows_row(web=True)])
    for _ in range(5):
        await feature_flags.is_enabled(db, "workflows", platform="web")
    assert db.flag_reads == 1
    feature_flags._cache = (feature_flags._cache[0] - feature_flags.CACHE_TTL_SECONDS - 1, feature_flags._cache[1])
    await feature_flags.is_enabled(db, "workflows", platform="web")
    assert db.flag_reads == 2


@pytest.mark.asyncio
async def test_missing_row_or_unreadable_table_uses_the_defaults():
    assert await feature_flags.is_enabled(FakeDb([]), "workflows", platform="web", user_id="u") is False
    feature_flags.clear_cache()
    db = FakeDb(fail=True, admins={"boss"})
    assert await feature_flags.is_enabled(db, "workflows", platform="web", user_id="u") is False
    assert await feature_flags.is_enabled(db, "workflows", platform="web", user_id="boss") is True


@pytest.mark.asyncio
async def test_evaluated_flags_looks_up_the_user_once():
    db = FakeDb(
        [workflows_row(), {"key": "other", "web_enabled": False, "ios_enabled": False, "admins_bypass": True, "description": ""}],
        admins={"boss"},
    )
    assert await feature_flags.evaluated_flags(db, platform="web", user_id="boss") == {"other": True, "tiktok_publishing": False, "workflows": True}
    assert db.user_reads == 1


@pytest.mark.asyncio
async def test_workflow_gate_needs_the_flag_and_the_env_gate(workflows_for_all):
    db = FakeDb([workflows_row(web=False, ios=True)])
    assert await workflow_allowed_for(db, "u", "series", platform="web") is False
    assert await allowed_workflows(db, "u", platform="web") == frozenset()
    assert await workflow_allowed_for(db, "u", "series", platform="ios") is True
    assert await allowed_workflows(db, "u", platform="ios") == frozenset(WORKFLOWS)

    workflows_for_all.workflows_access = "off"
    assert await workflow_allowed_for(db, "u", "series", platform="ios") is False
    assert await allowed_workflows(db, "u", platform="ios") == frozenset()


@pytest.mark.asyncio
async def test_public_workflows_also_wait_for_the_flag(workflows_for_all):
    workflows_for_all.workflows_access = "admins"
    workflows_for_all.workflows_public = frozenset({"recap"})
    db = FakeDb([workflows_row(web=False, ios=True)])
    assert await allowed_workflows(db, "u", platform="web") == frozenset()
    assert await allowed_workflows(db, "u", platform="ios") == frozenset({"recap"})
