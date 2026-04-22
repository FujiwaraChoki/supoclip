"""Tests for ``src/services/prompt_service.py``.

All repository calls are patched with ``AsyncMock`` — no DB required.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.services import prompt_service as ps


class TestGetPromptContent:
    @pytest.mark.asyncio
    async def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            await ps.get_prompt_content(db=object(), key="nope")

    @pytest.mark.asyncio
    async def test_falls_back_to_default_when_no_row(self, monkeypatch):
        monkeypatch.setattr(
            ps.PromptRepository, "get_one", AsyncMock(return_value=None)
        )
        result = await ps.get_prompt_content(db="db", key=ps.KEY_TRANSCRIPT_SYSTEM)
        assert (
            result == ps.PROMPT_DEFINITIONS[ps.KEY_TRANSCRIPT_SYSTEM].default_content
        )

    @pytest.mark.asyncio
    async def test_returns_db_override_when_present(self, monkeypatch):
        monkeypatch.setattr(
            ps.PromptRepository,
            "get_one",
            AsyncMock(return_value={"content": "customized"}),
        )
        assert await ps.get_prompt_content(db="db", key=ps.KEY_TRANSCRIPT_SYSTEM) == "customized"

    @pytest.mark.asyncio
    async def test_empty_db_content_falls_back(self, monkeypatch):
        # Empty string should NOT override the default
        monkeypatch.setattr(
            ps.PromptRepository,
            "get_one",
            AsyncMock(return_value={"content": ""}),
        )
        result = await ps.get_prompt_content(db="db", key=ps.KEY_TRANSCRIPT_SYSTEM)
        assert result == ps.PROMPT_DEFINITIONS[ps.KEY_TRANSCRIPT_SYSTEM].default_content


class TestListPrompts:
    @pytest.mark.asyncio
    async def test_empty_db_uses_defaults(self, monkeypatch):
        monkeypatch.setattr(
            ps.PromptRepository, "list_all", AsyncMock(return_value=[])
        )
        rows = await ps.list_prompts(db="db")
        assert len(rows) == len(ps.PROMPT_DEFINITIONS)
        for row in rows:
            assert row["is_customized"] is False
            assert row["updated_at"] is None
            assert row["updated_by"] is None
            defn = ps.PROMPT_DEFINITIONS[row["key"]]
            assert row["content"] == defn.default_content

    @pytest.mark.asyncio
    async def test_merges_db_row(self, monkeypatch):
        updated = datetime(2026, 4, 20, tzinfo=timezone.utc)
        monkeypatch.setattr(
            ps.PromptRepository,
            "list_all",
            AsyncMock(
                return_value=[
                    {
                        "key": ps.KEY_TRANSCRIPT_SYSTEM,
                        "content": "my custom prompt",
                        "updated_at": updated,
                        "updated_by": "admin-1",
                    }
                ]
            ),
        )
        rows = await ps.list_prompts(db="db")
        customized = next(r for r in rows if r["key"] == ps.KEY_TRANSCRIPT_SYSTEM)
        assert customized["is_customized"] is True
        assert customized["content"] == "my custom prompt"
        assert customized["updated_at"] == updated.isoformat()
        assert customized["updated_by"] == "admin-1"


class TestUpdatePrompt:
    @pytest.mark.asyncio
    async def test_unknown_key_raises(self, monkeypatch):
        monkeypatch.setattr(ps.PromptRepository, "upsert", AsyncMock())
        with pytest.raises(KeyError):
            await ps.update_prompt(db="db", key="nope", content="x", updated_by=None)

    @pytest.mark.asyncio
    async def test_empty_content_rejected(self):
        with pytest.raises(ValueError):
            await ps.update_prompt(
                db="db", key=ps.KEY_TRANSCRIPT_SYSTEM, content="", updated_by=None
            )
        with pytest.raises(ValueError):
            await ps.update_prompt(
                db="db", key=ps.KEY_TRANSCRIPT_SYSTEM, content="   \n   ", updated_by=None
            )

    @pytest.mark.asyncio
    async def test_missing_required_placeholder(self):
        # transcript_user_template requires {transcript} and {broll_instruction}
        with pytest.raises(ValueError) as exc:
            await ps.update_prompt(
                db="db",
                key=ps.KEY_TRANSCRIPT_USER_TEMPLATE,
                content="no placeholders here",
                updated_by=None,
            )
        assert "placeholder" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_accepts_when_all_placeholders_present(self, monkeypatch):
        fake = AsyncMock()
        monkeypatch.setattr(ps.PromptRepository, "upsert", fake)
        await ps.update_prompt(
            db="db",
            key=ps.KEY_TRANSCRIPT_USER_TEMPLATE,
            content="Analyze {transcript} using {broll_instruction}",
            updated_by="me",
        )
        fake.assert_awaited_once()
        assert fake.await_args.kwargs["updated_by"] == "me"

    @pytest.mark.asyncio
    async def test_system_prompt_has_no_required_placeholders(self, monkeypatch):
        fake = AsyncMock()
        monkeypatch.setattr(ps.PromptRepository, "upsert", fake)
        # transcript_system has empty placeholders tuple → any non-empty content OK
        await ps.update_prompt(
            db="db",
            key=ps.KEY_TRANSCRIPT_SYSTEM,
            content="  plain new system prompt  ",
            updated_by="me",
        )
        fake.assert_awaited_once()
        # Whitespace trimmed before persisting
        assert fake.await_args.kwargs["content"] == "plain new system prompt"


class TestResetPrompt:
    @pytest.mark.asyncio
    async def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            await ps.reset_prompt(db="db", key="nope")

    @pytest.mark.asyncio
    async def test_delegates_to_repo_delete(self, monkeypatch):
        fake = AsyncMock()
        monkeypatch.setattr(ps.PromptRepository, "delete", fake)
        await ps.reset_prompt(db="db", key=ps.KEY_TRANSCRIPT_SYSTEM)
        fake.assert_awaited_once_with("db", ps.KEY_TRANSCRIPT_SYSTEM)
