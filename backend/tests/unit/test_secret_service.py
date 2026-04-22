"""Tests for the file-backed secret store in ``src/services/secret_service.py``.

Uses ``tmp_path`` to redirect the SECRETS_DIR/SECRETS_FILE so the real
``backend/secrets/app_secrets.json`` is never touched.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.services import secret_service as ss


@pytest.fixture
def fake_secrets_dir(tmp_path: Path, monkeypatch):
    """Redirect SECRETS_DIR + SECRETS_FILE, clear environ pollution between tests."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    monkeypatch.setattr(ss, "SECRETS_DIR", secrets_dir)
    monkeypatch.setattr(ss, "SECRETS_FILE", secrets_dir / "app_secrets.json")

    # Snapshot + restore environ so test writes don't leak
    saved = {k: os.environ.get(k) for k in list(ss.SECRET_DEFINITIONS.keys())}
    yield secrets_dir
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class TestMaskPreview:
    def test_none_returns_none(self):
        assert ss.mask_preview(None) is None
        assert ss.mask_preview("") is None

    def test_short_value_fully_masked(self):
        # Under _MIN_REVEAL_LEN → no prefix/suffix reveal
        result = ss.mask_preview("short")
        assert result.startswith("***")
        assert result.endswith("***")
        assert "short" not in result

    def test_long_value_shows_edges(self):
        preview = ss.mask_preview("abcdefghijklmnop")  # 16 chars
        assert preview.startswith("abcd")
        assert preview.endswith("mnop")
        assert ss.MASK_MIDDLE in preview


class TestReadWriteFile:
    def test_read_missing_file_returns_empty(self, fake_secrets_dir):
        assert ss._read_file() == {}

    def test_read_invalid_json_returns_empty(self, fake_secrets_dir):
        ss.SECRETS_FILE.write_text("not{valid json", encoding="utf-8")
        assert ss._read_file() == {}

    def test_read_non_dict_returns_empty(self, fake_secrets_dir):
        ss.SECRETS_FILE.write_text(json.dumps(["a", "b"]), encoding="utf-8")
        assert ss._read_file() == {}

    def test_write_then_read_roundtrip(self, fake_secrets_dir):
        payload = {"ASSEMBLY_AI_API_KEY": {"value": "x", "updated_at": "t"}}
        ss._write_file(payload)
        assert ss._read_file() == payload

    def test_write_creates_directory(self, tmp_path, monkeypatch):
        new_dir = tmp_path / "does-not-exist-yet"
        monkeypatch.setattr(ss, "SECRETS_DIR", new_dir)
        monkeypatch.setattr(ss, "SECRETS_FILE", new_dir / "app_secrets.json")

        ss._write_file({"key": {"value": "v"}})
        assert new_dir.exists()
        assert (new_dir / "app_secrets.json").exists()


class TestListStatus:
    def test_all_unset_when_file_empty(self, fake_secrets_dir):
        rows = ss.list_status()
        assert len(rows) == len(ss.SECRET_DEFINITIONS)
        for row in rows:
            assert row["set"] is False
            assert row["preview"] is None
            assert row["updated_at"] is None

    def test_shows_preview_when_set(self, fake_secrets_dir):
        ss._write_file({
            "ASSEMBLY_AI_API_KEY": {
                "value": "abcdefghijklmnop",
                "updated_at": "2026-04-23T00:00:00+00:00",
            }
        })
        rows = ss.list_status()
        row = next(r for r in rows if r["name"] == "ASSEMBLY_AI_API_KEY")
        assert row["set"] is True
        assert row["preview"] is not None
        assert "abcd" in row["preview"]
        assert row["updated_at"] == "2026-04-23T00:00:00+00:00"

    def test_handles_legacy_non_dict_rows(self, fake_secrets_dir):
        # If the file somehow has a string instead of a dict, we should not crash.
        ss._write_file({"ASSEMBLY_AI_API_KEY": "legacy-string"})  # type: ignore[dict-item]
        rows = ss.list_status()
        row = next(r for r in rows if r["name"] == "ASSEMBLY_AI_API_KEY")
        assert row["set"] is False


class TestUpsert:
    def test_rejects_unknown_name(self, fake_secrets_dir):
        with pytest.raises(KeyError):
            ss.upsert("NOT_A_REGISTERED_SECRET", "value")

    def test_rejects_empty_value(self, fake_secrets_dir):
        with pytest.raises(ValueError):
            ss.upsert("ASSEMBLY_AI_API_KEY", "")

    def test_rejects_whitespace_only_value(self, fake_secrets_dir):
        with pytest.raises(ValueError):
            ss.upsert("ASSEMBLY_AI_API_KEY", "   \n   ")

    def test_stores_and_updates_environ(self, fake_secrets_dir):
        result = ss.upsert("ASSEMBLY_AI_API_KEY", "real-api-key-123456")
        assert result["set"] is True
        assert result["preview"] is not None
        # Persisted
        stored = ss._read_file()
        assert stored["ASSEMBLY_AI_API_KEY"]["value"] == "real-api-key-123456"
        # Populated environ
        assert os.environ["ASSEMBLY_AI_API_KEY"] == "real-api-key-123456"
        # Timestamp is ISO-8601
        datetime.fromisoformat(stored["ASSEMBLY_AI_API_KEY"]["updated_at"])

    def test_trims_whitespace(self, fake_secrets_dir):
        ss.upsert("ASSEMBLY_AI_API_KEY", "  trimmed-value  ")
        assert ss._read_file()["ASSEMBLY_AI_API_KEY"]["value"] == "trimmed-value"


class TestDelete:
    def test_unknown_name_raises(self, fake_secrets_dir):
        with pytest.raises(KeyError):
            ss.delete("NOT_A_REGISTERED_SECRET")

    def test_missing_returns_false(self, fake_secrets_dir):
        assert ss.delete("ASSEMBLY_AI_API_KEY") is False

    def test_existing_returns_true_and_removes(self, fake_secrets_dir):
        ss.upsert("ASSEMBLY_AI_API_KEY", "value-that-is-long-enough")
        assert os.environ.get("ASSEMBLY_AI_API_KEY") == "value-that-is-long-enough"

        assert ss.delete("ASSEMBLY_AI_API_KEY") is True
        assert "ASSEMBLY_AI_API_KEY" not in os.environ
        assert "ASSEMBLY_AI_API_KEY" not in ss._read_file()


class TestHydrateEnviron:
    def test_empty_file_returns_zero(self, fake_secrets_dir):
        assert ss.hydrate_environ() == 0

    def test_applies_stored_values(self, fake_secrets_dir):
        os.environ.pop("ASSEMBLY_AI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
        ss._write_file({
            "ASSEMBLY_AI_API_KEY": {"value": "aaa", "updated_at": "t"},
            "GOOGLE_API_KEY": {"value": "bbb", "updated_at": "t"},
        })
        count = ss.hydrate_environ()
        assert count == 2
        assert os.environ["ASSEMBLY_AI_API_KEY"] == "aaa"
        assert os.environ["GOOGLE_API_KEY"] == "bbb"

    def test_skips_non_dict_rows(self, fake_secrets_dir):
        ss._write_file({
            "ASSEMBLY_AI_API_KEY": {"value": "good", "updated_at": "t"},
            "GARBAGE": "not-a-dict",  # type: ignore[dict-item]
        })
        assert ss.hydrate_environ() == 1

    def test_skips_empty_values(self, fake_secrets_dir):
        os.environ.pop("ASSEMBLY_AI_API_KEY", None)
        ss._write_file({
            "ASSEMBLY_AI_API_KEY": {"value": "", "updated_at": "t"},
        })
        assert ss.hydrate_environ() == 0
        assert "ASSEMBLY_AI_API_KEY" not in os.environ
