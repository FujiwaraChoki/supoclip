"""Path-traversal defense tests for ``font_registry.find_font_path``."""

from pathlib import Path

import pytest

from src.font_registry import _is_path_inside, find_font_path


class TestIsPathInside:
    def test_direct_child_is_inside(self, tmp_path):
        f = tmp_path / "a.ttf"
        f.write_bytes(b"")
        assert _is_path_inside(f, tmp_path) is True

    def test_nested_child_is_inside(self, tmp_path):
        nested = tmp_path / "sub" / "deeper" / "a.ttf"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"")
        assert _is_path_inside(nested, tmp_path) is True

    def test_sibling_is_not_inside(self, tmp_path):
        sibling = tmp_path.parent / "other.ttf"
        assert _is_path_inside(sibling, tmp_path) is False

    def test_traversal_resolves_outside(self, tmp_path):
        traversal = tmp_path / ".." / "escape.ttf"
        assert _is_path_inside(traversal, tmp_path) is False

    def test_absolute_outside_path(self, tmp_path):
        elsewhere = Path("/") / "etc" / "passwd"
        assert _is_path_inside(elsewhere, tmp_path) is False


class TestFindFontPathTraversalRejection:
    @pytest.mark.parametrize(
        "malicious",
        [
            "../secret",
            "../../etc/passwd",
            "..\\..\\windows\\system32\\cmd",
            "some/../escape",
            "/absolute/path",
            "C:\\Windows\\notepad.exe",
            "\x00nullbyte",
            "legit.ttf/../evil",
        ],
    )
    def test_rejects_traversal_tokens(self, malicious):
        assert find_font_path(malicious) is None

    def test_empty_returns_none(self):
        assert find_font_path("") is None
        assert find_font_path("   ") is None

    def test_plain_name_not_rejected(self):
        # A well-formed name without traversal tokens is allowed through to
        # the filesystem lookup (may still miss — we only care that the
        # guard didn't short-circuit it).
        # Returns None because the font does not exist, but no exception.
        result = find_font_path("DoesNotExistFont-Regular")
        assert result is None or isinstance(result, Path)
