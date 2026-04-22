"""Filesystem-driven tests for ``src/font_registry.py``.

Uses ``tmp_path`` and monkeypatches ``FONTS_DIR`` so tests exercise the real
lookup/collection code without touching the repo's actual fonts directory.
"""

from pathlib import Path

import pytest

from src import font_registry as fr


def _write_font(path: Path) -> None:
    """Write a minimal placeholder — contents don't matter for registry logic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 8)


@pytest.fixture
def fake_fonts_dir(tmp_path: Path, monkeypatch):
    """Redirect FONTS_DIR and USER_FONTS_DIR to a clean tmp directory."""
    fonts_dir = tmp_path / "fonts"
    user_dir = fonts_dir / "users"
    fonts_dir.mkdir()
    user_dir.mkdir()
    monkeypatch.setattr(fr, "FONTS_DIR", fonts_dir)
    monkeypatch.setattr(fr, "USER_FONTS_DIR", user_dir)
    return fonts_dir


class TestHasVariant:
    def test_finds_bold_sibling_via_lowercase_token(self, fake_fonts_dir):
        # ``_has_variant`` builds candidates with the lowercase token suffix,
        # e.g. ``stem + "-bold"``. On case-sensitive filesystems (Linux/Docker)
        # the on-disk file must match that casing exactly.
        _write_font(fake_fonts_dir / "Inter.ttf")
        _write_font(fake_fonts_dir / "Inter-bold.ttf")
        base = fake_fonts_dir / "Inter.ttf"
        variant = fr._has_variant(base, fr._BOLD_TOKENS)
        assert variant == fake_fonts_dir / "Inter-bold.ttf"

    def test_finds_italic_sibling_via_lowercase_token(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Roboto.ttf")
        _write_font(fake_fonts_dir / "Roboto-italic.ttf")
        base = fake_fonts_dir / "Roboto.ttf"
        variant = fr._has_variant(base, fr._ITALIC_TOKENS)
        assert variant == fake_fonts_dir / "Roboto-italic.ttf"

    def test_replaces_regular_with_bold(self, fake_fonts_dir):
        # Regular → Bold replacement path IS case-insensitive via re.IGNORECASE.
        _write_font(fake_fonts_dir / "MyFont-Regular.ttf")
        _write_font(fake_fonts_dir / "MyFont-Bold.ttf")
        base = fake_fonts_dir / "MyFont-Regular.ttf"
        variant = fr._has_variant(base, fr._BOLD_TOKENS)
        assert variant == fake_fonts_dir / "MyFont-Bold.ttf"

    def test_no_variant_returns_none(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Solo.ttf")
        base = fake_fonts_dir / "Solo.ttf"
        assert fr._has_variant(base, fr._BOLD_TOKENS) is None


class TestDetectVariants:
    def test_returns_bold_and_italic_paths(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        _write_font(fake_fonts_dir / "Inter-bold.ttf")
        _write_font(fake_fonts_dir / "Inter-italic.ttf")
        result = fr.detect_variants(fake_fonts_dir / "Inter.ttf")
        assert result["bold_path"] is not None
        assert "bold" in result["bold_path"].lower()
        assert result["italic_path"] is not None
        assert "italic" in result["italic_path"].lower()

    def test_only_bold_exists(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Solo.ttf")
        _write_font(fake_fonts_dir / "Solo-bold.ttf")
        result = fr.detect_variants(fake_fonts_dir / "Solo.ttf")
        assert result["bold_path"] is not None
        assert result["italic_path"] is None


class TestCollectFontsFromDir:
    def test_empty_dir_returns_empty(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert fr._collect_fonts_from_dir(empty, scope="system") == []

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        nowhere = tmp_path / "does-not-exist"
        assert fr._collect_fonts_from_dir(nowhere, scope="system") == []

    def test_primary_font_is_listed(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        fonts = fr._collect_fonts_from_dir(fake_fonts_dir, scope="system")
        assert len(fonts) == 1
        assert fonts[0]["name"] == "Inter"
        assert fonts[0]["scope"] == "system"
        assert fonts[0]["format"] == "ttf"

    def test_bold_variant_hidden_when_sibling_present(self, fake_fonts_dir):
        # Use stem-with-Regular so the Regular→Bold replacement path (which
        # is case-insensitive) handles the variant detection.
        _write_font(fake_fonts_dir / "MyFont-Regular.ttf")
        _write_font(fake_fonts_dir / "MyFont-Bold.ttf")
        fonts = fr._collect_fonts_from_dir(fake_fonts_dir, scope="system")
        names = {f["name"] for f in fonts}
        # The primary stem is "MyFont-Regular", the Bold variant is hidden
        # because a "MyFont-Regular" sibling exists for it.
        assert "MyFont-Regular" in names
        assert "MyFont-Bold" not in names
        primary = next(f for f in fonts if f["name"] == "MyFont-Regular")
        assert primary["has_bold_variant"] is True

    def test_variable_font_flagged(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Pretendard-Variable.ttf")
        fonts = fr._collect_fonts_from_dir(fake_fonts_dir, scope="system")
        assert fonts[0]["is_variable"] is True
        assert fonts[0]["language"] == "korean"

    def test_korean_metadata(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "BlackHanSans-Regular.ttf")
        fonts = fr._collect_fonts_from_dir(fake_fonts_dir, scope="system")
        assert fonts[0]["language"] == "korean"


class TestGetAvailableFonts:
    def test_sorts_korean_first(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        _write_font(fake_fonts_dir / "Pretendard-Variable.ttf")
        fonts = fr.get_available_fonts()
        # Korean first, then Latin
        assert fonts[0]["language"] == "korean"
        assert fonts[-1]["language"] == "latin"

    def test_includes_user_fonts(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        user_id = "user-1"
        user_dir = fr.get_user_fonts_dir(user_id)
        _write_font(user_dir / "MyCustomFont.ttf")
        fonts = fr.get_available_fonts(user_id=user_id)
        names = [f["name"] for f in fonts]
        assert "MyCustomFont" in names
        # User font is marked as scope=user
        custom = next(f for f in fonts if f["name"] == "MyCustomFont")
        assert custom["scope"] == "user"


class TestFindFontPathLookup:
    def test_exact_filename_match(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        path = fr.find_font_path("Inter.ttf")
        assert path is not None
        assert path.name == "Inter.ttf"

    def test_stem_only_finds_ttf(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        path = fr.find_font_path("Inter")
        assert path is not None

    def test_normalized_match(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        # Normalized lookup strips non-alphanumeric and lowercases
        path = fr.find_font_path("inter")
        assert path is not None

    def test_user_scoped_font(self, fake_fonts_dir):
        user_id = "ux1"
        user_dir = fr.get_user_fonts_dir(user_id)
        _write_font(user_dir / "Custom.ttf")
        path = fr.find_font_path("Custom", user_id=user_id)
        assert path is not None

    def test_missing_returns_none(self, fake_fonts_dir):
        assert fr.find_font_path("NonExistent") is None


class TestIsFontAccessible:
    def test_true_when_font_exists(self, fake_fonts_dir):
        _write_font(fake_fonts_dir / "Inter.ttf")
        assert fr.is_font_accessible("Inter", user_id="x") is True

    def test_false_when_missing(self, fake_fonts_dir):
        assert fr.is_font_accessible("MissingFont", user_id="x") is False


class TestFontCoversText:
    def test_empty_text_always_covered(self):
        # Any path works — empty text short-circuits before IO
        assert fr.font_covers_text(Path("/bogus/path"), "") is True

    def test_unknown_coverage_defaults_to_true(self, monkeypatch):
        # When codepoints come back empty (fontTools unavailable or failed),
        # the helper defaults to True so the render still proceeds.
        monkeypatch.setattr(fr, "_font_codepoints", lambda _p: frozenset())
        assert fr.font_covers_text(Path("/bogus"), "hello") is True

    def test_text_missing_codepoint(self, monkeypatch):
        # ASCII 'a' only → text with '한' should not be covered.
        monkeypatch.setattr(fr, "_font_codepoints", lambda _p: frozenset({ord("a")}))
        assert fr.font_covers_text(Path("/bogus"), "aaa") is True
        assert fr.font_covers_text(Path("/bogus"), "a한") is False

    def test_whitespace_ignored(self, monkeypatch):
        monkeypatch.setattr(fr, "_font_codepoints", lambda _p: frozenset({ord("a")}))
        assert fr.font_covers_text(Path("/bogus"), "a a a") is True
