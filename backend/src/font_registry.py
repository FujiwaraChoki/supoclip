from pathlib import Path
from typing import Any
import re
import struct

SUPPORTED_FONT_EXTENSIONS = (".ttf", ".otf")
FONTS_DIR = Path(__file__).parent.parent / "fonts"
USER_FONTS_DIR = FONTS_DIR / "users"
TTF_NAME_ID_FONT_FAMILY = 1
TTF_NAME_ID_FULL_NAME = 4


def _display_name(font_stem: str) -> str:
    return font_stem.replace("-", " ").replace("_", " ").strip().title()


def sanitize_user_id_for_path(user_id: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_-]", "-", user_id).strip("-")
    return safe_value or "user"


def get_user_fonts_dir(user_id: str) -> Path:
    return USER_FONTS_DIR / sanitize_user_id_for_path(user_id)


def _collect_fonts_from_dir(font_dir: Path, scope: str) -> list[dict[str, Any]]:
    if not font_dir.exists():
        return []

    fonts: list[dict[str, Any]] = []
    for extension in SUPPORTED_FONT_EXTENSIONS:
        for font_path in sorted(font_dir.glob(f"*{extension}")):
            fonts.append(
                {
                    "name": font_path.stem,
                    "display_name": _display_name(font_path.stem),
                    "filename": font_path.name,
                    "format": extension.lstrip("."),
                    "file_path": str(font_path),
                    "scope": scope,
                }
            )

    return fonts


def get_available_fonts(user_id: str | None = None) -> list[dict[str, Any]]:
    fonts: list[dict[str, Any]] = _collect_fonts_from_dir(FONTS_DIR, scope="system")

    if user_id:
        fonts.extend(_collect_fonts_from_dir(get_user_fonts_dir(user_id), scope="user"))

    return sorted(fonts, key=lambda font: font["display_name"])


def find_font_path(
    font_name: str,
    user_id: str | None = None,
    allow_all_user_fonts: bool = False,
) -> Path | None:
    requested = font_name.strip()
    if not requested:
        return None

    search_dirs = [FONTS_DIR]
    if user_id:
        search_dirs.insert(0, get_user_fonts_dir(user_id))

    for search_dir in search_dirs:
        exact_file = search_dir / requested
        if (
            exact_file.exists()
            and exact_file.suffix.lower() in SUPPORTED_FONT_EXTENSIONS
        ):
            return exact_file

        for extension in SUPPORTED_FONT_EXTENSIONS:
            candidate = search_dir / f"{requested}{extension}"
            if candidate.exists():
                return candidate

    normalized_requested = re.sub(r"[^a-z0-9]", "", requested.lower())
    for font in get_available_fonts(user_id):
        normalized_name = re.sub(r"[^a-z0-9]", "", font["name"].lower())
        if normalized_requested == normalized_name:
            return Path(font["file_path"])

    if allow_all_user_fonts:
        for font_path in USER_FONTS_DIR.glob(f"**/{requested}.*"):
            if font_path.suffix.lower() in SUPPORTED_FONT_EXTENSIONS:
                return font_path

    return None


def _decode_ttf_name(raw_value: bytes, platform_id: int) -> str:
    encodings = ["utf-16-be"] if platform_id in {0, 3} else ["mac_roman", "utf-8"]
    for encoding in encodings:
        try:
            decoded = raw_value.decode(encoding).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
        if decoded:
            return decoded
    return ""


def get_font_family_name(font_path: Path) -> str | None:
    """Read a font's internal family name for renderers that match by metadata."""
    try:
        data = Path(font_path).read_bytes()
        if len(data) < 12:
            return None

        num_tables = struct.unpack_from(">H", data, 4)[0]
        name_table_offset = None
        name_table_length = None
        table_record_offset = 12
        for table_index in range(num_tables):
            record_offset = table_record_offset + table_index * 16
            if record_offset + 16 > len(data):
                return None
            tag, _checksum, table_offset, table_length = struct.unpack_from(
                ">4sIII", data, record_offset
            )
            if tag == b"name":
                name_table_offset = table_offset
                name_table_length = table_length
                break

        if name_table_offset is None or name_table_length is None:
            return None
        if name_table_offset + min(name_table_length, 6) > len(data):
            return None

        _format_selector, record_count, string_offset = struct.unpack_from(
            ">HHH", data, name_table_offset
        )
        storage_start = name_table_offset + string_offset
        candidates: dict[int, list[str]] = {
            TTF_NAME_ID_FONT_FAMILY: [],
            TTF_NAME_ID_FULL_NAME: [],
        }

        for record_index in range(record_count):
            record_offset = name_table_offset + 6 + record_index * 12
            if record_offset + 12 > len(data):
                break
            (
                platform_id,
                _encoding_id,
                language_id,
                name_id,
                value_length,
                value_offset,
            ) = struct.unpack_from(">HHHHHH", data, record_offset)
            if name_id not in candidates:
                continue
            value_start = storage_start + value_offset
            value_end = value_start + value_length
            if value_start < storage_start or value_end > len(data):
                continue
            decoded = _decode_ttf_name(data[value_start:value_end], platform_id)
            if not decoded:
                continue
            # Prefer English records when present, but keep every valid name as fallback.
            if language_id in {0x0409, 0x0000}:
                candidates[name_id].insert(0, decoded)
            else:
                candidates[name_id].append(decoded)

        family_names = list(dict.fromkeys(candidates[TTF_NAME_ID_FONT_FAMILY]))
        if family_names:
            return min(family_names, key=len)

        for candidate in candidates[TTF_NAME_ID_FULL_NAME]:
            if candidate:
                return candidate
    except Exception:
        return None

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
