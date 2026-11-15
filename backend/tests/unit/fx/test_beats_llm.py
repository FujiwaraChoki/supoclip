"""WP10: beat tagging through pydantic-ai (§7.1, §7.2, §7.4, §7.5). No network: models are FunctionModels."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic_ai import NativeOutput
from pydantic_ai.messages import ModelResponse, SystemPromptPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.edit_fx import beats_llm
from src.edit_fx.beats import words_hash
from src.edit_fx.contracts import BEATS_VERSION, EditBeats, EditPlanLLM

SENTENCE = "So I literally made $2,000,000 in one year. That is insane! Nobody believed me."


def make_words(text: str = SENTENCE, *, start: float = 0.0, step: float = 0.4, repeat: int = 1) -> list[dict]:
    tokens = text.split() * repeat
    return [
        {"id": f"w{i}", "text": tok, "start": round(start + i * step, 3), "end": round(start + i * step + 0.3, 3),
         "src_start": round(100 + start + i * step, 3), "src_end": round(100 + start + i * step + 0.3, 3)}
        for i, tok in enumerate(tokens)
    ]


def tag(kind="emphasis", first=0, last=0, strength=2, tone="neutral", emoji="", label=""):
    return {"kind": kind, "first_word": first, "last_word": last, "strength": strength, "tone": tone,
            "emoji": emoji, "label": label}


def plan(beats=(), *, mood="funny", sensitivity="normal", hook_last_word=-1, reactions=()):
    return {"mood": mood, "sensitivity": sensitivity, "hook_last_word": hook_last_word, "beats": list(beats),
            "reactions": list(reactions)}


class FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value.encode() if isinstance(value, str) else value
        self.ttl[key] = ex


class FakePostHog:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def capture(self, event, distinct_id, properties):
        self.events.append((event, distinct_id, properties))


def _config(**keys):
    base = dict(openai_api_key="", openrouter_api_key="", openrouter_fallback_model="openai/gpt-6-luna",
                google_api_key="", anthropic_api_key="", ollama_base_url="", ollama_api_key="")
    base.update(keys)
    return SimpleNamespace(**base, as_runtime_settings=lambda: {},
                           resolve_ollama_base_url=lambda: "http://127.0.0.1:11434/v1/")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(beats_llm, "_beats_agent", None)
    monkeypatch.setattr(beats_llm, "_beats_agent_signature", None)
    monkeypatch.setattr(beats_llm, "apply_settings_to_process_env", lambda settings: None)
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config(google_api_key="test-key"))
    monkeypatch.setenv("EDIT_PLAN_LLM", "google-gla:gemini-test")
    monkeypatch.delenv("EDIT_PLAN_TIMEOUT_SECONDS", raising=False)
    posthog = FakePostHog()
    monkeypatch.setattr(beats_llm, "get_posthog_client", lambda: posthog)
    return posthog


def use_function_model(monkeypatch, responder):
    """Route the agent to a FunctionModel. `responder(prompt, info)` returns the plan dict (or raises / sleeps)."""
    calls: list[dict] = []

    async def function(messages, info: AgentInfo) -> ModelResponse:
        system = "".join(p.content for m in messages for p in getattr(m, "parts", ()) if isinstance(p, SystemPromptPart))
        user = "".join(p.content for m in messages for p in getattr(m, "parts", ()) if isinstance(p, UserPromptPart))
        calls.append({"system": system, "user": user, "info": info, "messages": messages})
        out = responder(user, info)
        if asyncio.iscoroutine(out):
            out = await out
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

    built: list[str] = []

    def build(runtime_config, model_name):
        built.append(model_name)
        return FunctionModel(function)

    monkeypatch.setattr(beats_llm, "_build_model", build)
    return calls, built


# ----------------------------------------------------------------------------- happy path


async def test_function_model_happy_path(monkeypatch, env):
    words = make_words()
    calls, built = use_function_model(monkeypatch, lambda user, info: plan(
        [tag("hook", 0, 8, 3, "shocked"), tag("number", 4, 4, 3, "hype", "💰"), tag("punchline", 9, 11, 2, "funny",
         label="WAIT WHAT")],
        hook_last_word=8, reactions=[{"word": 11, "query": "mind blown"}]))

    beats = await beats_llm.plan_beats(words, 15.0, timing="word", language="en")

    assert built == ["google-gla:gemini-test"] and len(calls) == 1
    assert calls[0]["system"] == beats_llm.BEATS_SYSTEM_PROMPT
    assert calls[0]["user"].startswith("Clip length: 15.0 s. Language: en.\n[0.0s] 0:So 1:I 2:literally")
    assert beats.source == "llm" and beats.model == "google-gla:gemini-test" and beats.mood == "funny"
    assert beats.words_hash == words_hash(words) and beats.timing == "word" and beats.timeline_hash == ""
    kinds = {b.kind: b for b in beats.beats}
    assert set(kinds) == {"hook", "number", "punchline"}
    number = kinds["number"]
    assert (number.start, number.end, number.text) == (words[4]["start"], words[4]["end"], "$2,000,000")
    assert number.value == 2_000_000 and number.prefix == "$" and number.emoji == "💰"
    assert (number.src_start, number.src_end) == (words[4]["src_start"], words[4]["src_end"])
    assert kinds["punchline"].label == "WAIT WHAT" and kinds["punchline"].tone == "funny"
    assert beats.hook_end == words[8]["end"]
    assert [b.id for b in beats.beats] == [f"b{i}" for i in range(len(beats.beats))]
    assert [b.start for b in beats.beats] == sorted(b.start for b in beats.beats)
    assert beats.reactions[0].query == "mind blown" and beats.reactions[0].at == words[11]["start"]
    assert not env.events


async def test_invalid_ids_are_clamped_and_swapped(monkeypatch):
    words = make_words()
    n = len(words)
    use_function_model(monkeypatch, lambda user, info: plan(
        [tag("emphasis", -7, -2, 9), tag("payoff", 500, n - 3, 0), tag("build", 3, 40, 2)], hook_last_word=999,
        reactions=[{"word": -4, "query": "  shocked   cat "}]))

    beats = await beats_llm.plan_beats(words, 15.0, timing="word")

    by_kind = {b.kind: b for b in beats.beats}
    assert by_kind["emphasis"].start == words[0]["start"] and by_kind["emphasis"].strength == 3
    payoff = by_kind["payoff"]   # swapped to (n-3, n-1), strength clamped up to 1
    assert (payoff.start, payoff.end, payoff.strength) == (words[n - 3]["start"], words[n - 1]["end"], 1)
    build = by_kind["build"]     # span cut to 8 words
    assert (build.start, build.end) == (words[3]["start"], words[10]["end"]) and len(build.words) == 8
    assert beats.hook_end == words[n - 1]["end"]
    assert beats.reactions[0].at == words[0]["start"] and beats.reactions[0].query == "shocked cat"


async def test_emoji_outside_atlas_is_dropped_and_canonicalised(monkeypatch):
    words = make_words("The unicorn ran past my heart today.")
    keys = frozenset({"❤️", "🔥"})
    use_function_model(monkeypatch, lambda user, info: plan([
        tag("emphasis", 1, 1, 2, emoji="🦄"),        # not an atlas key, no keyword mapping in keys -> ""
        tag("emphasis", 5, 5, 2, emoji="❤"),         # missing U+FE0F -> the atlas key
    ]))

    beats = await beats_llm.plan_beats(words, 5.0, timing="word", emoji_keys=keys)

    emoji = {b.text: b.emoji for b in beats.beats}
    assert emoji["unicorn"] == "" and emoji["heart"] == "❤️"


async def test_default_emoji_keys_come_from_the_atlas(monkeypatch):
    from src.edit_fx.assets import load_index

    words = make_words("We made so much money")
    use_function_model(monkeypatch, lambda user, info: plan([tag("emphasis", 4, 4, 2, emoji="🦄"),
                                                             tag("emphasis", 1, 1, 2, emoji="💰")]))
    beats = await beats_llm.plan_beats(words, 3.0, timing="word")
    keys = load_index().emoji_keys
    assert all(b.emoji == "" or b.emoji in keys for b in beats.beats)
    assert {b.text: b.emoji for b in beats.beats}["made"] == "💰"


async def test_approx_timing_is_stamped(monkeypatch):
    words = make_words()
    use_function_model(monkeypatch, lambda user, info: plan([tag("emphasis", 2, 2)]))
    beats = await beats_llm.plan_beats(words, 15.0, timing="approx")
    assert beats.source == "llm" and beats.timing == "approx"


# ----------------------------------------------------------------------------- fallbacks


async def test_timeout_falls_back_to_heuristic_with_reason(monkeypatch, env):
    monkeypatch.setenv("EDIT_PLAN_TIMEOUT_SECONDS", "0.2")

    async def slow(user, info):
        await asyncio.sleep(5)
        return plan()

    use_function_model(monkeypatch, slow)
    words = make_words()
    loop = asyncio.get_running_loop()
    started = loop.time()
    beats = await beats_llm.plan_beats(words, 15.0, timing="word")
    assert loop.time() - started < 2.0
    assert beats.source == "heuristic" and beats.model == "" and beats.beats
    assert [(e[0], e[2]["reason"]) for e in env.events] == [("edit_beats_fallback", "timeout")]
    assert env.events[0][2]["provider"] == "google-gla" and env.events[0][2]["words"] == len(words)
    assert env.events[0][2]["$process_person_profile"] is False


async def test_no_key_uses_heuristic_without_a_call(monkeypatch, env):
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config())   # no GOOGLE_API_KEY
    calls, built = use_function_model(monkeypatch, lambda user, info: plan())
    redis = FakeRedis()

    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="word", redis=redis)

    assert beats.source == "heuristic" and not calls and not built
    assert [e[2]["reason"] for e in env.events] == ["no_key"]
    assert redis.store == {}    # heuristic results are never cached


async def test_invalid_output_after_retries_falls_back(monkeypatch, env):
    calls, _ = use_function_model(monkeypatch, lambda user, info: {"mood": "elated", "beats": "nope"})
    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="word")
    assert beats.source == "heuristic"
    assert len(calls) == 3       # first try + output_retries=2
    assert [e[2]["reason"] for e in env.events] == ["invalid_output"]


async def test_provider_error_falls_back(monkeypatch, env):
    def boom(user, info):
        raise ValueError("provider exploded")

    use_function_model(monkeypatch, boom)
    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="word")
    assert beats.source == "heuristic"
    assert [e[2]["reason"] for e in env.events] == ["error"]


async def test_misconfigured_model_falls_back(monkeypatch, env):
    monkeypatch.setenv("EDIT_PLAN_LLM", "openrouter:no-vendor")
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config(openrouter_api_key="k"))
    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="word")
    assert beats.source == "heuristic" and [e[2]["reason"] for e in env.events] == ["no_key"]


async def test_timing_none_returns_no_beats_and_no_call(monkeypatch, env):
    calls, _ = use_function_model(monkeypatch, lambda user, info: plan())
    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="none")
    assert beats.beats == () and beats.timing == "none" and not calls and not env.events
    empty = await beats_llm.plan_beats([], 15.0, timing="word")
    assert empty.beats == () and empty.timing == "none"


async def test_blank_words_make_no_call(monkeypatch, env):
    calls, _ = use_function_model(monkeypatch, lambda user, info: plan())
    blank = [{"text": " ", "start": 0.0, "end": 0.4}, {"text": "", "start": 0.5, "end": 0.9}]
    beats = await beats_llm.plan_beats(blank, 1.0, timing="word")
    assert beats.source == "heuristic" and not calls and not env.events


async def test_cancellation_passes_through(monkeypatch):
    async def slow(user, info):
        await asyncio.sleep(5)
        return plan()

    use_function_model(monkeypatch, slow)
    task = asyncio.create_task(beats_llm.plan_beats(make_words(), 15.0, timing="word"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_posthog_failure_never_breaks_planning(monkeypatch):
    class Broken:
        def capture(self, *args, **kwargs):
            raise RuntimeError("down")

    monkeypatch.setattr(beats_llm, "get_posthog_client", lambda: Broken())
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config())
    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="word")
    assert beats.source == "heuristic"


async def test_fallback_event_uses_the_bound_user(monkeypatch, env):
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config())
    monkeypatch.setattr(beats_llm, "get_user_id", lambda: "user-123")
    await beats_llm.plan_beats(make_words(), 15.0, timing="word")
    event, distinct_id, props = env.events[0]
    assert distinct_id == "user-123" and "$process_person_profile" not in props
    assert set(props) <= {"reason", "provider", "words", "beats_version", "trace_id"}   # no transcript content


# ----------------------------------------------------------------------------- output mode


def test_output_type_native_only_where_required():
    assert beats_llm._output_type("ollama:qwen3:8b") is EditPlanLLM
    assert beats_llm._output_type("google-gla:gemini-3-flash-preview") is EditPlanLLM
    native = beats_llm._output_type("anthropic:claude-sonnet-5-5")
    assert isinstance(native, NativeOutput) and native.strict is True and native.outputs is EditPlanLLM
    assert isinstance(beats_llm._output_type("openrouter:anthropic/claude-sonnet-5.5"), NativeOutput)


async def test_ollama_path_uses_tool_output(monkeypatch):
    monkeypatch.setenv("EDIT_PLAN_LLM", "ollama:qwen3:8b")
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config())    # Ollama needs no key
    calls, built = use_function_model(monkeypatch, lambda user, info: plan([tag("emphasis", 2, 2)]))
    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="word")
    assert built == ["ollama:qwen3:8b"] and beats.source == "llm" and beats.model == "ollama:qwen3:8b"
    info = calls[0]["info"]
    assert info.model_request_parameters.output_mode == "tool" and info.output_tools


def test_ollama_agent_builds_a_real_ollama_model(monkeypatch):
    from pydantic_ai.models.ollama import OllamaModel

    monkeypatch.setenv("EDIT_PLAN_LLM", "ollama:qwen3:8b")
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config())
    agent = beats_llm.get_beats_agent()
    assert isinstance(agent.model, OllamaModel)
    assert beats_llm.get_beats_agent() is agent      # cached by model + keys signature
    monkeypatch.setattr(beats_llm, "get_config", lambda: _config(ollama_api_key="other"))
    assert beats_llm.get_beats_agent() is not agent


def test_llm_schema_has_no_optional_fields():
    """Grammar-size rule: every object property is required, no defaults, no nullable unions."""
    schema = EditPlanLLM.model_json_schema()
    objects = [schema] + list(schema.get("$defs", {}).values())

    def walk(node):
        if isinstance(node, dict):
            assert "default" not in node
            for union in ("anyOf", "oneOf"):
                assert not any(isinstance(o, dict) and o.get("type") == "null" for o in node.get(union, ()))
            assert node.get("type") != "null"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for obj in objects:
        if obj.get("type") == "object":
            assert set(obj.get("required", ())) == set(obj["properties"]), obj.get("title")
            assert obj.get("additionalProperties") is False
    walk(schema)
    assert {o.get("title") for o in objects} >= {"EditPlanLLM", "BeatTagLLM", "ReactionLLM"}


# ----------------------------------------------------------------------------- cache (§7.4)


async def test_redis_hit_means_no_agent_call(monkeypatch):
    words = make_words()
    calls, _ = use_function_model(monkeypatch, lambda user, info: plan([tag("emphasis", 2, 2, 3)], mood="dark"))
    redis = FakeRedis()

    first = await beats_llm.plan_beats(words, 15.0, timing="word", redis=redis)
    key = beats_llm.cache_key("google-gla:gemini-test", words_hash(words))
    assert list(redis.store) == [key] and redis.ttl[key] == 14 * 24 * 3600
    expected = "fx_beats:" + hashlib.sha256(f"{BEATS_VERSION}|google-gla:gemini-test|{words_hash(words)}".encode()).hexdigest()
    assert key == expected

    second = await beats_llm.plan_beats(words, 15.0, timing="word", redis=redis)
    assert len(calls) == 1 and second == first and second.mood == "dark"


async def test_cache_is_keyed_by_model_and_ignores_bad_entries(monkeypatch):
    words = make_words()
    calls, _ = use_function_model(monkeypatch, lambda user, info: plan([tag("emphasis", 2, 2)]))
    redis = FakeRedis()
    await beats_llm.plan_beats(words, 15.0, timing="word", redis=redis)
    monkeypatch.setenv("EDIT_PLAN_LLM", "google-gla:gemini-other")
    await beats_llm.plan_beats(words, 15.0, timing="word", redis=redis)
    assert len(calls) == 2 and len(redis.store) == 2

    key = beats_llm.cache_key("google-gla:gemini-other", words_hash(words))
    redis.store[key] = b"{not json"
    await beats_llm.plan_beats(words, 15.0, timing="word", redis=redis)
    assert len(calls) == 3


async def test_cache_read_is_forward_lenient(monkeypatch):
    words = make_words()
    calls, _ = use_function_model(monkeypatch, lambda user, info: plan())
    redis = FakeRedis()
    stored = EditBeats(words_hash=words_hash(words), model="google-gla:gemini-test", mood="chill").model_dump(mode="json")
    stored["future_key"] = 1
    redis.store[beats_llm.cache_key("google-gla:gemini-test", words_hash(words))] = json.dumps(stored).encode()
    beats = await beats_llm.plan_beats(words, 15.0, timing="word", redis=redis)
    assert not calls and beats.mood == "chill"


async def test_redis_errors_are_ignored(monkeypatch):
    class Down:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def set(self, *args, **kwargs):
            raise ConnectionError("redis down")

    calls, _ = use_function_model(monkeypatch, lambda user, info: plan([tag("emphasis", 2, 2)]))
    beats = await beats_llm.plan_beats(make_words(), 15.0, timing="word", redis=Down())
    assert beats.source == "llm" and len(calls) == 1


# ----------------------------------------------------------------------------- long clips


async def test_words_cap_at_900_with_heuristic_tail(monkeypatch):
    sentence = "This is the craziest money story ever! "
    words = make_words(sentence * 160, step=0.35)       # 1120 words
    assert len(words) > 1000
    calls, _ = use_function_model(monkeypatch, lambda user, info: plan(
        [tag("hook", 0, 6, 3), tag("payoff", 890, 893, 3)], hook_last_word=6))

    beats = await beats_llm.plan_beats(words, words[-1]["end"], timing="word")

    user = calls[0]["user"]
    assert " 899:" in user and " 900:" not in user and "900:" not in user.split()
    cutoff = words[900]["start"]
    llm_beats = [b for b in beats.beats if b.start < cutoff]
    tail = [b for b in beats.beats if b.start >= cutoff]
    assert any(b.kind == "payoff" for b in llm_beats) and any(b.kind == "hook" for b in llm_beats)
    assert tail and all(b.source == "heuristic" for b in tail)
    assert sum(1 for b in beats.beats if b.kind == "hook") == 1
    assert sum(1 for b in beats.beats if b.kind == "payoff") <= 2
    assert sum(1 for b in beats.beats if b.kind != "swear") <= 40
    assert beats.words_hash == words_hash(words) and beats.source == "llm"
    assert [b.id for b in beats.beats] == [f"b{i}" for i in range(len(beats.beats))]
    assert [b.start for b in beats.beats] == sorted(b.start for b in beats.beats)


async def test_swears_past_the_cap_are_still_tagged(monkeypatch):
    words = make_words("we talk calmly here " * 230 + "oh shit that hurt", step=0.3)
    use_function_model(monkeypatch, lambda user, info: plan())
    beats = await beats_llm.plan_beats(words, words[-1]["end"], timing="word")
    swear = [b for b in beats.beats if b.kind == "swear"]
    assert [b.text for b in swear] == ["shit"]


# ----------------------------------------------------------------------------- prompt (§7.2)

SYSTEM_PROMPT_SHA256 = "7e0afc60"   # first 8 hex of sha256(BEATS_SYSTEM_PROMPT); bump BEATS_VERSION when it changes


def test_system_prompt_snapshot():
    text = beats_llm.BEATS_SYSTEM_PROMPT
    assert hashlib.sha256(text.encode()).hexdigest()[:8] == SYSTEM_PROMPT_SHA256, (
        "the beat prompt changed: bump BEATS_VERSION and update this snapshot")
    assert BEATS_VERSION == "beats-v3"
    assert text.startswith("You tag moments in one short vertical video")
    assert text.endswith("Never tag filler. Never invent words.")
    assert "intensity" in text and "template" not in text.lower()


def test_system_prompt_defines_every_mood():
    from src.edit_fx.contracts import MOODS
    text = beats_llm.BEATS_SYSTEM_PROMPT
    for mood in MOODS:
        assert f"- {mood}: " in text, mood
    aggressive = next(line for line in text.splitlines() if line.startswith("- aggressive: "))
    assert "rant" in aggressive and "calling someone out" in aggressive
    assert "Sincere, vulnerable or grieving moments are never aggressive" in text


async def test_the_model_may_tag_a_clip_aggressive(monkeypatch):
    words = make_words("who approved this garbage halftime show honestly")
    use_function_model(monkeypatch, lambda user, info: plan(mood="aggressive"))
    beats = await beats_llm.plan_beats(words, words[-1]["end"], timing="word")
    assert beats.source == "llm" and beats.mood == "aggressive"


def test_user_prompt_snapshot():
    words = [
        {"text": "so", "start": 12.4, "end": 12.5}, {"text": "I", "start": 12.5, "end": 12.6},
        {"text": "literally", "start": 12.6, "end": 13.0}, {"text": "made", "start": 13.0, "end": 13.2},
        {"text": "$2,000,000.", "start": 13.2, "end": 14.0}, {"text": "No", "start": 15.1, "end": 15.3},
        {"text": "joke", "start": 15.3, "end": 15.6}, {"text": "", "start": 15.6, "end": 15.6},
        {"text": "and", "start": 15.6, "end": 15.7}, {"text": "then", "start": 15.7, "end": 15.8},
        {"text": "I", "start": 15.8, "end": 15.9}, {"text": "lost", "start": 15.9, "end": 16.1},
        {"text": "it", "start": 16.1, "end": 16.2}, {"text": "all", "start": 16.2, "end": 16.4},
        {"text": "in", "start": 16.4, "end": 16.5}, {"text": "a", "start": 16.5, "end": 16.6},
        {"text": "week", "start": 16.6, "end": 17.0},
    ]
    assert beats_llm.build_beats_prompt(words, 41.25, "en") == (
        "Clip length: 41.2 s. Language: en.\n"
        "[12.4s] 0:so 1:I 2:literally 3:made 4:$2,000,000.\n"
        "[15.1s] 5:No 6:joke 8:and 9:then 10:I 11:lost 12:it 13:all\n"
        "[16.4s] 14:in 15:a 16:week"
    )
    assert beats_llm.build_beats_prompt(words[:2], 3, None).startswith("Clip length: 3.0 s. Language: unknown.\n")


def test_prompt_never_mentions_intensity_or_template():
    prompt = beats_llm.build_beats_prompt(make_words(), 15.0, "de")
    assert "intensity" not in prompt.lower() and "template" not in prompt.lower()
