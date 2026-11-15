"""Beat tagging with an LLM (§7.1, §7.2, §7.4, §7.5).

The model tags moments of one clip (hook, emphasis, number, punchline, swear, emotional turn, payoff, build) by WORD
ID; `beats.validate_llm_plan` turns the ids into word-exact beats in S seconds and code decides every cap. The prompt
never mentions intensity or template, so beats are intensity-independent and reusable across settings changes.

Runs through pydantic-ai exactly like `ai.get_broll_agent()`: the agent is cached by model + key signature, uses
`NativeOutput(EditPlanLLM, strict=True)` where the model needs native structured output (else tool output, which is
also what Ollama gets), `output_retries=2`, and `ai._run_transcript_analysis` with `EDIT_PLAN_TIMEOUT_SECONDS`.

`plan_beats` never raises (cancellation passes through): a timeout, provider error, invalid output after retries,
missing key or misconfigured model falls back to `heuristic_beats`, logs `edit_beats_fallback` and sends the same
PostHog event. LLM results are cached in Redis under `fx_beats:{sha256(BEATS_VERSION|model|words_hash)}` for 14 days;
a hit makes no model call. Heuristic results are never cached (a transient outage must not pin them).

Light contract: imports nothing from the video pipeline, so the AI worker and the API can import it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Optional, Sequence

from pydantic import ValidationError
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior

from .. import ai as _ai
from ..config import get_config
from ..emoji_captions import EMOJI_KEYWORD_MAP
from ..observability import get_posthog_client, get_trace_id, get_user_id
from ..runtime_settings import apply_settings_to_process_env
from .beats import (
    KIND_PRIORITY,
    MAX_BEATS,
    MAX_BEATS_TOTAL,
    MAX_PAYOFFS,
    MIN_KIND_GAP,
    heuristic_beats,
    union_swears,
    validate_llm_plan,
    words_hash,
)
from .compat import known_fields, log_dropped
from .constants import LLM_ALLOWED_EMOJI, TONE_EMOJI
from .contracts import BEATS_VERSION, Beat, EditBeats, EditPlanLLM, Timing
from .env import edit_plan_model, edit_plan_timeout

logger = logging.getLogger(__name__)

MAX_PROMPT_WORDS = 900                      # words sent to the model; the rest get heuristic beats (§7.1)
CACHE_TTL_SECONDS = 14 * 24 * 3600          # Redis `fx_beats:*` (§7.4)
CACHE_PREFIX = "fx_beats:"
FALLBACK_EVENT = "edit_beats_fallback"

# Prompt lines are caption-sized: a new line after a sentence end, a pause, or this many words / characters.
LINE_MAX_WORDS = 8
LINE_MAX_CHARS = 42
LINE_PAUSE_SECONDS = 0.6
_SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")
_CLOSERS = "\"'”’)]}"

# §7.2, VERBATIM. Versioned by BEATS_VERSION (bump it when this text, the schema or validation changes). Not part of
# the pinned clip-selection prompts in ai.py.
BEATS_SYSTEM_PROMPT = """You tag moments in one short vertical video so an editor can add sound effects, zooms and animated captions.
You never choose effects. Words are numbered; return word ids, never timestamps.
Beat kinds:
- hook: the opening line that grabs attention (at most 1, inside the first sentence or two)
- emphasis: a word the speaker leans on or that carries the point
- number: a spoken quantity, price, percent, count or year (tag only the number words)
- punchline: a comedic or surprising landing, only when the speaker means it to land as a joke or a twist
- swear: profanity or a slur in ANY language (tag every one, only the swear word itself; never an ordinary word)
- emotional_turn: the mood clearly flips
- payoff: the line the clip builds to (at most 2)
- build: rising tension right before a payoff or punchline
A beat spans 1-8 consecutive words. strength: 3 = the 2-4 biggest moments of the clip, 2 = clear, 1 = minor.
tone: how the moment feels. Sincere, vulnerable or emotional moments (crying, grief, confession, faith, fear) are
never funny, smug or awkward and never a punchline: tag them emphasis, emotional_turn or payoff with tone sad,
wholesome or neutral.
emoji: one emoji only when it literally depicts a concrete thing the word names (money -> 💰, car -> 🚗, fire -> 🔥),
else "". Never pointing hands, faces, people or symbols for ideas.
label: a 0-4 word sticker reacting to the moment (e.g. "WAIT WHAT"), in the transcript's language, else "".
sensitivity: "serious" when the clip is about grief, illness, violence, abuse or tragedy, else "normal".
mood: the background music that fits the whole clip:
- hype: positive excitement and energy (a win, a flex, big news, celebration)
- aggressive: heated anger at someone or something (a rant, calling someone out, confrontation, roasting, hard
  trash talk, fight energy)
- dramatic: tension, high stakes or a story building to a reveal, without anger
- funny: jokes, silliness, playful teasing
- inspiring: hope, a lesson, a comeback, encouragement
- chill: calm, relaxed talk or explanation
- dark: eerie, ominous or disturbing (crime, horror, unsettling facts)
Sincere, vulnerable or grieving moments are never aggressive, even when the speaker is upset.
hook_last_word: id of the last word of the hook, -1 if none.
reactions: up to 3 meme-reaction GIF search ideas (2-4 words) on the word they react to, only for genuinely
funny or shocking moments; [] otherwise.
Tag generously; the editor filters by intensity. Never tag filler. Never invent words."""


class BeatsUnavailable(RuntimeError):
    """The beat model cannot be called (no key, unsupported or misconfigured model). `reason` goes to analytics."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# ----------------------------------------------------------------------------- prompt (§7.2)


def _word_text(word: dict) -> str:
    return " ".join(str(word.get("text", "") or "").split())


def _float(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def _ends_sentence(text: str) -> bool:
    return text.rstrip(_CLOSERS).endswith(_SENTENCE_END)


def build_beats_prompt(words: Sequence[dict], clip_seconds: float, language: str | None) -> str:
    """The user prompt: clip length, language, then numbered words in caption-sized lines prefixed by their start.

    `[12.4s] 41:so 42:I 43:literally 44:made 45:$2,000,000`. Word ids are positions in `words` (the caller passes at
    most MAX_PROMPT_WORDS). Never includes intensity or template.
    """
    lang = " ".join(str(language or "").split())[:16] or "unknown"
    lines: list[str] = []
    current: list[str] = []
    line_start = 0.0
    chars = 0
    previous_end: Optional[float] = None
    previous_text = ""
    for index, word in enumerate(words):
        text = _word_text(word)
        if not text:
            continue
        start = _float(word.get("start"))
        if current and (
            len(current) >= LINE_MAX_WORDS
            or chars + 1 + len(text) > LINE_MAX_CHARS
            or _ends_sentence(previous_text)
            or (previous_end is not None and start - previous_end >= LINE_PAUSE_SECONDS)
        ):
            lines.append(f"[{line_start:.1f}s] " + " ".join(current))
            current, chars = [], 0
        if not current:
            line_start = start
        token = f"{index}:{text}"
        current.append(token)
        chars += len(text) + (1 if chars else 0)
        previous_end = max(start, _float(word.get("end")))
        previous_text = text
    if current:
        lines.append(f"[{line_start:.1f}s] " + " ".join(current))
    header = f"Clip length: {max(0.0, _float(clip_seconds)):.1f} s. Language: {lang}."
    return header + "\n" + "\n".join(lines)


# ----------------------------------------------------------------------------- agent (§7.1)

_beats_agent: Optional[Agent[None, EditPlanLLM]] = None
_beats_agent_signature: Optional[tuple[str | None, ...]] = None


def _output_type(model_name: str) -> Any:
    """Native strict JSON for models that require it (Sonnet 5.5, OpenRouter), tool output otherwise (Ollama too)."""
    return NativeOutput(EditPlanLLM, strict=True) if _ai._uses_native_output(model_name) else EditPlanLLM


def _build_model(runtime_config: Any, model_name: str) -> Any:
    """The pydantic-ai model for `model_name` (separate so tests can substitute a FunctionModel)."""
    return _ai._build_transcript_model(runtime_config, model_name)


def _config_signature(runtime_config: Any, model_name: str) -> tuple[str | None, ...]:
    return (
        model_name,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )


def get_beats_agent(model_name: str | None = None) -> Agent[None, EditPlanLLM]:
    """Get or create the beat-tagging agent. Raises BeatsUnavailable when the model cannot be called."""
    global _beats_agent, _beats_agent_signature
    runtime_config = get_config()
    model_name = model_name or edit_plan_model()
    config_error = _ai._get_missing_llm_key_error(model_name, runtime_config)
    if config_error:
        raise BeatsUnavailable("no_key", config_error)
    signature = _config_signature(runtime_config, model_name)
    if _beats_agent is None or _beats_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        try:
            model = _build_model(runtime_config, model_name)
        except RuntimeError as error:
            raise BeatsUnavailable("config", str(error)) from None
        _beats_agent = Agent[None, EditPlanLLM](
            model=model,
            output_type=_output_type(model_name),
            system_prompt=BEATS_SYSTEM_PROMPT,
            output_retries=2,
        )
        _beats_agent_signature = signature
    return _beats_agent


# ----------------------------------------------------------------------------- cache (§7.4)


def cache_key(model_name: str, words_digest: str) -> str:
    blob = f"{BEATS_VERSION}|{model_name}|{words_digest}"
    return CACHE_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def _cache_get(redis: Any, key: str, digest: str) -> Optional[EditBeats]:
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception as error:  # noqa: BLE001 - the cache is an optimisation, never a failure
        logger.warning("fx_beats_cache_read_failed error=%s", type(error).__name__)
        return None
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        clean, dropped = known_fields(EditBeats, data)
        log_dropped("fx_beats_cache", dropped)
        beats = EditBeats.model_validate(clean)
    except (ValueError, TypeError, ValidationError) as error:
        logger.warning("fx_beats_cache_unreadable error=%s", type(error).__name__)
        return None
    if beats.words_hash != digest or beats.source != "llm":
        return None
    return beats


async def _cache_set(redis: Any, key: str, beats: EditBeats) -> None:
    if redis is None:
        return
    try:
        payload = json.dumps(beats.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        await redis.set(key, payload, ex=CACHE_TTL_SECONDS)
    except Exception as error:  # noqa: BLE001
        logger.warning("fx_beats_cache_write_failed error=%s", type(error).__name__)


# ----------------------------------------------------------------------------- fallback (§7.5)


def _provider(model_name: str) -> str:
    return (model_name.split(":", 1)[0] if model_name else "").strip().lower()[:32]


def _capture_fallback(reason: str, *, model_name: str, word_count: int) -> None:
    """Log and send PostHog `edit_beats_fallback {reason}`. No transcript content. Never raises."""
    logger.warning("edit_beats_fallback reason=%s provider=%s words=%s", reason, _provider(model_name), word_count)
    try:
        client = get_posthog_client()
        if client is None:
            return
        user_id = get_user_id()
        properties: dict[str, Any] = {
            "reason": reason,
            "provider": _provider(model_name),
            "words": word_count,
            "beats_version": BEATS_VERSION,
        }
        trace_id = get_trace_id()
        if trace_id and trace_id != "-":
            properties["trace_id"] = trace_id
        if not user_id:
            properties["$process_person_profile"] = False   # worker jobs have no bound user: no person profile
        client.capture(FALLBACK_EVENT, distinct_id=str(user_id or "supoclip-worker"), properties=properties)
    except Exception as error:  # noqa: BLE001 - analytics never breaks planning
        logger.warning("Could not capture %s: %s", FALLBACK_EVENT, type(error).__name__)


def _default_emoji_keys() -> frozenset[str]:
    try:
        from .assets import load_index

        return load_index().emoji_keys
    except Exception as error:  # noqa: BLE001 - a broken asset tree must not cost the beats
        logger.warning("fx_emoji_keys_unavailable error=%s", type(error).__name__)
        return frozenset(EMOJI_KEYWORD_MAP.values()) | frozenset(TONE_EMOJI.values()) | frozenset(LLM_ALLOWED_EMOJI)


def _canonical_tags(out: EditPlanLLM, emoji_keys: frozenset[str]) -> EditPlanLLM:
    """Map each tag's emoji onto its atlas key (NFC, U+FE0F anywhere, ZWJ sequences); unknown ones stay as given."""
    from .assets import canonical_emoji

    tags = []
    changed = False
    for tag in out.beats:
        canonical = canonical_emoji(tag.emoji, emoji_keys) if tag.emoji else None
        if canonical and canonical != tag.emoji:
            tags.append(tag.model_copy(update={"emoji": canonical}))
            changed = True
        else:
            tags.append(tag)
    return out.model_copy(update={"beats": tags}) if changed else out


# ----------------------------------------------------------------------------- long clips (§7.1)


def _beat_order(beat: Beat) -> tuple:
    return (beat.start, -KIND_PRIORITY.get(beat.kind, 0), beat.end, beat.id)


def _renumber(beats: EditBeats) -> EditBeats:
    ordered = sorted(beats.beats, key=_beat_order)
    return beats.model_copy(update={"beats": tuple(b.model_copy(update={"id": f"b{i}"}) for i, b in enumerate(ordered))})


def _with_heuristic_tail(llm: EditBeats, words: Sequence[dict], timing: Timing) -> EditBeats:
    """Beats for words past MAX_PROMPT_WORDS: the heuristic's beats there, under the same caps as the model's."""
    cutoff = _float(words[MAX_PROMPT_WORDS].get("start"))
    tail = [
        b for b in heuristic_beats(words, timing=timing).beats
        if b.start >= cutoff - 1e-9 and b.kind not in ("hook", "swear")   # swears: union below; one hook, up front
    ]
    kept = [b for b in llm.beats]
    payoffs = sum(1 for b in kept if b.kind == "payoff")
    budget = max(0, MAX_BEATS - sum(1 for b in kept if b.kind != "swear"))
    for beat in sorted(tail, key=lambda b: (-KIND_PRIORITY.get(b.kind, 0), -b.strength, b.start)):
        if budget <= 0:
            break
        if beat.kind == "payoff":
            if payoffs >= MAX_PAYOFFS:
                continue
            payoffs += 1
        if any(o.kind == beat.kind and abs(o.start - beat.start) < MIN_KIND_GAP - 1e-9 for o in kept):
            continue
        kept.append(beat.model_copy(update={"id": f"h{len(kept)}"}))
        budget -= 1
    merged = llm.model_copy(update={
        "beats": tuple(sorted(kept, key=_beat_order)[:MAX_BEATS_TOTAL]),
        "words_hash": words_hash(words),
    })
    return _renumber(union_swears(_renumber(merged), words))


# ----------------------------------------------------------------------------- plan_beats


def _stamp(beats: EditBeats, timing: Timing) -> EditBeats:
    if not beats.beats and beats.timing == "none":
        return beats
    return beats if beats.timing == timing else beats.model_copy(update={"timing": timing})


async def plan_beats(words: Sequence[dict], clip_seconds: float, *, timing: Timing, language: str | None = None,
                     redis=None, emoji_keys: frozenset[str] | None = None) -> EditBeats:
    """Beats for one clip's words (S == E seconds, list position = word id). Never raises; heuristic on any failure.

    `timing` is the quality from `words_with_timing`: "none" gives no beats and no call; "approx" plans normally and
    is stamped on the result (the resolver then drops bleep audio). `timeline_hash` stays "" (the caller stamps it).
    """
    words = [w for w in words or () if isinstance(w, dict)]
    if timing == "none" or not words:
        return heuristic_beats(words, timing="none")
    if not any(_word_text(w) for w in words):
        # Nothing spoken to tag (e.g. a music-only clip with blank tokens): no call, no fallback event.
        return heuristic_beats(words, timing=timing)

    model_name = ""
    try:
        model_name = edit_plan_model()
    except Exception as error:  # noqa: BLE001 - a broken config still gets beats
        logger.warning("edit_plan_model_unavailable error=%s", type(error).__name__)

    digest = words_hash(words)
    key = cache_key(model_name, digest) if model_name else ""
    if key:
        cached = await _cache_get(redis, key, digest)
        if cached is not None:
            return _stamp(cached, timing)

    reason = ""
    beats: Optional[EditBeats] = None
    timeout = edit_plan_timeout()
    started = time.monotonic()
    try:
        if not model_name:
            raise BeatsUnavailable("no_model", "no beat model configured")
        agent = get_beats_agent(model_name)
        prompt_words = words[:MAX_PROMPT_WORDS]
        prompt = build_beats_prompt(prompt_words, clip_seconds, language)
        # Our own deadline sits just past the helper's so a slow provider is reported as "timeout", not "error".
        async with asyncio.timeout(timeout + 1.0):
            result = await _ai._run_transcript_analysis(agent, prompt, timeout)
        out = result.output
        if not isinstance(out, EditPlanLLM):
            raise UnexpectedModelBehavior(f"unexpected output type {type(out).__name__}")
        keys = emoji_keys if emoji_keys is not None else _default_emoji_keys()
        beats = validate_llm_plan(_canonical_tags(out, keys), prompt_words, model=model_name, emoji_keys=keys)
        if len(words) > MAX_PROMPT_WORDS:
            beats = _with_heuristic_tail(beats, words, timing)
        beats = _stamp(beats, timing)
    except BeatsUnavailable as error:
        reason = error.reason
    except (UnexpectedModelBehavior, ValidationError):
        reason = "invalid_output"
    except TimeoutError:
        reason = "timeout"
    except Exception as error:  # noqa: BLE001 - never raises (CancelledError is a BaseException and passes through)
        reason = "timeout" if time.monotonic() - started >= timeout - 0.05 else "error"
        logger.info("edit_beats_llm_failed error=%s", type(error).__name__)

    if beats is None:
        _capture_fallback(reason or "error", model_name=model_name, word_count=len(words))
        return heuristic_beats(words, timing=timing)

    if key:
        await _cache_set(redis, key, beats)
    return beats
