"""WORKFLOWS_ACCESS rollout gate (spec section 4.4)."""

from types import SimpleNamespace

import pytest

from src.config import Config, set_config_override
from src.workflows.options import workflows_enabled_for


class _UsersDb:
    def __init__(self, users):
        self.users = users
        self.queries = 0

    async def execute(self, _statement, params):
        self.queries += 1
        row = self.users.get(params["user_id"])

        class _Result:
            def fetchone(self_inner):
                return SimpleNamespace(**row) if row else None

        return _Result()


USERS = {
    "admin": {"is_admin": True, "email": "boss@example.com"},
    "beta": {"is_admin": False, "email": " Beta@Example.com "},
    "user": {"is_admin": False, "email": "someone@example.com"},
    "no-email": {"is_admin": False, "email": None},
}


@pytest.fixture
def config():
    config = Config()
    set_config_override(config)
    yield config
    set_config_override(None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "admins"), ("", "admins"), ("all", "all"), (" ALL ", "all"), ("off", "off"), ("Admins", "admins"), ("everyone", "admins")],
)
def test_access_setting_defaults_to_admins(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("WORKFLOWS_ACCESS", raising=False)
    else:
        monkeypatch.setenv("WORKFLOWS_ACCESS", value)
    assert Config().workflows_access == expected


def test_allowlist_and_limits_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("WORKFLOWS_ALLOWED_EMAILS", " A@Example.com, ,b@example.com ")
    monkeypatch.setenv("WORKFLOW_TOPIC_MAX_CLIPS", "8")
    monkeypatch.setenv("WORKFLOW_PLAN_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("WORKFLOW_FULL_CONTEXT_MAX_WORDS", "40000")
    config = Config()
    assert config.workflows_allowed_emails == frozenset({"a@example.com", "b@example.com"})
    assert config.workflow_topic_max_clips == 8
    assert config.workflow_plan_timeout_seconds == 900.0
    assert config.workflow_full_context_max_words == 40000


def test_limit_defaults(monkeypatch):
    for name in (
        "WORKFLOWS_ALLOWED_EMAILS",
        "WORKFLOW_TOPIC_MAX_CLIPS",
        "WORKFLOW_PLAN_TIMEOUT_SECONDS",
        "WORKFLOW_FULL_CONTEXT_MAX_WORDS",
    ):
        monkeypatch.delenv(name, raising=False)
    config = Config()
    assert config.workflows_allowed_emails == frozenset()
    assert config.workflow_topic_max_clips == 15
    assert config.workflow_plan_timeout_seconds == 1800.0
    assert config.workflow_full_context_max_words is None
    monkeypatch.setenv("WORKFLOW_PLAN_TIMEOUT_SECONDS", "-5")
    monkeypatch.setenv("WORKFLOW_FULL_CONTEXT_MAX_WORDS", "-1")
    config = Config()
    assert config.workflow_plan_timeout_seconds == 1800.0
    assert config.workflow_full_context_max_words is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access", "allowlist", "expected"),
    [
        ("all", frozenset(), {"admin": True, "beta": True, "user": True, "no-email": True, "missing": True}),
        ("admins", frozenset({"beta@example.com"}), {"admin": True, "beta": True, "user": False, "no-email": False, "missing": False}),
        ("admins", frozenset(), {"admin": True, "beta": False, "user": False, "no-email": False, "missing": False}),
        ("off", frozenset({"beta@example.com"}), {"admin": False, "beta": False, "user": False, "no-email": False, "missing": False}),
    ],
)
async def test_gate_respects_admins_and_allowlist(config, access, allowlist, expected):
    config.workflows_access = access
    config.workflows_allowed_emails = allowlist
    db = _UsersDb(USERS)
    assert {user: await workflows_enabled_for(db, user) for user in expected} == expected
    if access != "admins":
        assert db.queries == 0


@pytest.mark.asyncio
async def test_gate_without_a_database_or_user_is_closed(config):
    config.workflows_access = "admins"
    assert await workflows_enabled_for(None, "admin") is False
    assert await workflows_enabled_for(_UsersDb(USERS), "") is False
    config.workflows_access = "all"
    assert await workflows_enabled_for(None, "anyone") is True
