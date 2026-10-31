import json
import types
import typing

import pytest
from pydantic import BaseModel

from src.workflows.schemas import LLM_SCHEMAS

MAX_SCHEMA_CHARS = 6000


def _nested_models(model: type[BaseModel], seen=None) -> list[type[BaseModel]]:
    seen = seen if seen is not None else set()
    if model in seen:
        return []
    seen.add(model)
    found = [model]
    for field in model.model_fields.values():
        for arg in _flatten(field.annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                found += _nested_models(arg, seen)
    return found


def _flatten(annotation) -> list:
    args = typing.get_args(annotation)
    if not args:
        return [annotation]
    return [annotation] + [item for arg in args for item in _flatten(arg)]


@pytest.mark.parametrize("model", LLM_SCHEMAS, ids=lambda model: model.__name__)
def test_schema_stays_small(model):
    assert len(json.dumps(model.model_json_schema())) < MAX_SCHEMA_CHARS


@pytest.mark.parametrize("model", LLM_SCHEMAS, ids=lambda model: model.__name__)
def test_every_field_is_required_and_never_optional(model):
    for nested in _nested_models(model):
        for name, field in nested.model_fields.items():
            assert field.is_required(), f"{nested.__name__}.{name} has a default"
            types_in = _flatten(field.annotation)
            assert type(None) not in types_in, f"{nested.__name__}.{name} is Optional"
            assert not any(isinstance(arg, types.UnionType) for arg in types_in), f"{nested.__name__}.{name} is a union"


@pytest.mark.parametrize("model", LLM_SCHEMAS, ids=lambda model: model.__name__)
def test_json_schema_marks_every_property_required(model):
    schema = model.model_json_schema()
    definitions = [schema, *schema.get("$defs", {}).values()]
    for definition in definitions:
        properties = set(definition.get("properties", {}))
        assert properties == set(definition.get("required", [])), definition.get("title")


@pytest.mark.parametrize("model", LLM_SCHEMAS, ids=lambda model: model.__name__)
def test_literals_stay_short(model):
    for nested in _nested_models(model):
        for field in nested.model_fields.values():
            for arg in _flatten(field.annotation):
                if typing.get_origin(arg) is typing.Literal:
                    assert len(typing.get_args(arg)) <= 9


def test_plan_schemas_think_before_they_commit():
    from src.workflows import schemas

    first_fields = {
        schemas.SourceOutline: "analysis",
        schemas.OutlineWindow: "analysis",
        schemas.OutlineReduce: "analysis",
        schemas.SeriesScope: "analysis",
        schemas.SeriesDraft: "story_analysis",
        schemas.SeriesCritique: "viewer_notes",
        schemas.TopicLocate: "interpretation",
        schemas.TopicExtraction: "window_notes",
        schemas.RecapDraft: "editorial_notes",
        schemas.RecapCritique: "viewer_notes",
        schemas.TrailerDraft: "premise",
        schemas.SpoilerVerdict: "reasoning",
    }
    for model, field in first_fields.items():
        assert next(iter(model.model_fields)) == field, model.__name__
