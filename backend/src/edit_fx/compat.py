"""Forward-lenient reads of persisted dynamic-edit data (AI-M9, §0.4.11).

Rows, sidecars and job payloads may be written by a newer deploy than the one reading them (rolling deploys, a
render worker on another machine). The contracts are strict (``extra="forbid"``), so a reader first strips what
its own models do not declare, logs it, and only then validates. A newer key must never turn a stored style into
"off", and an unknown cue kind drops that one cue, not the plan.
"""

from __future__ import annotations

import logging
import types
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_DROP = object()   # sentinel: the value is a model the reader does not know (an unknown cue kind)


def known_fields(model_cls: type[BaseModel], raw: dict) -> tuple[dict, list[str]]:
    """Drop keys ``model_cls`` does not declare, recursively.

    Walks nested models, ``Optional`` models, tuples/lists of models and discriminated unions (the ``FxCue`` union is
    discriminated by ``kind``; an element whose kind no member declares is dropped whole). Values are otherwise left
    as they are: type errors are the validator's business. Returns ``(clean, dropped_paths)`` with paths such as
    ``"style.new_toggle"`` or ``"cues[3]"``. Never raises; a non-dict ``raw`` gives ``({}, [])``.
    """
    dropped: list[str] = []
    if not isinstance(raw, dict):
        return {}, dropped
    return _clean_model(model_cls, raw, "", dropped), dropped


def log_dropped(where: str, dropped: list[str]) -> None:
    """The one log line every lenient reader emits when it had to drop something."""
    if dropped:
        logger.info("fx_compat_dropped_keys where=%s keys=%s", where, ",".join(dropped[:50]))


def _clean_model(model_cls: type[BaseModel], raw: dict, path: str, dropped: list[str]) -> dict:
    fields = model_cls.model_fields
    clean: dict = {}
    for key, value in raw.items():
        key_path = f"{path}.{key}" if path else str(key)
        field = fields.get(key) if isinstance(key, str) else None
        if field is None:
            dropped.append(key_path)
            continue
        cleaned = _clean_value(field.annotation, value, key_path, dropped)
        if cleaned is _DROP:
            dropped.append(key_path)
            continue
        clean[key] = cleaned
    return clean


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _strip_annotated(annotation: Any) -> Any:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _union_members(annotation: Any) -> tuple[Any, ...] | None:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        return tuple(_strip_annotated(member) for member in get_args(annotation) if member is not type(None))
    return None


def _discriminated_member(models: list[type[BaseModel]], value: dict) -> type[BaseModel] | None:
    kind = value.get("kind")
    for model in models:
        field = model.model_fields.get("kind")
        if field is not None and field.default == kind:
            return model
    return None


def _clean_value(annotation: Any, value: Any, path: str, dropped: list[str]) -> Any:
    annotation = _strip_annotated(annotation)
    if _is_model(annotation):
        return _clean_model(annotation, value, path, dropped) if isinstance(value, dict) else value

    members = _union_members(annotation)
    if members is not None:
        if not isinstance(value, dict):
            return value
        models = [member for member in members if _is_model(member)]
        if not models:
            return value
        if len(models) == 1:
            return _clean_model(models[0], value, path, dropped)
        model = _discriminated_member(models, value)
        if model is None:
            return _DROP
        return _clean_model(model, value, path, dropped)

    origin = get_origin(annotation)
    if origin in (tuple, list) and isinstance(value, list):
        args = get_args(annotation)
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            return value                    # fixed-shape tuple (e.g. a (start, end) pair): plain data
        element = args[0] if args else Any
        out = []
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            cleaned = _clean_value(element, item, item_path, dropped)
            if cleaned is _DROP:
                dropped.append(item_path)
                continue
            out.append(cleaned)
        return out
    return value
