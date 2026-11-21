"""Reading and writing the user's edit style (``tasks.edit_style``, request bodies, sidecars).

Two readers with opposite contracts:

- ``parse_edit_style`` is for API input. It is strict, so a client bug is a 422 (``INVALID_EDIT_STYLE``) before any
  charge, not a silently different edit.
- ``read_edit_style`` is for persisted data. It never raises and never turns an active style written by a newer
  deploy into "off": unknown keys are dropped (``compat.known_fields``) and an invalid value falls back to that one
  key's default.

The stored form is ``EditStyle.to_json()``: only non-default keys, ``{}`` (stored as NULL) when off.
"""

from __future__ import annotations

import json
import logging
import typing
from typing import Any

from pydantic import ValidationError

from .compat import known_fields, log_dropped
from .contracts import EditStyle

logger = logging.getLogger(__name__)


class EditStyleError(ValueError):
    """An ``edit_style`` request value the API rejects (422 ``INVALID_EDIT_STYLE``)."""


def parse_edit_style(raw: object) -> EditStyle:
    """Strict: ``None`` / ``{}`` → off; a non-dict, an unknown key or a bad type/value → ``EditStyleError``."""
    if raw is None:
        return EditStyle()
    if not isinstance(raw, dict):
        raise EditStyleError("edit_style must be an object")
    if not raw:
        return EditStyle()
    unknown = sorted(str(key) for key in raw if key not in EditStyle.model_fields)
    if unknown:
        raise EditStyleError(f"Unknown edit_style setting: {', '.join(unknown)}")
    try:
        # strict: "true" or 1 is not a bool here; the stored JSON must round-trip exactly.
        return EditStyle.model_validate(raw, strict=True)
    except ValidationError as exc:
        problems = sorted({".".join(str(part) for part in error["loc"]) or "edit_style" for error in exc.errors()})
        raise EditStyleError(f"Invalid edit_style setting: {', '.join(problems)}") from None


def read_edit_style(raw: object) -> EditStyle:
    """Lenient: accepts a dict or a JSON string (asyncpg may return either for JSONB). Never raises.

    ``None``, ``{}``, non-objects and unparsable text are off. Unknown keys are dropped and logged; a known key with
    an invalid value takes that key's default, so ``{"intensity": "bold", "future_toggle": true}`` stays bold.
    """
    try:
        if isinstance(raw, (str, bytes, bytearray)):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                return EditStyle()
        if not isinstance(raw, dict) or not raw:
            return EditStyle()
        clean, dropped = known_fields(EditStyle, raw)
        log_dropped("edit_style", dropped)
        valid: dict[str, Any] = {}
        for key, value in clean.items():
            try:
                EditStyle.model_validate({key: value}, strict=True)
            except ValidationError:
                logger.info("fx_edit_style_invalid_value key=%s", key)
                continue
            valid[key] = value
        return EditStyle.model_validate(valid, strict=True)
    except Exception:   # a persisted value must never break a render or a page
        logger.warning("Could not read a stored edit_style; treating it as off", exc_info=True)
        return EditStyle()


def edit_style_schema() -> list[dict]:
    """The settings menu for ``GET /tasks/edit-styles``: every ``EditStyle`` key except ``intensity``, in
    declaration order, as ``{"key", "type": "bool"|"enum", "default", "choices"?}``."""
    schema: list[dict] = []
    for key, field in EditStyle.model_fields.items():
        if key == "intensity":
            continue
        annotation = field.annotation
        if annotation is bool:
            schema.append({"key": key, "type": "bool", "default": field.default})
            continue
        choices = _literal_choices(annotation)
        schema.append({"key": key, "type": "enum", "default": field.default, "choices": choices})
    return schema


def _literal_choices(annotation: Any) -> list[str]:
    if typing.get_origin(annotation) is typing.Literal:
        return [str(value) for value in typing.get_args(annotation)]
    raise TypeError(f"Unsupported edit_style field type: {annotation!r}")   # a contract change this menu must learn
