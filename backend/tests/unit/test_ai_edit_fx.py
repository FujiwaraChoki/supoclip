"""AI edits with dynamic edits (§14.1): unflagged creators keep today's prompt and grammar byte for byte; allowed
creators get fx settings, AddEffect / RemoveEffects / TagBeat, per-op clamps and a re-resolve; the studio's beat
planning job on the AI worker."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.edit_fx.contracts import EditBeats, EditStyle
from src.editor_document import DocFx, EditDocument, fx_hold_budget, mapped_hold_total
from src.services import ai_edit_service as ai
from src.services.ai_edit_service import EditPlan, EditPlanFx, apply_plan, build_context

PINS = Path(__file__).resolve().parents[1] / "fixtures" / "legacy_pins"
TEXT = ("so here is the thing nobody tells you about money you can make ten thousand dollars a month "
        "and still be broke because the real secret is what you keep").split()


def words(count=40):
    return [{"id": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4, "text": TEXT[i % len(TEXT)]}
            for i in range(count)]


def draft(fx=None, **extra) -> EditDocument:
    data = {"segments": [{"id": "s", "start": 0, "end": 20}], "words": words(), **extra}
    if fx is not None:
        data["fx"] = fx
    return EditDocument.model_validate(data)


def planned(**fx) -> dict:
    beats = [{"id": f"b{i}", "kind": "emphasis", "start": 1.0 + 3 * i, "end": 1.8 + 3 * i, "strength": 3,
              "words": ["money"], "text": "money"} for i in range(6)]
    return DocFx.model_validate({"mood": "hype", "seed": 5, "beats": beats, **fx}).model_dump()


def fx_plan(*operations) -> EditPlanFx:
    return EditPlanFx.model_validate({"summary": "Done.", "operations": list(operations)})


async def apply_fx(document, *operations, duration=20.0):
    return await apply_plan(document, fx_plan(*operations), duration, fx_allowed=True, fx_seed=99,
                            layout_has_zoompan=True)


# --------------------------------------------------------------------------- unflagged creators: unchanged


def _fixture_document() -> EditDocument:
    return EditDocument.model_validate(json.loads((PINS / "editor_document.json").read_text(encoding="utf-8")))


def test_context_without_dynamic_edits_is_the_pinned_text():
    document = _fixture_document()
    expected = (PINS / "expected" / "ai_edit_context.txt").read_text(encoding="utf-8")
    assert build_context(document, 12.0, 5.5, "Cut the um and make it punchier") == expected
    assert build_context(document, 12.0, 5.5, "Cut the um and make it punchier", fx_allowed=False) == expected
    assert (PINS / "expected" / "ai_edit_system_prompt.txt").read_text(encoding="utf-8") == ai.SYSTEM_PROMPT
    assert json.dumps(EditPlan.model_json_schema(), indent=2, ensure_ascii=False) + "\n" == (
        PINS / "expected" / "ai_edit_op_schema.txt").read_text(encoding="utf-8")


async def test_legacy_plans_cannot_reach_dynamic_edits():
    with pytest.raises(Exception):
        EditPlan.model_validate({"summary": "x", "operations": [
            {"type": "add_effect", "kind": "sfx", "start": 1, "end": 1, "variant": "pop"}]})
    with pytest.raises(Exception):
        EditPlan.model_validate({"summary": "x", "operations": [{"type": "set", "setting": "fx.intensity",
                                                                  "value": "bold"}]})
    plan = EditPlan.model_validate({"summary": "x", "operations": [
        {"type": "set", "setting": "captions.template", "value": "slam"}]})
    result, notes = await apply_plan(draft(), plan, 20.0)
    assert result.captions.template == "default" and notes == ["Caption template 'slam' doesn't exist"]
    assert result.fx is None


def _walk(schema, defs, seen=None):
    """Every object schema reachable from ``schema``."""
    seen = seen if seen is not None else set()
    if isinstance(schema, dict):
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                yield from _walk(defs[name], defs, seen)
        if schema.get("type") == "object" or "properties" in schema:
            yield schema
        for value in schema.values():
            yield from _walk(value, defs, seen)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk(value, defs, seen)


def test_the_fx_grammar_has_no_optional_fields():
    from anthropic import transform_schema

    schema = EditPlanFx.model_json_schema()
    objects = list(_walk(schema, schema.get("$defs", {})))
    assert len(objects) > 10
    for obj in objects:
        assert set(obj.get("properties", {})) == set(obj.get("required", [])), obj.get("title")
    transformed = json.dumps(transform_schema(EditPlanFx))
    assert '"null"' not in transformed and len(transformed) < 9000


# --------------------------------------------------------------------------- allowed creators


def test_the_fx_context_lists_style_beats_and_cues():
    doc = draft(fx={**planned(style={"intensity": "bold"}), "cues": [
        {"kind": "zoom", "id": "e7", "start": 12.4, "end": 13.1, "mode": "punch", "scale": 1.2},
        {"kind": "freeze", "id": "u0", "start": 5.0, "end": 5.0, "hold": 0.5, "label": "wait", "origin": "user"},
    ]})
    context = build_context(doc, 20.0, None, "add a whoosh", fx_allowed=True)
    assert "  e7 zoom punch 12.40-13.10 auto" in context
    assert '  u0 freeze hold 0.50s "wait" 5.00 user' in context
    assert "  b0 emphasis 1.00-1.80 strength 3" in context
    assert "fx.intensity (" in context and "emoji_drop (" in context and "Effect kinds for add_effect" in context
    assert "slam (" not in context                      # unlisted styles are never offered
    assert build_context(draft(), 20.0, None, "x", fx_allowed=True).count("Dynamic edits: off") == 1


def test_the_fx_context_stays_within_120_effect_lines():
    cues = [{"kind": "sfx", "id": f"c{i}", "start": i * 0.04, "end": i * 0.04, "sound": "pop"} for i in range(400)]
    context = build_context(draft(fx={**planned(), "cues": cues}), 20.0, None, "x", fx_allowed=True)
    listed = [line for line in context.splitlines() if line.startswith("  c") or line.startswith("  b")]
    assert len(listed) <= 120 and "... and" in context


async def test_setting_the_intensity_re_resolves_the_automatic_effects():
    result, notes = await apply_fx(draft(fx=planned()), {"type": "set", "setting": "fx.intensity", "value": "bold"})
    assert notes == [] and result.fx.style.intensity == "bold"
    assert any(c.origin == "auto" for c in result.fx.cues)
    unplanned, notes = await apply_fx(draft(), {"type": "set", "setting": "fx.intensity", "value": "Max"})
    assert unplanned.fx.style.intensity == "max" and notes == ["Press Plan edits to tag beats first"]
    _, notes = await apply_fx(draft(), {"type": "set", "setting": "fx.intensity", "value": "extreme"})
    assert notes == ["fx.intensity: 'extreme' isn't one of the choices"]
    toggled, _ = await apply_fx(draft(fx=planned(style={"intensity": "bold"})),
                                {"type": "set", "setting": "fx.music", "value": "off"})
    assert toggled.fx.style.music is False and toggled.fx.music is None


async def test_fx_templates_are_accepted_and_resolve_style_only():
    result, notes = await apply_fx(draft(), {"type": "set", "setting": "captions.template", "value": "highlighter_swipe"})
    assert notes == [] and result.captions.template == "highlighter_swipe"
    _, notes = await apply_fx(draft(), {"type": "set", "setting": "captions.template", "value": "marker_circle"})
    assert notes and "doesn't exist" in notes[0]          # an unlisted style is never picked anew
    assert any(c.kind == "accent" for c in result.fx.cues)


@pytest.mark.parametrize("op, check", [
    ({"kind": "sfx", "start": 3.0, "end": 3.0, "variant": "Whoosh"}, lambda c: c.sound == "whoosh" and c.start == 3.0),
    ({"kind": "zoom", "start": 3.0, "end": 30.0, "variant": "push"}, lambda c: c.mode == "push" and c.end == 11.0),
    ({"kind": "shake", "start": 3.0, "end": 3.0, "variant": "heavy"}, lambda c: c.amp == 20 and c.end == 3.15),
    ({"kind": "flash", "start": 3.0, "end": 3.2, "variant": "#ff0000"}, lambda c: c.color == "#FF0000"),
    ({"kind": "glitch", "start": 3.0, "end": 3.2, "variant": "light"}, lambda c: c.amount == 0.3),
    ({"kind": "freeze", "start": 3.01, "end": 3.31, "variant": "wait"}, lambda c: c.hold == 0.3 and c.label == "wait"),
    ({"kind": "sticker", "start": 3.0, "end": 4.0, "variant": "wait for it"}, lambda c: c.sticker == "wait_for_it"),
    ({"kind": "sticker", "start": 3.0, "end": 4.0, "variant": "no way"}, lambda c: c.text == "no way"),
    ({"kind": "transition", "start": 3.0, "end": 3.0, "variant": "whip"}, lambda c: c.style == "whip"),
])
async def test_add_effect_makes_a_clamped_user_cue(op, check):
    result, notes = await apply_fx(draft(), {"type": "add_effect", **op})
    assert notes == []
    (cue,) = result.fx.cues
    assert cue.id == "u0" and cue.origin == "user" and cue.kind == op["kind"] and check(cue), cue
    assert result.fx.style == EditStyle()   # an effect never switches the intensity on


@pytest.mark.parametrize("op, note", [
    ({"kind": "sfx", "start": 3, "end": 3, "variant": "trumpet"}, "the sound 'trumpet' isn't available"),
    ({"kind": "zoom", "start": 3, "end": 4, "variant": "spin"}, "isn't punch, push or pull"),
    ({"kind": "transition", "start": 3, "end": 3, "variant": "dissolve"}, "isn't available"),
    ({"kind": "sticker", "start": 3, "end": 4, "variant": "  "}, "needs its text"),
])
async def test_unknown_effect_values_are_skipped_with_a_note(op, note):
    result, notes = await apply_fx(draft(), {"type": "add_effect", **op})
    assert len(notes) == 1 and note in notes[0] and result.fx.cues == []


async def test_an_effect_outside_the_edit_is_skipped():
    doc = draft(segments=[{"id": "a", "start": 0, "end": 5}, {"id": "b", "start": 10, "end": 20}])
    result, notes = await apply_fx(doc, {"type": "add_effect", "kind": "sfx", "start": 7, "end": 7, "variant": "pop"})
    assert "isn't in the edit" in notes[0] and result.fx.cues == []


async def test_an_over_budget_freeze_is_skipped_and_the_rest_applies():
    budget = fx_hold_budget(20.0)    # 0.8 s on a 20 s clip
    result, notes = await apply_fx(
        draft(),
        {"type": "add_effect", "kind": "freeze", "start": 2.0, "end": 2.6, "variant": ""},
        {"type": "add_effect", "kind": "freeze", "start": 6.0, "end": 6.6, "variant": ""},
        {"type": "add_effect", "kind": "sfx", "start": 7.0, "end": 7.0, "variant": "pop"},
        {"type": "set", "setting": "captions.uppercase", "value": "true"},
    )
    assert [c.kind for c in result.fx.cues] == ["freeze", "sfx"]
    assert len(notes) == 1 and "Freeze at 6.0s: skipped" in notes[0]
    assert mapped_hold_total(result) <= budget and result.captions.uppercase is True


async def test_without_the_flag_a_draft_with_edits_can_keep_or_reduce_them_but_not_turn_more_on():
    """§0.4.8: an unflagged creator whose draft already has edits gets the fx grammar, but a legacy -> FX template
    or an intensity switched on from off is skipped (saving it would be refused), while lowering it works."""
    styled = draft(captions={"template": "highlighter_swipe"}, fx=planned())

    async def unflagged(document, *operations):
        return await apply_plan(document, fx_plan(*operations), 20.0, fx_allowed=True, fx_seed=99,
                                layout_has_zoompan=True, fx_flag=False)

    result, notes = await unflagged(styled, {"type": "set", "setting": "fx.intensity", "value": "bold"})
    assert result.fx.style.intensity == "off" and notes == ["fx.intensity: dynamic edits aren't available on your account yet"]
    bold = draft(fx=planned(style={"intensity": "bold"}))
    result, notes = await unflagged(bold, {"type": "set", "setting": "captions.template", "value": "emoji_drop"})
    assert result.captions.template == "default" and "aren't available" in notes[0]
    result, notes = await unflagged(bold, {"type": "set", "setting": "fx.intensity", "value": "max"},
                                    {"type": "add_effect", "kind": "sfx", "start": 3, "end": 3, "variant": "pop"})
    assert notes == [] and result.fx.style.intensity == "max" and any(c.id == "u0" for c in result.fx.cues)
    result, notes = await unflagged(styled, {"type": "set", "setting": "captions.template", "value": "emoji_drop"})
    assert notes == [] and result.captions.template == "emoji_drop"
    result, notes = await unflagged(bold, {"type": "set", "setting": "fx.intensity", "value": "off"})
    assert notes == [] and result.fx.style.intensity == "off"


async def test_remove_effects_deletes_user_cues_and_suppresses_auto_ones():
    fx = {**planned(), "cues": [
        {"kind": "zoom", "id": "b0.zoom", "start": 1.0, "end": 1.5, "mode": "punch", "scale": 1.2},
        {"kind": "sfx", "id": "u0", "start": 2.0, "end": 2.0, "sound": "pop", "origin": "user"},
    ]}
    result, notes = await apply_fx(draft(fx=fx), {"type": "remove_effects", "ids": ["b0.zoom", "u0", "nope"]})
    assert result.fx.cues == [] and result.fx.suppressed == ["b0.zoom"]
    assert notes == ["Remove effects: nope doesn't exist"]


async def test_tag_beat_adds_a_user_beat_and_re_resolves():
    doc = draft(fx={**planned(style={"intensity": "bold"}), "beats": []})
    result, notes = await apply_fx(doc, {"type": "tag_beat", "word_ids": ["w14", "w13", "w15"], "kind": "number",
                                         "strength": 9})
    assert notes == []
    (beat,) = result.fx.beats
    assert (beat.id, beat.kind, beat.source, beat.strength, beat.start, beat.end) == ("u0", "number", "user", 3, 6.5, 7.9)
    assert beat.text == "ten thousand dollars" and beat.value == 10000
    assert any(c.beat == "u0" for c in result.fx.cues)
    _, notes = await apply_fx(doc, {"type": "tag_beat", "word_ids": ["nope"], "kind": "hook", "strength": 2})
    assert notes == ["Tag moment: none of those words exist"]


# --------------------------------------------------------------------------- the worker job


class FakeRedis:
    def __init__(self):
        self.data, self.counts = {}, {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, key):
        self.data.pop(key, None)

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def decr(self, key):
        self.counts[key] = self.counts.get(key, 0) - 1
        return self.counts[key]

    async def expire(self, key, seconds):
        return True


@pytest.fixture
def no_runtime_settings(monkeypatch):
    monkeypatch.setattr(ai, "refund_ai_edit_usage", AsyncMock())
    monkeypatch.setattr("src.runtime_settings.load_runtime_settings_cache", AsyncMock())

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("src.database.AsyncSessionLocal", Session)


async def test_the_ai_job_uses_the_fx_grammar_only_when_allowed(monkeypatch, no_runtime_settings):
    redis = FakeRedis()
    seen = []

    async def fake_plan(context, fx_allowed=False):
        seen.append((context, fx_allowed))
        if not fx_allowed:
            return EditPlan.model_validate({"summary": "Nothing.", "operations": []})
        return fx_plan({"type": "add_effect", "kind": "sfx", "start": 2.0, "end": 2.0, "variant": "pop"})

    monkeypatch.setattr(ai, "plan_edit", fake_plan)
    base = {"document": draft().model_dump(), "instruction": "add a pop", "duration": 20}
    redis.data[ai.request_key("a")] = json.dumps(base)
    await ai.ai_edit_job({"redis": redis}, "a")
    assert seen[-1][1] is False and "fx." not in seen[-1][0]
    redis.data[ai.request_key("b")] = json.dumps({**base, "fx_allowed": True, "fx_seed": 7})
    await ai.ai_edit_job({"redis": redis}, "b")
    result = json.loads(redis.data[ai.result_key("b")])
    assert seen[-1][1] is True and "Effect kinds for add_effect" in seen[-1][0]
    assert result["status"] == "completed" and result["changed"] is True
    assert result["document"]["fx"]["cues"][0]["sound"] == "pop" and result["document"]["fx"]["seed"] == 7


async def test_plan_job_publishes_beats(monkeypatch, no_runtime_settings):
    redis = FakeRedis()
    captured = {}

    async def fake_plan_beats(words, clip_seconds, *, timing, language=None, redis=None, emoji_keys=None):
        captured.update(words=words, clip_seconds=clip_seconds, timing=timing)
        return EditBeats(words_hash="h", mood="funny", beats=())

    monkeypatch.setattr("src.edit_fx.beats_llm.plan_beats", fake_plan_beats)
    redis.data[ai.fx_plan_request_key("p")] = json.dumps({"words": words(4), "clip_seconds": 2.0, "user_id": "u"})
    await ai.edit_fx_plan_job({"redis": redis}, "p")
    result = json.loads(redis.data[ai.fx_plan_result_key("p")])
    assert result["status"] == "done" and result["beats"]["mood"] == "funny"
    assert captured["timing"] == "word" and captured["clip_seconds"] == 2.0 and len(captured["words"]) == 4
    assert ai.fx_plan_request_key("p") not in redis.data


async def test_a_failed_plan_job_refunds_its_unit_once(monkeypatch, no_runtime_settings):
    redis = FakeRedis()
    daily = await ai.reserve_edit_plan(redis, "u")
    assert redis.counts[daily] == 1

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.edit_fx.beats_llm.plan_beats", broken)
    redis.data[ai.fx_plan_meta_key("q")] = json.dumps({"owner": "t:c", "queued_at": 0, "daily_key": daily})
    redis.data[ai.fx_plan_request_key("q")] = json.dumps({"words": [], "clip_seconds": 1.0})
    await ai.edit_fx_plan_job({"redis": redis}, "q")
    assert json.loads(redis.data[ai.fx_plan_result_key("q")])["error"]["code"] == "PLAN_FAILED"
    assert redis.counts[daily] == 0
    assert await ai.refund_edit_plan(redis, "q") is False and redis.counts[daily] == 0
    # An expired request fails (and refunds) without calling the model.
    await ai.edit_fx_plan_job({"redis": redis}, "gone")
    assert json.loads(redis.data[ai.fx_plan_result_key("gone")])["status"] == "failed"
