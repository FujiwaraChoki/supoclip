"""Beats: validation of the LLM's tags, the heuristic fallback, the swear union and re-anchoring (§7.3, §7.5, §13.1).

A beat is a tagged moment (kind, word span, strength, tone) in S seconds. The model only ever names word ids; every
time here comes from word times. Beats are independent of intensity and template: the resolver decides what to do
with them.

Words are dicts `{"text", "start", "end"}` in S seconds (E == S in generation), optionally with `"id"` and the word's
A times (`"src_start"`, `"src_end"`, absolute seconds in the source video). The position of a word in the list is its
word id in the beat prompt.

Pure: no I/O besides reading the bundled `swears.txt` once. Imports nothing from the video pipeline, so the AI worker
can use it.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

from ..emoji_captions import (
    EMOJI_KEYWORD_MAP,
    POWER_WORDS,
    _lookup_emoji,
    annotate_caption_words,
    normalize_token,
)
from .contracts import Beat, EditBeats, EditPlanLLM, ReactionIdea, Timing
from .numbers import find_number_spans, parse_spoken_number

# Beat cap priority (§8.2): payoff > hook > punchline > number > swear > emotional_turn > build > emphasis.
KIND_PRIORITY: dict[str, int] = {
    "payoff": 7, "hook": 6, "punchline": 5, "number": 4, "swear": 3, "emotional_turn": 2, "build": 1, "emphasis": 0,
}
MAX_BEATS = 40               # LLM/heuristic beats per clip (swears are never dropped by this cap)
MAX_BEATS_TOTAL = 200        # EditBeats.beats max_length
MAX_SPAN_WORDS = 8           # a beat spans 1-8 consecutive words
MAX_PAYOFFS = 2
HOOK_SENTENCES = 2           # a hook must start within the first two sentences
HEURISTIC_HOOK_WORDS = 12    # heuristic hook: the first sentence, at most 12 words
MIN_KIND_GAP = 0.6           # at most one beat per 0.6 s per kind (swears exempt: every swear must be bleepable)
MAX_LABEL_CHARS = 40
MAX_REACTION_QUERY_CHARS = 40
MAX_REACTIONS = 3
VALID_TIMING_SHARE = 0.8     # word timings are usable when ≥ 80 % of words have end > start
PUNCHLINE_PAUSE = 0.35       # "!" sentence followed by at least this much silence
PUNCHLINE_WORDS = 4          # heuristic punchline span: the landing words of the sentence
PAYOFF_MIN_CLIP = 12.0       # heuristic payoff only for clips of at least 12 s
REANCHOR_SURVIVAL = 0.5      # below this share of surviving beats, re-anchoring gives up (caller re-plans)
REANCHOR_SLACK = 0.03        # seconds of A-time tolerance when matching a beat's source span

_SENTENCE_END_RE = re.compile(r"""[.!?…。！？]["'”’)\]}]*$""")
_EXCLAIM_END_RE = re.compile(r"""[!！]["'”’)\]}]*$""")
_CENSORED_RE = re.compile(r"^[^\W\d_]+\*+[^\W\d_]*\**$")   # "f***", "sh*t": the transcript already masked a swear
_MASK_RE = re.compile(r"^_{2,}$")                            # YouTube captions' "[ __ ]"
_BEAT_ID_RE = re.compile(r"^b(\d+)$")
_SWEARS_FILE = Path(__file__).with_name("swears.txt")


# ----------------------------------------------------------------------------- words


def normalize_word(text: str) -> str:
    """Lowercase letters and digits only, unicode-aware, accents kept ("Scheiße!" -> "scheiße")."""
    folded = unicodedata.normalize("NFC", str(text or "")).lower()
    return "".join(ch for ch in folded if ch.isalnum())


def _num(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number == number and number not in (float("inf"), float("-inf")) else default


def _r(value: Optional[float]) -> Optional[float]:
    """Millisecond precision for stored times (sidecar size, stable JSON)."""
    return None if value is None else round(value, 3)


@dataclass(frozen=True)
class _Word:
    text: str
    start: float
    end: float
    src_start: Optional[float]
    src_end: Optional[float]


def _timed(words: Sequence[dict]) -> list[_Word]:
    timed: list[_Word] = []
    for word in words or ():
        start = max(0.0, _num(word.get("start")))
        end = max(start, _num(word.get("end"), start))
        src_start = word.get("src_start")
        src_end = word.get("src_end")
        timed.append(
            _Word(
                text=str(word.get("text", "") or ""),
                start=start,
                end=end,
                src_start=_num(src_start) if src_start is not None else None,
                src_end=_num(src_end) if src_end is not None else None,
            )
        )
    return timed


def words_hash(words: Sequence[dict]) -> str:
    """sha256 of json [(text, round(start, 2), round(end, 2))]: the identity of a clip's word timeline."""
    rows = [[str(w.get("text", "") or ""), round(_num(w.get("start")), 2), round(_num(w.get("end")), 2)]
            for w in words or ()]
    blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def timeline_hash(ranges: Sequence[Sequence[float]], offsets: Sequence[float] | None, words: Sequence[dict]) -> str:
    """§13.1: sha256(json([ranges, offsets, words_hash(words)]))[:32], stamped on EditBeats by the caller."""
    blob = json.dumps(
        [
            [[round(_num(a), 3), round(_num(b), 3)] for a, b in ranges or ()],
            [round(_num(o), 3) for o in offsets] if offsets is not None else None,
            words_hash(words),
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _approx_words(lines: Sequence[dict]) -> list[dict]:
    """Spread each line's duration across its words by character count (G12)."""
    out: list[dict] = []
    ordered = sorted((line for line in lines or () if isinstance(line, dict)), key=lambda line: _num(line.get("start")))
    for line in ordered:
        tokens = str(line.get("text", "") or "").split()
        start = _num(line.get("start"))
        end = _num(line.get("end"), start)
        if not tokens or end <= start:
            continue
        weights = [max(1, len(token)) for token in tokens]
        total = float(sum(weights))
        has_src = line.get("src_start") is not None and line.get("src_end") is not None
        src_start = _num(line.get("src_start")) if has_src else 0.0
        src_end = _num(line.get("src_end")) if has_src else 0.0
        cursor = 0
        for token, weight in zip(tokens, weights):
            a = cursor / total
            b = (cursor + weight) / total
            cursor += weight
            word = {
                "id": f"w{len(out)}",
                "text": token,
                "start": round(start + (end - start) * a, 3),
                "end": round(start + (end - start) * b, 3),
            }
            if has_src:
                word["src_start"] = round(src_start + (src_end - src_start) * a, 3)
                word["src_end"] = round(src_start + (src_end - src_start) * b, 3)
            out.append(word)
    return out


def words_with_timing(words: Sequence[dict], lines: Sequence[dict] | None = None) -> tuple[list[dict], Timing]:
    """Words ready for beat planning and their timing quality.

    - "word": ≥ 80 % of the words have end > start. Words are copied with an `id` ("w<i>" unless they had one) and
      end clamped to ≥ start.
    - "approx": word timings are missing or broken but caption `lines` ([{start, end, text}]) exist; each line's
      duration is spread across its words by character count. Bleeps are disabled downstream in this mode.
    - "none": nothing usable; ([], "none").
    """
    spoken = [w for w in words or () if isinstance(w, dict) and str(w.get("text", "") or "").strip()]
    if spoken:
        valid = sum(1 for w in spoken if _num(w.get("end")) > _num(w.get("start")))
        if valid >= VALID_TIMING_SHARE * len(spoken):
            out: list[dict] = []
            for index, word in enumerate(spoken):
                copy = dict(word)
                start = _num(word.get("start"))
                copy["id"] = str(word.get("id") or f"w{index}")
                copy["start"] = start
                copy["end"] = max(start, _num(word.get("end"), start))
                out.append(copy)
            return out, "word"
    approx = _approx_words(lines) if lines else []
    if approx:
        return approx, "approx"
    return [], "none"


def _sentence_index(words: Sequence[_Word]) -> list[int]:
    """Sentence number of each word (0-based); a word ending in . ! ? closes its sentence."""
    out: list[int] = []
    sentence = 0
    for word in words:
        out.append(sentence)
        if _SENTENCE_END_RE.search(word.text.strip()):
            sentence += 1
    return out


def _join(words: Sequence[_Word]) -> str:
    return " ".join(w.text.strip() for w in words if w.text.strip())[:200]


# ----------------------------------------------------------------------------- drafts


@dataclass(frozen=True)
class _Draft:
    """A beat before it has times and an id: a word-index span."""
    kind: str
    first: int
    last: int
    strength: int = 2
    tone: str = "neutral"
    emoji: str = ""
    label: str = ""
    source: str = "llm"


def _clamp_strength(value: object) -> int:
    try:
        return max(1, min(3, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 2


def _is_emoji_mark(ch: str) -> bool:
    """Combining marks that only dress up an emoji: variation selectors and the keycap ring."""
    code = ord(ch)
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF or code == 0x20E3


def _clean_label(text: object, limit: int = MAX_LABEL_CHARS) -> str:
    """Strip emoji, symbols and control characters; collapse whitespace; cut to `limit` characters."""
    kept: list[str] = []
    for ch in unicodedata.normalize("NFC", str(text or "")):
        category = unicodedata.category(ch)
        if category in ("Cc", "Zl", "Zp") or ch.isspace():
            kept.append(" ")
        elif category[0] == "C" or category in ("So", "Sk") or _is_emoji_mark(ch):
            continue
        else:
            kept.append(ch)
    return " ".join("".join(kept).split())[:limit].strip()


def _emoji_variants(emoji: str) -> list[str]:
    bare = emoji.replace("️", "")
    return [emoji, bare, bare + "️"]


# Emoji that never depict a thing the word names: faces, people, hands and gestures, ZWJ person sequences. The model
# reaches for them on abstract words (👉 "you", 👨‍💻 "gambling code"); the editor's emoji pick the object instead.
_NON_LITERAL_EMOJI = ((0x1F446, 0x1F487), (0x1F600, 0x1F64F), (0x1F910, 0x1F937), (0x1F9D0, 0x1F9DF),
                      (0x261D, 0x261D), (0x270A, 0x270D), (0x1F574, 0x1F57A), (0x1F645, 0x1F64F))


def _literal_emoji(raw: str) -> bool:
    if "\u200d" in raw:
        return False
    return not any(lo <= ord(ch) <= hi for ch in raw for lo, hi in _NON_LITERAL_EMOJI)


# Words that mark a vulnerable or grieving moment. When the clip has one (or the model calls it serious), nothing in
# it is played for laughs: punchlines become payoffs and comic tones become neutral (the model sometimes jokes anyway).
_SINCERE_WORDS = frozenset("""
cry crying cried cries tears tear sob sobbing grief grieving grieve funeral died dies dying death dead passed cancer
suicide depressed depression hurt hurting pain trauma abuse abused mourning lost loss sorry miss missed
""".split())
_COMIC_TONES = frozenset({"funny", "smug", "awkward"})


def _sincere_guard(drafts: list[_Draft], timed: Sequence[_Word], sensitivity: str) -> list[_Draft]:
    sincere = sensitivity == "serious" or any(normalize_word(w.text) in _SINCERE_WORDS for w in timed)
    if not sincere:
        return drafts
    out: list[_Draft] = []
    for d in drafts:
        if d.kind == "punchline":
            d = replace(d, kind="payoff")
        if d.tone in _COMIC_TONES:
            d = replace(d, tone="neutral")
        out.append(d)
    return out


def _confirmed_swear(timed: Sequence[_Word], first: int, last: int) -> Optional[tuple[int, int]]:
    """The model's swear span narrowed to the words the swear list (or the transcript's masking) confirms. Words
    with non-ASCII letters are trusted to the model (languages the list may miss). None: nothing to bleep."""
    span = timed[first: last + 1]
    hits = _swear_matches(span)
    if hits:
        return first + hits[0][0], first + hits[-1][1]
    foreign = [i for i, w in enumerate(span) if any(ch.isalpha() and ord(ch) > 127 for ch in w.text)]
    if foreign:
        return first + foreign[0], first + foreign[-1]
    return None


def _resolve_emoji(raw: str, span: Sequence[_Word], emoji_keys: frozenset[str]) -> str:
    """§7.3: the model's emoji when the atlas has it and it depicts a thing; otherwise the keyword map on the beat's
    words; else ""."""
    raw = (raw or "").strip()
    if raw and not _literal_emoji(raw):
        raw = ""
    if raw:
        for candidate in _emoji_variants(raw):
            if candidate in emoji_keys:
                return candidate
    for word in span:
        mapped = _lookup_emoji(normalize_token(word.text), EMOJI_KEYWORD_MAP)
        if mapped and mapped in emoji_keys and _literal_emoji(mapped):
            return mapped
    return ""


def _merge_same_kind(drafts: list[_Draft]) -> list[_Draft]:
    """Same-kind beats whose word spans overlap become one; the stronger one's attributes win."""
    merged: list[_Draft] = []
    by_kind: dict[str, list[_Draft]] = {}
    for draft in drafts:
        by_kind.setdefault(draft.kind, []).append(draft)
    for kind_drafts in by_kind.values():
        kind_drafts.sort(key=lambda d: (d.first, d.last))
        current: Optional[_Draft] = None
        for draft in kind_drafts:
            if current is None or draft.first > current.last:
                if current is not None:
                    merged.append(current)
                current = draft
                continue
            winner = draft if draft.strength > current.strength else current
            first, last = min(current.first, draft.first), max(current.last, draft.last)
            if last - first + 1 > MAX_SPAN_WORDS:
                first, last = winner.first, winner.last
            current = replace(winner, first=first, last=last, strength=max(current.strength, draft.strength))
        if current is not None:
            merged.append(current)
    return merged


def _space_same_kind(drafts: list[_Draft], words: Sequence[_Word]) -> list[_Draft]:
    """At most one beat per MIN_KIND_GAP seconds per kind; stronger beats claim their slot first."""
    kept: list[_Draft] = []
    starts: dict[str, list[float]] = {}
    for draft in sorted(drafts, key=lambda d: (-d.strength, words[d.first].start, d.first, d.kind)):
        start = words[draft.first].start
        taken = starts.get(draft.kind, ())
        if draft.kind != "swear" and any(abs(start - other) < MIN_KIND_GAP - 1e-9 for other in taken):
            continue
        starts.setdefault(draft.kind, []).append(start)
        kept.append(draft)
    return kept


def _priority_key(kind: str, strength: int, start: float, tiebreak: object) -> tuple:
    return (-KIND_PRIORITY.get(kind, 0), -strength, start, tiebreak)


def _finalize_drafts(drafts: list[_Draft], words: Sequence[_Word]) -> list[_Draft]:
    """§7.3 rules in order: one hook in the first two sentences, ≤ 2 payoffs, same-kind merge, 0.6 s spacing, cap 40."""
    if not drafts:
        return []
    sentences = _sentence_index(words)

    hooks = [d for d in drafts if d.kind == "hook" and sentences[d.first] < HOOK_SENTENCES]
    hook = max(hooks, key=lambda d: (d.strength, -d.first), default=None)
    others = [d for d in drafts if d.kind != "hook"]

    others = _merge_same_kind(others)   # before the payoff cap, so overlapping payoffs count once
    payoffs = sorted((d for d in others if d.kind == "payoff"), key=lambda d: (-d.strength, d.first))[:MAX_PAYOFFS]
    others = [d for d in others if d.kind != "payoff"] + payoffs

    pool = ([hook] if hook else []) + others
    pool = _space_same_kind(pool, words)

    swears = sorted((d for d in pool if d.kind == "swear"), key=lambda d: d.first)[:MAX_BEATS_TOTAL]
    rest = sorted((d for d in pool if d.kind != "swear"),
                  key=lambda d: _priority_key(d.kind, d.strength, words[d.first].start, d.first))
    rest = rest[: max(0, MAX_BEATS - len(swears))]   # ≤ 40 beats unless swears alone exceed it; never > 200
    return swears + rest


def _draft_to_beat(draft: _Draft, words: Sequence[_Word], beat_id: str) -> Beat:
    span = words[draft.first: draft.last + 1]
    value: Optional[float] = None
    prefix = suffix = ""
    if draft.kind == "number":
        parsed = parse_spoken_number([w.text for w in span])
        if parsed is not None:
            value, prefix, suffix = parsed
    first, last = span[0], span[-1]
    return Beat(
        id=beat_id,
        kind=draft.kind,  # type: ignore[arg-type]
        start=_r(first.start),
        end=_r(max(first.start, last.end)),
        text=_join(span),
        strength=draft.strength,
        tone=draft.tone,  # type: ignore[arg-type]
        emoji=draft.emoji,
        label=draft.label,
        value=value,
        prefix=prefix[:4],
        suffix=suffix[:8],
        source=draft.source,  # type: ignore[arg-type]
        src_start=_r(first.src_start),
        src_end=_r(last.src_end),
        words=tuple(n for n in (normalize_word(w.text) for w in span) if n)[:MAX_SPAN_WORDS],
    )


def _beat_order(beat: Beat) -> tuple:
    return (beat.start, -KIND_PRIORITY.get(beat.kind, 0), beat.end, beat.id)


def _renumber(beats: EditBeats) -> EditBeats:
    """Planning time only: ids b0.. in time order."""
    ordered = sorted(beats.beats, key=_beat_order)
    renamed = tuple(b.model_copy(update={"id": f"b{i}"}) for i, b in enumerate(ordered))
    return beats.model_copy(update={"beats": renamed})


# ----------------------------------------------------------------------------- swears (G7)


@dataclass(frozen=True)
class _SwearList:
    exact: frozenset[str]
    stems: tuple[str, ...]
    phrases: tuple[tuple[str, ...], ...]


@lru_cache(maxsize=1)
def _swear_list() -> _SwearList:
    exact: set[str] = set()
    stems: set[str] = set()
    phrases: set[tuple[str, ...]] = set()
    for raw in _SWEARS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) > 1:
            phrase = tuple(normalize_word(p) for p in parts)
            if all(phrase):
                phrases.add(phrase)
        elif line.endswith("*"):
            stem = normalize_word(line[:-1])
            if stem:
                stems.add(stem)
        else:
            word = normalize_word(line)
            if word:
                exact.add(word)
    return _SwearList(frozenset(exact), tuple(sorted(stems)), tuple(sorted(phrases, key=len, reverse=True)))


def _is_swear_word(text: str, swears: _SwearList) -> bool:
    stripped = text.strip().strip("\"'“”‘’«»()[]{}<>,.;:!?…")
    if _CENSORED_RE.match(stripped) or _MASK_RE.match(stripped):
        return True
    norm = normalize_word(text)
    if not norm:
        return False
    return norm in swears.exact or any(norm.startswith(stem) for stem in swears.stems)


def _swear_matches(words: Sequence[_Word]) -> list[tuple[int, int]]:
    """Word-index spans of list swears, left to right; phrases first at each position."""
    swears = _swear_list()
    norms = [normalize_word(w.text) for w in words]
    matches: list[tuple[int, int]] = []
    index = 0
    while index < len(words):
        hit = False
        for phrase in swears.phrases:
            end = index + len(phrase)
            if end <= len(words) and tuple(norms[index:end]) == phrase:
                matches.append((index, end - 1))
                index = end
                hit = True
                break
        if hit:
            continue
        if _is_swear_word(words[index].text, swears):
            matches.append((index, index))
        index += 1
    return matches


def _next_beat_number(beats: Sequence[Beat]) -> int:
    numbers = [int(m.group(1)) for m in (_BEAT_ID_RE.match(b.id) for b in beats) if m]
    return (max(numbers) + 1) if numbers else len(beats)


def _cap_total(beats: list[Beat]) -> list[Beat]:
    if len(beats) <= MAX_BEATS_TOTAL:
        return beats
    swears = [b for b in beats if b.kind == "swear"]
    rest = sorted((b for b in beats if b.kind != "swear"),
                  key=lambda b: _priority_key(b.kind, b.strength, b.start, b.id))
    keep = swears[:MAX_BEATS_TOTAL] + rest[: max(0, MAX_BEATS_TOTAL - len(swears))]
    return sorted(keep, key=_beat_order)


def union_swears(beats: EditBeats, words: Sequence[dict]) -> EditBeats:
    """G7: add a strength-2 `swear` beat for every list swear not already covered by a swear beat.

    Idempotent. Existing beats and ids are untouched; new beats get the next free "b<n>" ids. Applied to LLM and
    heuristic beats alike (and after re-anchoring), so a swear the model missed is still bleeped.
    """
    timed = _timed(words)
    if not timed:
        return beats
    covered: set[int] = set()
    for beat in beats.beats:
        if beat.kind != "swear":
            continue
        for index, word in enumerate(timed):
            middle = (word.start + word.end) / 2
            if beat.start - 0.02 <= middle <= beat.end + 0.02:
                covered.add(index)

    added: list[Beat] = []
    next_number = _next_beat_number(beats.beats)
    taken = {b.id for b in beats.beats}
    for first, last in _swear_matches(timed):
        if all(i in covered for i in range(first, last + 1)):
            continue
        while f"b{next_number}" in taken:
            next_number += 1
        beat_id = f"b{next_number}"
        taken.add(beat_id)
        next_number += 1
        added.append(_draft_to_beat(_Draft(kind="swear", first=first, last=min(last, first + MAX_SPAN_WORDS - 1),
                                           strength=2, source="heuristic"), timed, beat_id))
        covered.update(range(first, last + 1))

    if not added:
        return beats
    combined = _cap_total(sorted(list(beats.beats) + added, key=_beat_order))
    return beats.model_copy(update={"beats": tuple(combined)})


# ----------------------------------------------------------------------------- LLM validation (§7.3)


def _clean_query(text: object) -> str:
    return _clean_label(text, MAX_REACTION_QUERY_CHARS)


def validate_llm_plan(out: EditPlanLLM, words: Sequence[dict], *, model: str,
                      emoji_keys: frozenset[str]) -> EditBeats:
    """Turn the model's word-id tags into beats. Code decides every time, cap and id (§7.3).

    The returned beats have `timing="word"` (or "none" without words) and an empty `timeline_hash`: the caller knows
    the timing quality, ranges and offsets and stamps them.
    """
    timed = _timed(words)
    n = len(timed)
    base = EditBeats(
        words_hash=words_hash(words),
        model=str(model or "")[:80],
        source="llm",
        mood=out.mood,
        sensitivity=out.sensitivity,
        timing="word" if n else "none",
    )
    if not n:
        return base

    def clamp(index: object) -> int:
        try:
            value = int(index)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            value = 0
        return max(0, min(n - 1, value))

    drafts: list[_Draft] = []
    for tag in out.beats:
        first, last = clamp(tag.first_word), clamp(tag.last_word)
        if first > last:
            first, last = last, first
        last = min(last, first + MAX_SPAN_WORDS - 1)
        if tag.kind == "swear":
            confirmed = _confirmed_swear(timed, first, last)
            if confirmed is None:
                continue          # the model tagged an ordinary word: never bleep it
            first, last = confirmed
        span = timed[first: last + 1]
        drafts.append(
            _Draft(
                kind=tag.kind,
                first=first,
                last=last,
                strength=_clamp_strength(tag.strength),
                tone=tag.tone,
                emoji=_resolve_emoji(tag.emoji, span, emoji_keys),
                label=_clean_label(tag.label),
                source="llm",
            )
        )

    drafts = _sincere_guard(drafts, timed, out.sensitivity)
    kept = _finalize_drafts(drafts, timed)
    beats = tuple(_draft_to_beat(d, timed, f"t{i}") for i, d in enumerate(kept))

    hook_end: Optional[float] = None
    if int(out.hook_last_word) >= 0:
        hook_end = _r(timed[clamp(out.hook_last_word)].end)
    else:
        hook = next((b for b in beats if b.kind == "hook"), None)
        hook_end = hook.end if hook else None

    reactions: list[ReactionIdea] = []
    seen: set[tuple[int, str]] = set()
    for idea in out.reactions:
        query = _clean_query(idea.query)
        index = clamp(idea.word)
        if not query or (index, query.lower()) in seen:
            continue
        seen.add((index, query.lower()))
        reactions.append(ReactionIdea(at=_r(timed[index].start), query=query))
        if len(reactions) >= MAX_REACTIONS:
            break

    result = base.model_copy(update={"beats": beats, "hook_end": hook_end, "reactions": tuple(reactions)})
    return _renumber(union_swears(result, words))


# ----------------------------------------------------------------------------- heuristic fallback (§7.5)


def heuristic_beats(words: Sequence[dict], *, timing: Timing) -> EditBeats:
    """Deterministic beats when the LLM is unavailable, failed, or the clip is style-only (§7.5).

    emphasis = emoji_captions emphasis (strength 2 for POWER_WORDS), digit numbers, the first sentence as hook, the
    strongest late emphasis as payoff (+ the word before it as build) on clips ≥ 12 s, "!" sentences followed by a
    pause as punchlines, and list swears.
    """
    timed = _timed(words)
    base = EditBeats(
        words_hash=words_hash(words),
        model="",
        source="heuristic",
        mood="hype",
        sensitivity="normal",
        timing=timing if timed else "none",
    )
    if not timed or timing == "none":
        return base

    texts = [w.text for w in timed]
    drafts: list[_Draft] = []

    number_words: set[int] = set()
    for start, count, _parsed in find_number_spans(texts):
        last = start + min(count, MAX_SPAN_WORDS) - 1
        drafts.append(_Draft(kind="number", first=start, last=last, strength=2, source="heuristic"))
        number_words.update(range(start, start + count))

    emoji_by_index, emphasis_indices = annotate_caption_words([{"text": t} for t in texts])
    emphasis: list[_Draft] = []
    for index in sorted(emphasis_indices):
        if index in number_words or any(ch.isdigit() for ch in texts[index]):
            continue
        token = normalize_token(texts[index])
        emphasis.append(
            _Draft(
                kind="emphasis",
                first=index,
                last=index,
                strength=2 if token in POWER_WORDS else 1,
                emoji=emoji_by_index.get(index, ""),
                source="heuristic",
            )
        )

    sentences = _sentence_index(timed)
    # Blank tokens never make a hook on their own: a first "sentence" of only blank tokens gives no hook beat.
    hook_words = [i for i, s in enumerate(sentences) if s == 0 and timed[i].text.strip()][:HEURISTIC_HOOK_WORDS]
    hook_end: Optional[float] = None
    if hook_words:
        drafts.append(_Draft(kind="hook", first=hook_words[0], last=hook_words[-1], strength=2, source="heuristic"))
        hook_end = _r(timed[hook_words[-1]].end)

    clip_end = timed[-1].end            # S starts at 0, so the last word's end is the spoken clip length
    if clip_end >= PAYOFF_MIN_CLIP and emphasis:
        late = clip_end * 2 / 3
        candidates = [d for d in emphasis if timed[d.first].start >= late]
        if candidates:
            payoff = max(candidates, key=lambda d: (d.strength, d.first))
            emphasis.remove(payoff)
            drafts.append(replace(payoff, kind="payoff", strength=max(2, payoff.strength)))
            if payoff.first > 0:
                drafts.append(_Draft(kind="build", first=payoff.first - 1, last=payoff.first - 1, strength=1,
                                     source="heuristic"))
    drafts.extend(emphasis)

    sentence_words: dict[int, list[int]] = {}
    for index, sentence in enumerate(sentences):
        sentence_words.setdefault(sentence, []).append(index)
    for indices in sentence_words.values():
        last = indices[-1]
        if not _EXCLAIM_END_RE.search(timed[last].text.strip()):
            continue
        pause = (timed[last + 1].start - timed[last].end) if last + 1 < len(timed) else PUNCHLINE_PAUSE
        if pause < PUNCHLINE_PAUSE - 1e-9:
            continue
        first = indices[max(0, len(indices) - PUNCHLINE_WORDS)]
        drafts.append(_Draft(kind="punchline", first=first, last=last, strength=2, tone="hype", source="heuristic"))

    drafts = _sincere_guard(drafts, timed, "normal")
    kept = _finalize_drafts(drafts, timed)
    beats = tuple(_draft_to_beat(d, timed, f"t{i}") for i, d in enumerate(kept))
    if hook_end is not None and not any(b.kind == "hook" for b in beats):
        hook_end = None
    result = base.model_copy(update={"beats": beats, "hook_end": hook_end})
    return _renumber(union_swears(result, words))


# ----------------------------------------------------------------------------- re-anchoring (§13.1)


def reanchor_beats(beats: EditBeats, words: Sequence[dict]) -> EditBeats | None:
    """Move beats onto a changed clip timeline by their source (A) times.

    Each beat's [src_start, src_end] is matched against the current words' A times; the beat survives when at least
    one of its normalised `words` is still spoken there (cleanup may have cut fillers or pauses around it). Its S
    times, text and identity words come from the surviving words; ids, kinds and attributes stay, so the resolver's
    per-beat seeds are stable. Swear beats re-anchor the same way and `union_swears` then adds any list swear on the
    new words, so bleeps land on the new swear times.

    Returns None when fewer than 50 % of the beats survive (or the words carry no A times): the caller re-plans.
    `timeline_hash` is cleared (the caller stamps it); reaction ideas carry no A time and are dropped.
    """
    timed = _timed(words)
    anchored = [w for w in timed if w.src_start is not None and w.src_end is not None]
    if beats.beats and not anchored:
        return None

    kept: list[Beat] = []
    for beat in beats.beats:
        if beat.src_start is None or beat.src_end is None:
            continue
        lo, hi = beat.src_start - REANCHOR_SLACK, beat.src_end + REANCHOR_SLACK
        candidates = [
            w for w in timed
            if w.src_start is not None and w.src_end is not None and w.src_end > lo and w.src_start < hi
        ]
        identity = set(beat.words)
        matched = [w for w in candidates if normalize_word(w.text) in identity] if identity else candidates
        if not matched:
            continue
        first, last = matched[0], matched[-1]
        kept.append(
            beat.model_copy(
                update={
                    "start": _r(first.start),
                    "end": _r(max(first.start, last.end)),
                    "text": _join(matched) or beat.text,
                    "src_start": _r(first.src_start),
                    "src_end": _r(last.src_end),
                    "words": tuple(n for n in (normalize_word(w.text) for w in matched) if n)[:MAX_SPAN_WORDS],
                }
            )
        )

    if beats.beats and len(kept) < REANCHOR_SURVIVAL * len(beats.beats):
        return None

    kept.sort(key=_beat_order)
    hook = next((b for b in kept if b.kind == "hook"), None)
    moved = beats.model_copy(
        update={
            "beats": tuple(kept),
            "words_hash": words_hash(words),
            "timeline_hash": "",
            "hook_end": hook.end if hook else None,
            "reactions": (),
        }
    )
    return union_swears(moved, words)
