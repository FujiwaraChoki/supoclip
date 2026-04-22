from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
import logging
import re

logger = logging.getLogger(__name__)

SUPPORTED_FONT_EXTENSIONS = (".ttf", ".otf")
FONTS_DIR = Path(__file__).parent.parent / "fonts"
USER_FONTS_DIR = FONTS_DIR / "users"

# Fallback chain used when the user-selected font does not cover the actual
# subtitle text (e.g., TikTok Sans selected but transcript contains Hangul).
# Earlier entries win when both cover the text. Pretendard is first because
# it has excellent Korean + Latin coverage in one file.
_FALLBACK_CHAIN: tuple[str, ...] = (
    "Pretendard-Variable",
    "NotoSansKR-Variable",
    "BlackHanSans-Regular",
    "GowunDodum-Regular",
)

# Known font metadata — display name in Korean + language classification.
# Match on lowercased stem. Stems not listed fall back to auto-generated display names.
_FONT_META: dict[str, dict[str, str]] = {
    # Korean fonts
    "pretendard-variable": {"display_name": "프리텐다드", "language": "korean"},
    "notosanskr-variable": {"display_name": "노토 산스 KR", "language": "korean"},
    "blackhansans-regular": {"display_name": "검은고딕 (Black Han Sans)", "language": "korean"},
    "gowundodum-regular": {"display_name": "고운도담", "language": "korean"},
    # Latin fonts
    "tiktoksans-regular": {"display_name": "TikTok Sans", "language": "latin"},
    "inter": {"display_name": "Inter", "language": "latin"},
    "roboto": {"display_name": "Roboto", "language": "latin"},
    "opensans": {"display_name": "Open Sans", "language": "latin"},
    "dmsans": {"display_name": "DM Sans", "language": "latin"},
    "poppins-extrabold": {"display_name": "Poppins ExtraBold", "language": "latin"},
    "montserrat-variable-wght": {"display_name": "Montserrat", "language": "latin"},
    "oswald-variable-wght": {"display_name": "Oswald", "language": "latin"},
    "raleway-variable-wght": {"display_name": "Raleway", "language": "latin"},
    "worksans": {"display_name": "Work Sans", "language": "latin"},
    "nunitosans": {"display_name": "Nunito Sans", "language": "latin"},
    "urbanist": {"display_name": "Urbanist", "language": "latin"},
    "rubik": {"display_name": "Rubik", "language": "latin"},
    "sora": {"display_name": "Sora", "language": "latin"},
    "leaguespartan": {"display_name": "League Spartan", "language": "latin"},
    "anton-regular": {"display_name": "Anton", "language": "latin"},
    "archivoblack-regular": {"display_name": "Archivo Black", "language": "latin"},
    "bangers-regular": {"display_name": "Bangers", "language": "latin"},
    "barlowcondensed-bold": {"display_name": "Barlow Condensed Bold", "language": "latin"},
    "bebasneue-regular": {"display_name": "Bebas Neue", "language": "latin"},
    "theboldfont": {"display_name": "The Bold Font", "language": "latin"},
}


def _display_name(font_stem: str) -> str:
    return font_stem.replace("-", " ").replace("_", " ").strip().title()


# Common filename patterns for bold / italic variants.
_BOLD_TOKENS = ("-bold", "-bd", "_bold", "bold")
_ITALIC_TOKENS = ("-italic", "-it", "_italic", "italic", "-oblique")


def _has_variant(font_path: Path, tokens: tuple[str, ...]) -> Path | None:
    """Look for a sibling font file whose stem is the requested variant."""
    stem = font_path.stem
    # Try adding a suffix like "-Bold" to the current stem
    for token in tokens:
        for candidate_stem in (stem + token, stem + token.replace("-", "")):
            for extension in SUPPORTED_FONT_EXTENSIONS:
                candidate = font_path.parent / f"{candidate_stem}{extension}"
                if candidate.exists():
                    return candidate
    # Try replacing common weight tokens in the existing stem (e.g., "-Regular" -> "-Bold")
    lowered = stem.lower()
    for replacement in ("regular", "light", "medium", "book", "roman"):
        if replacement in lowered:
            for token in ("Bold",) if tokens is _BOLD_TOKENS else ("Italic", "Oblique"):
                replaced = re.sub(replacement, token, stem, flags=re.IGNORECASE)
                for extension in SUPPORTED_FONT_EXTENSIONS:
                    candidate = font_path.parent / f"{replaced}{extension}"
                    if candidate.exists() and candidate != font_path:
                        return candidate
    return None


def detect_variants(font_path: Path) -> dict[str, str | None]:
    """Return paths to bold and italic variants for a given font, if present."""
    return {
        "bold_path": str(_has_variant(font_path, _BOLD_TOKENS) or "") or None,
        "italic_path": str(_has_variant(font_path, _ITALIC_TOKENS) or "") or None,
    }


# Fonts known to have a usable single-file "variable" axis for weight.
# Frontend can still apply a weight preference via Pillow if the environment
# supports it — this flag is informational.
_VARIABLE_FONTS = (
    "pretendard-variable",
    "notosanskr-variable",
    "montserrat-variable-wght",
    "oswald-variable-wght",
    "raleway-variable-wght",
)


def _lookup_meta(font_stem: str) -> dict[str, str]:
    key = font_stem.lower()
    meta = _FONT_META.get(key)
    if meta:
        return meta
    # Heuristic: detect Korean-capable fonts by substring
    lowered = key
    korean_hints = ("kr", "korean", "hangul", "hansans", "gowun", "pretendard", "nanum", "jeju", "noto")
    if any(hint in lowered for hint in korean_hints):
        return {"display_name": _display_name(font_stem), "language": "korean"}
    return {"display_name": _display_name(font_stem), "language": "latin"}


def sanitize_user_id_for_path(user_id: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_-]", "-", user_id).strip("-")
    return safe_value or "user"


def get_user_fonts_dir(user_id: str) -> Path:
    return USER_FONTS_DIR / sanitize_user_id_for_path(user_id)


def _collect_fonts_from_dir(font_dir: Path, scope: str) -> list[dict[str, Any]]:
    if not font_dir.exists():
        return []

    fonts: list[dict[str, Any]] = []
    # We need to track the stems of variant files (e.g. "Inter-Bold") so we
    # don't also list them as separate top-level fonts.
    variant_stems: set[str] = set()

    candidates: list[Path] = []
    for extension in SUPPORTED_FONT_EXTENSIONS:
        candidates.extend(sorted(font_dir.glob(f"*{extension}")))

    # First pass: collect variant paths so we know which stems are "secondary"
    primary_fonts: list[Path] = []
    for font_path in candidates:
        stem_lower = font_path.stem.lower()
        is_variant = any(tok.strip("-_") in stem_lower for tok in (*_BOLD_TOKENS, *_ITALIC_TOKENS))
        # Keep "Regular" / "Variable" / anything-not-bold-or-italic as primary.
        is_plain_bold_only = stem_lower.endswith("bold") or stem_lower.endswith("-bold")
        is_plain_italic_only = stem_lower.endswith("italic") or stem_lower.endswith("-italic")
        if is_variant and (is_plain_bold_only or is_plain_italic_only):
            # Does a non-variant sibling exist? If so, hide this as primary.
            base_stem = re.sub(r"[-_]?(bold|italic|oblique|bd|it)$", "", font_path.stem, flags=re.IGNORECASE)
            has_sibling = any(
                (font_path.parent / f"{base_stem}{ext}").exists()
                or (font_path.parent / f"{base_stem}-Regular{ext}").exists()
                for ext in SUPPORTED_FONT_EXTENSIONS
            )
            if has_sibling:
                variant_stems.add(font_path.stem)
                continue
        primary_fonts.append(font_path)

    for font_path in primary_fonts:
        meta = _lookup_meta(font_path.stem)
        variants = detect_variants(font_path)
        stem_lower = font_path.stem.lower()
        is_variable = stem_lower in _VARIABLE_FONTS or "variable" in stem_lower
        fonts.append(
            {
                "name": font_path.stem,
                "display_name": meta["display_name"],
                "language": meta["language"],
                "filename": font_path.name,
                "format": font_path.suffix.lstrip("."),
                "file_path": str(font_path),
                "scope": scope,
                "has_bold_variant": variants["bold_path"] is not None,
                "has_italic_variant": variants["italic_path"] is not None,
                "is_variable": is_variable,
            }
        )

    return fonts


def get_available_fonts(user_id: str | None = None) -> list[dict[str, Any]]:
    fonts: list[dict[str, Any]] = _collect_fonts_from_dir(FONTS_DIR, scope="system")

    if user_id:
        fonts.extend(_collect_fonts_from_dir(get_user_fonts_dir(user_id), scope="user"))

    # Korean fonts first, then by display name
    return sorted(
        fonts,
        key=lambda font: (0 if font.get("language") == "korean" else 1, font["display_name"]),
    )


def _is_path_inside(candidate: Path, base: Path) -> bool:
    """True if ``candidate`` resolves to a location under ``base``.

    Protects against path-traversal tricks like ``../../etc/passwd`` by
    resolving both sides and checking for a proper prefix on the resolved
    paths. Does not require ``candidate`` to exist.
    """
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_base = base.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        resolved_candidate.relative_to(resolved_base)
        return True
    except ValueError:
        return False


def find_font_path(
    font_name: str,
    user_id: str | None = None,
    allow_all_user_fonts: bool = False,
) -> Path | None:
    requested = font_name.strip()
    if not requested:
        return None

    # Reject obvious traversal payloads before any filesystem I/O — belt &
    # braces alongside the ``_is_path_inside`` resolve check below.
    if any(sep in requested for sep in ("..", "/", "\\", "\x00")):
        return None

    search_dirs = [FONTS_DIR]
    if user_id:
        search_dirs.insert(0, get_user_fonts_dir(user_id))

    for search_dir in search_dirs:
        exact_file = search_dir / requested
        if (
            exact_file.exists()
            and exact_file.suffix.lower() in SUPPORTED_FONT_EXTENSIONS
            and _is_path_inside(exact_file, search_dir)
        ):
            return exact_file

        for extension in SUPPORTED_FONT_EXTENSIONS:
            candidate = search_dir / f"{requested}{extension}"
            if candidate.exists() and _is_path_inside(candidate, search_dir):
                return candidate

    normalized_requested = re.sub(r"[^a-z0-9]", "", requested.lower())
    for font in get_available_fonts(user_id):
        normalized_name = re.sub(r"[^a-z0-9]", "", font["name"].lower())
        if normalized_requested == normalized_name:
            return Path(font["file_path"])

    if allow_all_user_fonts:
        for font_path in USER_FONTS_DIR.glob(f"**/{requested}.*"):
            if (
                font_path.suffix.lower() in SUPPORTED_FONT_EXTENSIONS
                and _is_path_inside(font_path, USER_FONTS_DIR)
            ):
                return font_path

    return None


def sanitize_font_stem(file_name: str) -> str:
    raw_stem = Path(file_name).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "-", raw_stem).strip("-")
    if not safe_stem:
        raise ValueError("Invalid font file name")
    return safe_stem


def build_user_font_stem(user_id: str, original_stem: str) -> str:
    safe_stem = sanitize_font_stem(original_stem)
    safe_user = sanitize_user_id_for_path(user_id)
    return f"usr-{safe_user}-{safe_stem}".lower()


def is_font_accessible(font_name: str, user_id: str) -> bool:
    return find_font_path(font_name, user_id=user_id) is not None


@lru_cache(maxsize=64)
def _font_codepoints(font_path_str: str) -> frozenset[int]:
    """Return the set of Unicode codepoints a font file's cmap covers."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        logger.warning("fontTools not installed — cannot verify font coverage")
        return frozenset()

    try:
        font = TTFont(font_path_str, fontNumber=0, ignoreDecompileErrors=True)
    except Exception as exc:
        logger.warning("Failed to read font %s for coverage check: %s", font_path_str, exc)
        return frozenset()

    covered: set[int] = set()
    try:
        for cmap in font["cmap"].tables:
            covered.update(cmap.cmap.keys())
    except Exception as exc:
        logger.warning("Failed to parse cmap for %s: %s", font_path_str, exc)
    finally:
        try:
            font.close()
        except Exception:
            pass
    return frozenset(covered)


def font_covers_text(font_path: Path, text: str) -> bool:
    """True if the font covers every non-whitespace character in ``text``."""
    if not text:
        return True
    covered = _font_codepoints(str(font_path))
    if not covered:
        # Unknown coverage — treat as "covers" to avoid false fallback when
        # fontTools is unavailable. Better to render with the requested font
        # and let the result show the issue than to silently swap.
        return True
    for ch in text:
        if ch.isspace():
            continue
        if ord(ch) not in covered:
            return False
    return True


def _iter_fallback_candidates(user_id: str | None) -> Iterable[Path]:
    for name in _FALLBACK_CHAIN:
        candidate = find_font_path(name, user_id=user_id, allow_all_user_fonts=True)
        if candidate:
            yield candidate


def select_font_for_text(
    preferred_font_name: str,
    text: str,
    user_id: str | None = None,
) -> tuple[str, Path | None]:
    """
    Return (font_name, font_path) best suited for rendering ``text``.

    If the preferred font covers the text, it is returned unchanged. Otherwise
    the first font from ``_FALLBACK_CHAIN`` that covers the text is returned.
    If none cover (unlikely), the preferred font is returned and the caller
    gets to decide how to handle missing glyphs.
    """
    preferred_path = find_font_path(preferred_font_name, user_id=user_id, allow_all_user_fonts=True)

    if preferred_path and font_covers_text(preferred_path, text):
        return preferred_font_name, preferred_path

    for candidate_path in _iter_fallback_candidates(user_id):
        if font_covers_text(candidate_path, text):
            logger.info(
                "Font '%s' lacks glyphs for subtitle text; falling back to '%s'",
                preferred_font_name,
                candidate_path.stem,
            )
            return candidate_path.stem, candidate_path

    return preferred_font_name, preferred_path
