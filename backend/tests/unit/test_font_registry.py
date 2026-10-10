import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import font_registry


@pytest.fixture(autouse=True)
def clear_coverage_cache():
    font_registry._query_font_languages.cache_clear()
    yield
    font_registry._query_font_languages.cache_clear()


def test_coverage_uses_actual_file_and_intersects_variable_font_faces(monkeypatch, tmp_path):
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    query = Mock(return_value=subprocess.CompletedProcess([], 0, "en|pt|hi\nen|pt\n"))
    monkeypatch.setattr(font_registry.subprocess, "run", query)

    assert font_registry.get_font_languages(font) == ["en", "pt"]
    assert query.call_args.args[0] == ["fc-query", "--format=%{lang}\\n", str(font)]
    assert font_registry.get_font_languages(font) == ["en", "pt"]
    assert query.call_count == 1

    font.write_bytes(b"replacement font")
    query.return_value.stdout = "hi\n"
    assert font_registry.get_font_languages(font) == ["hi"]
    assert query.call_count == 2


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("fc-query", 5), subprocess.CalledProcessError(1, "fc-query")])
def test_unavailable_coverage_is_unknown_not_unsupported(monkeypatch, tmp_path, error):
    font = tmp_path / "bad.otf"
    font.write_bytes(b"bad font")
    monkeypatch.setattr(font_registry.subprocess, "run", Mock(side_effect=error))

    assert font_registry.get_font_languages(font) is None


def test_symbol_font_with_no_complete_orthography_has_empty_coverage(monkeypatch, tmp_path):
    font = tmp_path / "symbols.otf"
    font.write_bytes(b"font")
    monkeypatch.setattr(font_registry.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, "\n")))

    assert font_registry.get_font_languages(font) == []


def test_uploaded_font_coverage_is_listed_only_for_its_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(font_registry, "FONTS_DIR", tmp_path)
    monkeypatch.setattr(font_registry, "USER_FONTS_DIR", tmp_path / "users")
    monkeypatch.setattr(font_registry, "get_font_languages", lambda _: ["hi"])
    user_font = font_registry.get_user_fonts_dir("owner") / "custom.ttf"
    user_font.parent.mkdir(parents=True)
    user_font.write_bytes(b"font")

    assert font_registry.get_available_fonts("owner")[0]["supported_languages"] == ["hi"]
    assert font_registry.get_available_fonts("someone-else") == []


@pytest.mark.skipif(not shutil.which("fc-query"), reason="Fontconfig not installed")
def test_bundled_font_does_not_claim_hindi_coverage():
    font = Path(__file__).parents[2] / "fonts" / "TikTokSans-Regular.ttf"
    languages = font_registry.get_font_languages(font)

    assert languages is not None
    assert {"en", "pt", "ru"}.issubset(languages)
    assert "hi" not in languages
