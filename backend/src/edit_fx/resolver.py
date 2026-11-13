"""Resolver (§8): tagged beats + intensity + caption template -> typed, editable cues (`FxPlan`).

Pure: no I/O, no clock, no globals that change between calls. Every random choice is
``hash01(cue_seed(seed, beat_id, purpose), n)``, so the same input always gives the same plan, and removing one beat
never changes another cue's id or values (it can only free budget for cues that were dropped before).

All times are on the **E** clock (edit seconds; == S for a generated clip). Budgets, spacing and quiet zones are
evaluated on the E duration. The resolver never emits Ken Burns pushes (`kb{n}`): `baseline.baseline_pushes` adds them
once the layout is known (AR-M9).

Cue ids are ``<beat>.<purpose>`` (§8.1). A treatment that depends on a per-clip budget gets its own purpose (``slam``,
``behind``, ``burst``), and its fallback keeps ``accent``, so a freed budget adds or removes a cue but never rewrites
one.

Order of work (§8.2, §8.5):
  1. user cues (kept verbatim; they occupy budget and spacing)
  2. bleeps (compliance: ignore budgets and spacing)
  3. reactions (their windows exclude zoom/shake/transition/glitch/behind/doodle)
  4. transitions at cuts, ranked by the strongest beat within 2 s
  5. beats by priority, each proposing a bundle (§8.4); a member that breaks its family cap, spacing or a global rule
     is dropped on its own
  6. template fill-ins (unhinged highlighter words) and wordless speed-ups; kinetic_stack without a hook beat
     places its kinetic first sentence before step 5
  7. music, grade, grain, retention, hook band
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from .censor import censor_text
from .constants import TONE_EMOJI
from .contracts import (
    TRANSITION_HALF,
    AccentCue,
    Beat,
    BleepCue,
    DesaturateCue,
    EditBeats,
    EditStyle,
    FlashCue,
    FreezeCue,
    FxCue,
    FxPlan,
    GlitchCue,
    MusicBed,
    ReactionCue,
    ShakeCue,
    SfxCue,
    SpeedCue,
    StickerCue,
    TransitionCue,
    ZoomCue,
    is_fx_template,
)
from .rng import cue_seed, hash01
from .warp import build_warp, quantize

# ----------------------------------------------------------------------------- input


@dataclass(frozen=True)
class ResolveInput:
    words: tuple[dict, ...]                          # S==E words {id,start,end,text}
    beats: EditBeats
    duration: float                                  # edit duration
    cuts: tuple[float, ...] = ()                     # junction CENTRE times in E (AR-m2): generation offset_i + fade_i/2
                                                     # (fade_i from junction_fades_for_offsets, 0 for hard cuts);
                                                     # studio: E start of each play-order segment after the first
    passage_breaks: tuple[float, ...] = ()           # subset of cuts (AP-M2): generation = workflow group output starts
                                                     # + junctions whose source gap ≥ 3.0 s; studio = junctions between
                                                     # non-contiguous segments (source gap ≥ 3.0 s or reordered)
    broll_windows: tuple[tuple[float, float], ...] = ()
    hook_window: tuple[float, float] | None = None   # (0.12, hook_title_window(dur)) when a hook title burns (AI-L2)
    max_clip_seconds: float | None = None
    sfx_variants: Mapping[str, int] = field(default_factory=dict)   # BUILT-IN manifest counts only (AI-H8)
    beds: Mapping[str, tuple[str, ...]] = field(default_factory=dict)   # mood → BUILT-IN bed ids
    bed_lengths: Mapping[str, float] = field(default_factory=dict)      # bed id → loop seconds (built-in)
    reactions_available: bool = False
    user_cues: tuple[FxCue, ...] = ()                # kept verbatim (studio); auto cues colliding with them dropped
    suppressed: frozenset[str] = frozenset()         # auto cue ids the user deleted (studio); never re-emitted


# ----------------------------------------------------------------------------- tables (§8.2-§8.7)

PRIORITY: dict[str, int] = {
    "payoff": 7, "hook": 6, "punchline": 5, "number": 4, "swear": 3, "emotional_turn": 2, "build": 1, "emphasis": 0,
}
TIERS: tuple[str, ...] = ("style", "chill", "spicy", "unhinged")
_RANK = {tier: i for i, tier in enumerate(TIERS)}

# Family budgets (§8.3). ("rate", per-60-s rate, min gap s) or ("count", per-clip count, 0).
Budget = tuple[str, float, float]
BUDGETS: dict[str, dict[str, Budget]] = {
    "accent":   {"style": ("rate", 10, 3.0), "chill": ("rate", 10, 3.0), "spicy": ("rate", 20, 1.5),
                 "unhinged": ("rate", 40, 0.8)},
    "punch":    {"style": ("count", 0, 0), "chill": ("rate", 4, 6.0), "spicy": ("rate", 8, 3.0),
                 "unhinged": ("rate", 14, 1.6)},
    "push":     {"style": ("count", 0, 0), "chill": ("count", 1, 0), "spicy": ("count", 2, 0),
                 "unhinged": ("count", 4, 0)},
    "shake":    {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("rate", 3, 8.0),
                 "unhinged": ("rate", 8, 3.0)},
    "sfx":      {"style": ("rate", 10, 1.0), "chill": ("rate", 6, 2.5), "spicy": ("rate", 12, 1.2),
                 "unhinged": ("rate", 24, 0.6)},
    "hit":      {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 2, 0),
                 "unhinged": ("rate", 6, 4.0)},
    "freeze":   {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 1, 0),
                 "unhinged": ("count", 2, 0)},
    "ramp":     {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 1, 0),
                 "unhinged": ("count", 2, 0)},
    "fast":     {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 0, 0),
                 "unhinged": ("count", 2, 0)},
    "slow":     {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 0, 0),
                 "unhinged": ("count", 1, 0)},
    "burst":    {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 3, 0),
                 "unhinged": ("count", 5, 0)},
    "reaction": {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 1, 0),
                 "unhinged": ("count", 2, 0)},
    "wait":     {"style": ("count", 0, 0), "chill": ("count", 0, 0), "spicy": ("count", 1, 0),
                 "unhinged": ("count", 1, 0)},
}
# Transitions: chill ≤1 on passage breaks only; spicy 1 per 8 s of clip; unhinged 1 per 4 s.
TRANSITION_EVERY: dict[str, float] = {"spicy": 8.0, "unhinged": 4.0}

SFX_BUS_GAIN_DB: dict[str, float] = {"style": 0.0, "chill": -4.0, "spicy": 0.0, "unhinged": 2.0}
MUSIC_LEVELS: dict[str, tuple[float, float]] = {"chill": (-24.0, -10.0), "spicy": (-20.0, -12.0),
                                                "unhinged": (-17.0, -13.0)}
NO_TIMING_MUSIC_DB = -24.0
GRAIN: dict[str, float] = {"chill": 0.15, "spicy": 0.20, "unhinged": 0.30}

# Sounds removed by serious mode (rule 1), template-intrinsic ones included.
SERIOUS_DROP_SOUNDS = frozenset({"pop", "boom", "record_scratch", "cash_register", "slap", "tape_stop"})
# Sounds that justify a shake (§8.3: shake only with an impact-class SFX or a slam).
IMPACT_SOUNDS = frozenset({"impact", "boom", "stamp", "slap", "cash_register"})
# Relative per-sound trims (dB). The intensity bus gain (§8.3) is applied by the mixer, never baked in here.
SFX_TRIM_DB: dict[str, float] = {"ticker": -6.0, "riser": -4.0, "whoosh": -2.0, "glitch": -3.0, "tape_stop": -2.0}
# Built-in riser lengths by variant (§12 synthesis recipe) so a riser can END at a moment.
RISER_SECONDS: tuple[float, ...] = (1.5, 2.5)
CURRENCY_PREFIXES = frozenset({"$", "€", "£", "¥", "₹", "₩", "₽", "R$"})
HIDES_CAPTIONS = frozenset({"slam", "kinetic"})
EXCLUDED_ACCENTS = frozenset({"behind", "doodle"})     # never inside B-roll or reaction windows (rule 4)
SLAM_TEMPLATE_KINDS = frozenset({"hook", "punchline", "payoff"})
ACCENT_KINDS = frozenset({"hook", "emphasis", "number", "punchline", "payoff"})
NEGATIONS = frozenset({"not", "never", "no", "don't", "can't", "won't", "isn't", "dont", "cant", "wont", "isnt",
                       "nunca", "nein", "não", "nao", "tidak"})
STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have he her his i if in into is it its
just me my of on or our she so than that the their them then there these they this to too us very was we were what
when which who will with would you your i'm it's that's i've you're we're they're gonna wanna yeah oh okay ok like
really um uh el la los las un una y o de que en es por con lo le se der die das und ist ein eine zu den mit le les
des et est une du à je il o os as um uma e é do da em que para yang dan di ini itu ke
""".split())
TRANSITION_POOLS: dict[str, tuple[str, ...]] = {
    "chill": ("light_leak", "flash", "whip"),
    "spicy": ("whip", "zoom_through", "flash", "light_leak", "glitch"),
    "unhinged": ("whip", "zoom_through", "spin", "glitch", "flash", "whip"),
}
CALM_MOODS = frozenset({"chill", "dramatic", "inspiring", "dark"})

_EDGE_PUNCT = re.compile(r"^[\"'“”‘’¿¡(\[]+|[\"'“”‘’.,!?;:…)\]]+$")

QUIET_HEAD = 0.25          # rule 3: nothing in [0, 0.25) except hook-bound cues
QUIET_TAIL = 0.30          # rule 3: nothing in the last 0.3 s unless loop_ending
HIT_SPACING = 0.8          # rule 4: full-frame hits ≥ 0.8 s apart
ZOOM_TRANSITION_GAP = 0.4  # rule 4: no zoom within 0.4 s of a transition
USER_COLLISION = 0.3       # rule 12
EPS = 1e-6
FPS = 30
MAX_CUE_ID = 32            # CueBase.id max_length; a longer derived id (very long beat id) is skipped, never raised


def _r(x: float) -> float:
    return round(float(x), 3)


def _frame(x: float) -> float:
    """Snap to the 30 fps grid exactly like `build_warp` (round half up, never banker's rounding)."""
    return quantize(x)


def _norm(text: str) -> str:
    return re.sub(r"^[^\w$€£¥#%]+|[^\w%']+$", "", (text or "").lower(), flags=re.UNICODE)


def _letters(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha())


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.!?…][\"')\]]*$", (text or "").strip()))


def _overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 < b1 - EPS and b0 < a1 - EPS


def _interval_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Distance between two intervals (0 when they touch or overlap). Instants are zero-length intervals."""
    return max(0.0, max(a0, b0) - min(a1, b1))


def family_cap(budget: Budget, duration: float) -> int:
    """§8.3: `cap = max(1, round(rate*dur/60))` when dur ≥ 6 s (round half up); shorter clips round without the
    floor of 1."""
    mode, value, _gap = budget
    if mode == "count":
        return int(value)
    if value <= 0:
        return 0
    raw = math.floor(value * duration / 60.0 + 0.5)
    return max(1, raw) if duration >= 6.0 else raw


def transition_cap(tier: str, duration: float) -> int:
    if tier == "chill":
        return 1
    every = TRANSITION_EVERY.get(tier)
    if not every:
        return 0
    return max(1, int(duration // every))


def tier_for(style: EditStyle, sensitivity: str) -> str:
    """Budget column: style-only when intensity is off; serious mode forces chill (rule 1)."""
    if not style.active:
        return "style"
    if sensitivity == "serious":
        return "chill"
    return style.intensity


def sfx_bus_gain_db(style: EditStyle, sensitivity: str) -> float:
    """§8.3 "SFX bus gain" row, applied by the mixer on top of each cue's `gain_db` (never baked into cues)."""
    return SFX_BUS_GAIN_DB[tier_for(style, sensitivity)]


# ----------------------------------------------------------------------------- planner


@dataclass
class _Cand:
    kind: str
    id: str
    start: float
    end: float
    beat: str
    fields: dict[str, Any]
    family: str = ""
    hook_bound: bool = False


class _Planner:
    def __init__(self, inp: ResolveInput, style: EditStyle, template: str, seed: int):
        self.inp = inp
        self.style = style
        self.template = template
        self.seed = int(seed) & 0xFFFFFFFF
        self.dur = max(0.0, float(inp.duration))
        self.beats_meta = inp.beats
        self.serious = inp.beats.sensitivity == "serious"
        self.tier = tier_for(style, inp.beats.sensitivity)
        self.timing = inp.beats.timing
        self.fx_template = is_fx_template(template)
        self.loop = bool(style.active and style.loop_ending)
        self.words: list[dict] = sorted(
            ({**w, "start": float(w["start"]), "end": max(float(w["start"]), float(w["end"])),
              "text": str(w.get("text") or "")}
             for w in inp.words if w.get("end") is not None and w.get("start") is not None),
            key=lambda w: (w["start"], w["end"]),
        )
        self.beats: list[Beat] = (
            [b for b in inp.beats.beats if b.start < self.dur + EPS] if self.timing != "none" else []
        )
        self.excl: list[tuple[float, float]] = [(float(a), float(b)) for a, b in inp.broll_windows if b > a]
        self.reaction_windows: list[tuple[float, float]] = []
        self.placed: list[_Cand] = []
        self.taken_ids: set[str] = set()
        self.counts: dict[str, int] = {}
        self.starts: dict[str, list[tuple[float, str]]] = {}
        self.hits: list[tuple[float, str]] = []          # full-frame hits: flash, glitch, transition, slam, shake
        self.ffg: list[tuple[float, float]] = []          # active windows of flash / glitch / transition
        self.zooms: list[tuple[float, float]] = []
        self.transitions: list[float] = []
        self.sounds: list[tuple[float, str, str]] = []    # (t, sound, beat)
        self.holds: list[tuple[float, float]] = []        # (E instant, hold)
        self.speeds: list[tuple[float, float, float]] = []  # (e0, e1, final-length delta)
        self.hides: list[tuple[float, float]] = []        # spans of accents that hide captions (slam, kinetic)
        self.accent_spans: list[tuple[float, float, str]] = []
        self.behind_count = 0
        self.slam_ids: frozenset[str] = frozenset()
        self.user_cues: list[FxCue] = list(inp.user_cues)
        self.user_ids: frozenset[str] = frozenset(c.id for c in self.user_cues)
        self.swear_spans: list[tuple[float, float]] = (
            [(b.start, b.end) for b in self.beats if b.kind == "swear"] if style.censor != "off" else []
        )
        self.hook_beat: Optional[Beat] = next((b for b in self.beats if b.kind == "hook"), None)

    # ------------------------------------------------------------------ helpers

    def ge(self, tier: str) -> bool:
        return _RANK[self.tier] >= _RANK[tier]

    def eligible(self, b: Beat) -> bool:
        """§8.3 strength gate for non-accent extras. Hook, number and swear are eligible at any strength."""
        if self.tier == "style":
            return False
        if b.kind in ("hook", "number", "swear"):
            return True
        need = {"chill": 3, "spicy": 2, "unhinged": 1}[self.tier]
        return b.strength >= need

    def cseed(self, beat_id: str, purpose: str) -> int:
        return cue_seed(self.seed, beat_id, purpose)

    def seed_of(self, beat_id: str, cue_id: str) -> int:
        """§8.1: `cue_seed(seed, beat_id or id, purpose)`; the purpose is the id after `<beat>.` (globals: after
        the first dot). So a cue's random values come from its own id only."""
        if beat_id and cue_id.startswith(beat_id + "."):
            return cue_seed(self.seed, beat_id, cue_id[len(beat_id) + 1:])
        return cue_seed(self.seed, cue_id, cue_id.split(".", 1)[-1])

    def rand(self, beat_id: str, purpose: str, lo: float, hi: float, n: int = 0) -> float:
        return lo + (hi - lo) * hash01(self.cseed(beat_id, purpose), n)

    def budget(self, family: str) -> Budget:
        return BUDGETS[family][self.tier]

    def family_ok(self, family: str, t: float, beat_id: str, *, exempt_same_beat: bool = False) -> bool:
        budget = self.budget(family)
        if self.counts.get(family, 0) >= family_cap(budget, self.dur):
            return False
        gap = budget[2]
        if gap > 0:
            for other_t, other_beat in self.starts.get(family, ()):
                if exempt_same_beat and beat_id and other_beat == beat_id:
                    continue
                if abs(other_t - t) < gap - EPS:
                    return False
        return True

    def count(self, family: str, t: float, beat_id: str, *, counted: bool = True) -> None:
        if counted:
            self.counts[family] = self.counts.get(family, 0) + 1
        self.starts.setdefault(family, []).append((t, beat_id))

    def in_excl(self, start: float, end: float) -> bool:
        for a, b in self.excl + self.reaction_windows:
            if end - start < EPS:
                if a - EPS <= start <= b + EPS:
                    return True
            elif _overlaps(start, end, a, b):
                return True
        return False

    def quiet(self, start: float, end: float, *, instant: bool, hook_bound: bool,
              min_len: float = 0.1) -> Optional[tuple[float, float]]:
        """Rule 3 quiet zones. Returns the (possibly end-clipped) span or None."""
        if start < QUIET_HEAD - EPS and not hook_bound:
            return None
        start = max(0.0, start)
        if self.loop:
            limit = self.dur
        else:
            limit = self.dur - QUIET_TAIL
        if instant:
            return (start, start) if start <= limit + EPS else None
        end = min(end, limit if not self.loop else self.dur)
        if end - start < min_len - EPS:
            return None
        return start, end

    def user_collides(self, kind: str, start: float, end: float) -> bool:
        for cue in self.user_cues:
            if cue.kind == kind and _interval_gap(start, end, cue.start, cue.end) < USER_COLLISION - EPS:
                return True
        return False

    def hit_ok(self, t: float, beat_id: str) -> bool:
        return all(abs(t - other) >= HIT_SPACING - EPS for other, b in self.hits if not (beat_id and b == beat_id))

    def ffg_ok(self, start: float, end: float) -> bool:
        return not any(_overlaps(start, max(end, start + EPS), a, b) for a, b in self.ffg)

    def zoom_ok(self, start: float, end: float) -> bool:
        if self.in_excl(start, end):
            return False
        return all(_interval_gap(start, end, t, t) >= ZOOM_TRANSITION_GAP - EPS for t in self.transitions)

    def add(self, cand: _Cand) -> bool:
        if cand.id in self.taken_ids:
            return False
        self.taken_ids.add(cand.id)
        self.placed.append(cand)
        return True

    def words_in(self, start: float, end: float) -> list[dict]:
        return [w for w in self.words if w["start"] >= start - 0.02 and w["end"] <= end + 0.02]

    def beat_words(self, b: Beat) -> list[dict]:
        inside = self.words_in(b.start, b.end)
        if inside:
            return inside
        touching = [w for w in self.words if _overlaps(w["start"], w["end"], b.start, b.end)]
        if touching:
            return touching
        return [{"id": b.id, "start": b.start, "end": max(b.end, b.start), "text": b.text}]

    def next_word_after(self, t: float) -> Optional[dict]:
        for w in self.words:
            if w["start"] >= t - EPS:
                return w
        return None

    def prev_word_before(self, t: float) -> Optional[dict]:
        prev = None
        for w in self.words:
            if w["end"] <= t + EPS:
                prev = w
            else:
                break
        return prev

    def inside_word(self, t: float, margin: float = 0.05) -> bool:
        return any(w["start"] + margin < t < w["end"] - margin for w in self.words)

    def censored(self, w: dict) -> str:
        text = str(w.get("text", ""))
        for a, b in self.swear_spans:
            if min(w["end"], b) - max(w["start"], a) >= 0.03 - EPS or (
                w["end"] - w["start"] < 0.03 and a - EPS <= w["start"] <= b + EPS
            ):
                return censor_text(text)
        return text

    def text_of(self, words: Sequence[dict], limit: int = 120) -> str:
        """Display text: censored where a bleep covers a word, without the sentence punctuation at its edges."""
        joined = " ".join(self.censored(w).strip() for w in words if str(w.get("text", "")).strip())
        return _EDGE_PUNCT.sub("", joined)[:limit].strip()

    @staticmethod
    def key_word(words: Sequence[dict]) -> dict:
        """The word an accent or punch lands on: a number if present, else a long distinctive content word (≥ 7
        letters), else the LAST content word (English phrase stress falls on it: "...while you SLEEP")."""
        def score(item: tuple[int, dict]) -> tuple:
            i, w = item
            text = str(w.get("text", ""))
            digit = any(ch.isdigit() for ch in text)
            content = _norm(text) not in STOPWORDS and _letters(text) > 0
            return (digit, content, _letters(text) >= 7, i)
        return max(enumerate(words), key=score)[1]

    def key_phrase(self, words: Sequence[dict], max_words: int) -> list[dict]:
        """Up to `max_words` consecutive words around the key word, without stopwords at its edges
        ("was sixteen when" -> "sixteen"). Beats of ≤ 2 words are kept whole ("That's it")."""
        if len(words) <= min(2, max_words):
            return list(words)
        key = self.key_word(words)
        idx = next(i for i, w in enumerate(words) if w is key)
        lo = max(0, min(idx - (max_words - 1) // 2, len(words) - max_words))
        phrase = list(words[lo:lo + max_words])
        while len(phrase) > 1 and phrase[0] is not key and _norm(phrase[0]["text"]) in STOPWORDS:
            phrase.pop(0)
        while len(phrase) > 1 and phrase[-1] is not key and _norm(phrase[-1]["text"]) in STOPWORDS:
            phrase.pop()
        return phrase

    def sentence_bounds(self, t: float) -> tuple[Optional[int], Optional[int]]:
        """Indices of the first and last word of the sentence containing time t."""
        idx = None
        for i, w in enumerate(self.words):
            if w["start"] <= t + 0.05:
                idx = i
            else:
                break
        if idx is None:
            return None, None
        first = idx
        while first > 0 and not _ends_sentence(self.words[first - 1]["text"]):
            first -= 1
        last = idx
        while last < len(self.words) - 1 and not _ends_sentence(self.words[last]["text"]):
            last += 1
        return first, last

    # ------------------------------------------------------------------ cue makers (each checks its own rules)

    def sfx(self, beat_id: str, cue_id: str, sound: str, t: float, *, intrinsic: bool, hook_bound: bool = False,
            variant: Optional[int] = None, counted: bool = True) -> bool:
        """One SFX cue. `beat_id` is the producing beat ("" for globals); same-beat sounds are exempt from the family
        spacing among themselves (a compound hit such as whoosh -> boom), but each one counts toward the cap."""
        if not self.style.sfx:
            return False
        if self.serious and sound in SERIOUS_DROP_SOUNDS:
            return False
        if self.tier == "style" and not intrinsic:
            return False
        if cue_id in self.taken_ids or cue_id in self.user_ids:
            return False
        if any(s == sound and b == beat_id and abs(t - other) < 0.1 for other, s, b in self.sounds):
            return False
        span = self.quiet(t, t, instant=True, hook_bound=hook_bound)
        if span is None or self.user_collides("sfx", t, t):
            return False
        if not self.family_ok("sfx", t, beat_id, exempt_same_beat=True):
            return False
        n = max(1, int(self.inp.sfx_variants.get(sound, 1) or 1))
        chosen = variant if variant is not None else self.seed_of(beat_id, cue_id) % n
        chosen = max(0, min(15, chosen if chosen < n else 0))
        self.add(_Cand("sfx", cue_id, t, t, beat_id,
                       {"sound": sound, "variant": chosen, "gain_db": SFX_TRIM_DB.get(sound, 0.0)},
                       "sfx", hook_bound))
        self.count("sfx", t, beat_id, counted=counted)
        self.sounds.append((t, sound, beat_id))
        return True

    def zoom(self, b: Beat, purpose: str, mode: str, scale: float, start: float, end: float, *,
             hook_bound: bool = False) -> bool:
        family = "punch" if mode == "punch" else "push"
        cue_id = f"{b.id}.{purpose}"
        if not self.style.zooms or cue_id in self.user_ids:
            return False
        span = self.quiet(start, end, instant=False, hook_bound=hook_bound, min_len=0.3)
        if span is None:
            return False
        start, end = span
        if not self.zoom_ok(start, end) or self.user_collides("zoom", start, end):
            return False
        if not self.family_ok(family, start, b.id):
            return False
        if not self.add(_Cand("zoom", cue_id, start, end, b.id,
                              {"mode": mode, "scale": _r(min(1.6, max(1.0, scale))), "anchor": "face"},
                              family, hook_bound)):
            return False
        self.count(family, start, b.id)
        self.zooms.append((start, end))
        return True

    def shake(self, b: Beat, purpose: str, t: float, length: float, amp: float, *, paired: bool,
              hook_bound: bool = False) -> bool:
        cue_id = f"{b.id}.{purpose}"
        if not self.style.shake or not paired or cue_id in self.user_ids:
            return False
        span = self.quiet(t, t + length, instant=False, hook_bound=hook_bound)
        if span is None:
            return False
        start, end = span
        if self.in_excl(start, end) or self.user_collides("shake", start, end) or not self.hit_ok(start, b.id):
            return False
        if not self.family_ok("shake", start, b.id):
            return False
        if not self.add(_Cand("shake", cue_id, start, end, b.id, {"amp": amp, "freq": 18.0}, "shake", hook_bound)):
            return False
        self.count("shake", start, b.id)
        self.hits.append((start, b.id))
        return True

    def flash_or_glitch(self, kind: str, beat_id: str, purpose: str, t: float, length: float, *,
                        intrinsic: bool, hook_bound: bool = False, amount: float = 0.5) -> bool:
        cue_id = f"{beat_id}.{purpose}"
        if cue_id in self.user_ids:
            return False
        span = self.quiet(t, t + length, instant=False, hook_bound=hook_bound, min_len=2 / FPS)
        if span is None:
            return False
        start, end = span
        if kind == "glitch" and self.in_excl(start, end):
            return False
        if self.user_collides(kind, start, end) or not self.hit_ok(start, beat_id) or not self.ffg_ok(start, end):
            return False
        if not intrinsic and not self.family_ok("hit", start, beat_id):
            return False
        fields = {"amount": _r(amount)} if kind == "glitch" else {"color": "#FFFFFF", "peak": 0.85}
        if not self.add(_Cand(kind, cue_id, start, end, beat_id, fields, "hit", hook_bound)):
            return False
        if not intrinsic:
            self.count("hit", start, beat_id)
        self.hits.append((start, beat_id))
        self.ffg.append((start, end))
        return True

    def desaturate(self, beat_id: str, purpose: str, start: float, end: float, amount: float, dim: float, *,
                   hook_bound: bool = False) -> bool:
        cue_id = f"{beat_id}.{purpose}" if beat_id else purpose
        if cue_id in self.user_ids:
            return False
        span = self.quiet(start, end, instant=False, hook_bound=hook_bound, min_len=0.3)
        if span is None or self.user_collides("desaturate", *span):
            return False
        return self.add(_Cand("desaturate", cue_id, span[0], span[1], beat_id,
                              {"amount": amount, "dim": dim}, "", hook_bound))

    # ------------------------------------------------------------------ warp (rule 7, caps G14)

    def warp_allowed(self) -> bool:
        return (self.style.speed and self.ge("spicy") and not self.serious and self.timing == "word")

    def final_duration(self, extra_hold: float = 0.0, extra_delta: float = 0.0) -> float:
        return self.dur + sum(h for _, h in self.holds) + extra_hold + sum(d for *_, d in self.speeds) + extra_delta

    def warp_caps_ok(self, extra_hold: float = 0.0, extra_delta: float = 0.0) -> bool:
        hold_total = sum(h for _, h in self.holds) + extra_hold
        if hold_total > min(1.6, 0.04 * self.dur) + EPS:
            return False
        delta_total = sum(abs(d) for *_, d in self.speeds) + abs(extra_delta)
        if delta_total > 0.1 * self.dur + EPS:
            return False
        if self.inp.max_clip_seconds is not None and extra_hold + extra_delta > 0:
            if self.final_duration(extra_hold, extra_delta) > float(self.inp.max_clip_seconds) + 2.0 + EPS:
                return False
        return True

    def speed_conflicts(self, e0: float, e1: float) -> bool:
        if any(_overlaps(e0, e1, a, b) for a, b, _ in self.speeds):
            return True
        if any(e0 + EPS < t < e1 - EPS for t, _ in self.holds):
            return True
        for cue in self.user_cues:
            if cue.kind == "speed" and _overlaps(e0, e1, cue.start, cue.end):
                return True
            if cue.kind == "freeze" and e0 + EPS < cue.start < e1 - EPS:
                return True
        return False

    def speed(self, beat_id: str, purpose: str, family: str, e0: float, e1: float, rate: float, *,
              hook_bound: bool = False) -> bool:
        if not self.warp_allowed():
            return False
        cue_id = f"{beat_id}.{purpose}" if beat_id else purpose
        if cue_id in self.user_ids:
            return False
        e0, e1 = _frame(e0), _frame(e1)
        span = self.quiet(e0, e1, instant=False, hook_bound=hook_bound, min_len=0.3)
        if span is None or span[1] != e1:
            return False
        if self.speed_conflicts(e0, e1) or self.user_collides("speed", e0, e1):
            return False
        length = e1 - e0
        delta = round(length / rate * FPS) / FPS - length
        if not self.warp_caps_ok(extra_delta=delta) or not self.family_ok(family, e0, beat_id):
            return False
        if not self.add(_Cand("speed", cue_id, e0, e1, beat_id,
                              {"rate": _r(rate)}, family, hook_bound)):
            return False
        self.count(family, e0, beat_id)
        self.speeds.append((e0, e1, delta))
        return True

    def freeze(self, b: Beat, t: float, hold: float, label: str) -> bool:
        if not self.warp_allowed():
            return False
        cue_id = f"{b.id}.freeze"
        if cue_id in self.user_ids:
            return False
        if self.quiet(t, t, instant=True, hook_bound=False) is None or self.user_collides("freeze", t, t):
            return False
        if any(a + EPS < t < c - EPS for a, c, _ in self.speeds) or any(
            c.kind == "speed" and c.start + EPS < t < c.end - EPS for c in self.user_cues
        ):
            return False
        if self.inside_word(t, margin=0.0):
            return False
        if not self.family_ok("freeze", t, b.id):
            return False
        # Clamp to the clip's total hold allowance (never to what other freezes left, so one beat's hold never
        # depends on another beat); a freeze that no longer fits is dropped (lowest priority first).
        allowance = math.floor((min(1.6, 0.04 * self.dur) + EPS) * FPS) / FPS
        hold = min(_frame(hold), allowance)
        if hold < 0.5 - EPS or not self.warp_caps_ok(extra_hold=hold):
            return False
        if not self.add(_Cand("freeze", cue_id, t, t, b.id,
                              {"hold": _r(hold), "label": label[:40]}, "freeze")):
            return False
        self.count("freeze", t, b.id)
        self.holds.append((t, hold))
        return True

    # ------------------------------------------------------------------ accents (§8.7, §11.1)

    def accent(self, b: Beat, purpose: str, variant: str, start: float, end: float, *, text: str = "",
               emoji: str = "", doodle: str = "sparkles", value: Optional[float] = None, prefix: str = "",
               suffix: str = "", hook_bound: bool = False) -> Optional[_Cand]:
        return self.place_accent(f"{b.id}.{purpose}", b.id, variant, start, end, text=text, emoji=emoji,
                                 doodle=doodle, value=value, prefix=prefix, suffix=suffix, hook_bound=hook_bound)

    def place_accent(self, cue_id: str, beat_id: str, variant: str, start: float, end: float, *,
                     text: str = "", emoji: str = "", doodle: str = "sparkles", value: Optional[float] = None,
                     prefix: str = "", suffix: str = "", hook_bound: bool = False) -> Optional[_Cand]:
        if cue_id in self.user_ids:
            return None
        span = self.quiet(start, end, instant=False, hook_bound=hook_bound, min_len=0.25)
        if span is None:
            return None
        start, end = span
        if variant in EXCLUDED_ACCENTS and self.in_excl(start, end):
            return None
        if self.user_collides("accent", start, end):
            return None
        hides = variant in HIDES_CAPTIONS
        if any(_overlaps(start, end, a, z) for a, z in self.hides):
            return None
        if hides and any(_overlaps(start, end, a, z) for a, z, _ in self.accent_spans):
            return None
        if variant == "slam" and not self.hit_ok(start, beat_id):
            return None
        # Accents of one beat are one mark (face_doodles' funny = sparkles + bang): spaced and counted once.
        sibling = bool(beat_id) and any(c.kind == "accent" and c.beat == beat_id for c in self.placed)
        if not sibling and not self.family_ok("accent", start, beat_id):
            return None
        fields: dict[str, Any] = {"variant": variant, "text": text[:120], "emoji": emoji, "doodle": doodle,
                                  "value": value, "prefix": prefix[:4], "suffix": suffix[:8]}
        cand = _Cand("accent", cue_id, start, end, beat_id, fields, "accent", hook_bound)
        if not self.add(cand):
            return None
        self.count("accent", start, beat_id, counted=not sibling)
        self.accent_spans.append((start, end, variant))
        if hides:
            self.hides.append((start, end))
        if variant == "slam":
            self.hits.append((start, beat_id))
        return cand

    def accent_for(self, b: Beat, words: list[dict], key: dict) -> list[_Cand]:
        """Template accent(s) for one beat (§8.7). Legacy templates get none."""
        if not self.fx_template:
            return []
        kinds = ACCENT_KINDS | ({"swear"} if self.template == "face_doodles" else frozenset())
        if b.kind not in kinds:
            return []
        hb = b.kind == "hook"
        tpl = self.template
        key_words = [key]
        if b.kind == "number" or len(words) <= 2:
            key_words = list(words)
        k0, k1 = key_words[0]["start"], key_words[-1]["end"]
        word_span = (k0, min(max(k1, k0 + 0.8), k0 + 2.5))
        text = self.text_of(key_words)
        out: list[_Cand] = []

        def put(variant: str, start: float, end: float, purpose: str = "accent", **kw) -> Optional[_Cand]:
            c = self.accent(b, purpose, variant, start, end, hook_bound=hb, **kw)
            if c is not None:
                out.append(c)
            return c

        if tpl == "marker_circle":
            negation = next((w for w in words if _norm(w["text"]) in NEGATIONS), None)
            if b.kind == "number":
                put("arrow", *word_span, text=text)
            elif negation is not None:
                put("strike", negation["start"], min(max(negation["end"], negation["start"] + 0.8),
                                                     negation["start"] + 2.5), text=self.text_of([negation]))
            elif b.kind == "punchline":
                put("underline", *word_span, text=text)
            else:
                put("circle", *word_span, text=text)
        elif tpl == "emoji_drop":
            if self.serious:
                return []
            emoji = b.emoji or TONE_EMOJI.get(b.tone, TONE_EMOJI["neutral"])
            burst = (self.tier == "spicy" and b.strength >= 3) or (self.tier == "unhinged" and b.strength >= 2)
            placed = None
            if burst and self.family_ok("burst", key["start"], b.id):
                placed = put("emoji_burst", key["start"], key["start"] + 1.0, purpose="burst", text=text,
                             emoji=emoji, value=5.0 if self.tier == "spicy" else 8.0)
                if placed is not None:
                    self.count("burst", key["start"], b.id)
            if placed is None:
                put("emoji", key["start"], max(key["start"] + 1.2, key["end"] + 0.3), text=text, emoji=emoji)
        elif tpl == "slam":
            if b.id in self.slam_ids:
                phrase = self.key_phrase(words, 1)   # the keyword alone (§11.1)
                s = phrase[0]["start"]
                put("slam", s, min(max(phrase[-1]["end"] + 0.35, s + 0.7), s + 1.6), purpose="slam",
                    text=self.text_of(phrase))
            else:
                put("highlight", *word_span, text=text)
        elif tpl == "behind_head":
            if (b.kind == "hook" or b.strength >= 3) and self.behind_count < 3:
                phrase = self.key_phrase(words, 2)
                s = phrase[0]["start"]
                c = put("behind", s, min(s + 3.0, max(b.end + 0.4, s + 1.4)), purpose="behind",
                        text=self.text_of(phrase))
                if c is not None:
                    self.behind_count += 1
                else:
                    put("highlight", *word_span, text=text)
            else:
                put("highlight", *word_span, text=text)
        elif tpl == "stat_counter":
            if b.kind == "number" and b.value is not None:
                count_dur = max(0.6, (b.end - b.start) + 0.4)
                put("counter", b.start, b.start + count_dur + 0.9, text=self.text_of(words), value=b.value,
                    prefix=b.prefix, suffix=b.suffix)
            elif b.kind == "number":
                put("arrow", *word_span, text=text)
            else:
                put("highlight", *word_span, text=text)
        elif tpl == "sticker_slap":
            label = b.label.strip() if b.label.strip() else self.text_of(self.key_phrase(words, 3), 40)
            s = key_words[0]["start"] if b.kind != "hook" else b.start
            put("sticker", s, s + min(1.6, max(1.0, b.end - b.start + 0.5)), text=label[:40])
        elif tpl == "kinetic_stack":
            if b.kind == "hook":
                first, last = self.sentence_bounds(b.start)
                if first is not None:
                    start_i = next((i for i, w in enumerate(self.words) if w["start"] >= b.start - 0.02), first)
                    last = min(last, start_i + 11)
                    sentence = self.words[start_i:last + 1] or words
                else:
                    sentence = words
                put("kinetic", sentence[0]["start"], sentence[-1]["end"] + 0.3, text=self.text_of(sentence))
            else:
                put("highlight", *word_span, text=text)
        elif tpl == "decode_glitch":
            put("decode", key_words[0]["start"], max(key_words[-1]["end"] + 0.3, key_words[0]["start"] + 0.6),
                text=text)
        elif tpl == "highlighter_swipe":
            if b.kind in ("emphasis", "number"):
                put("highlight", *word_span, text=text)
        elif tpl == "face_doodles":
            if self.serious:
                return []
            label = b.label.strip() if b.label.strip() else self.text_of(self.key_phrase(words, 3), 40)
            length = min(2.0, max(1.2, (b.end - b.start) + 0.8))
            s = key["start"]
            for i, kind in enumerate(self.doodle_kinds(b)):
                put("doodle", s + 0.15 * i, s + 0.15 * i + length, purpose="accent" if i == 0 else "accent2",
                    text=label[:40], doodle=kind)
        return out

    def doodle_kinds(self, b: Beat) -> list[str]:
        tone = b.tone
        if b.kind == "swear" or tone == "angry":
            return ["horns" if hash01(self.cseed(b.id, "doodle"), 0) < 0.5 else "anger"]
        if tone == "wholesome" and b.strength >= 3:
            return ["heart"]
        if tone in ("hype", "wholesome"):
            return ["sparkles"]
        if tone == "funny":
            return ["sparkles", "bang"]
        if tone == "shocked":
            return ["bang"]
        if tone in ("awkward", "sad"):
            return ["sweat"]
        if tone == "smug":
            return ["horns"]
        return ["arrows"]

    def accent_intrinsics(self, b: Beat, cands: list[_Cand]) -> None:
        """Template- and effect-intrinsic visuals and SFX (§8.4 table, §8.7), placed before bundle SFX."""
        for c in cands:
            v = c.fields["variant"]
            hb = c.hook_bound
            base = c.id
            if v == "slam":
                self.flash_or_glitch("flash", b.id, "slam.flash", c.start, 0.1, intrinsic=True, hook_bound=hb)
                self.sfx(b.id, f"{base}.sfx", "boom" if self.tier == "unhinged" and not self.serious else "impact",
                         c.start, intrinsic=True, hook_bound=hb)
                if self.ge("spicy"):
                    self.shake(b, "slam.shake", c.start, 0.25, 14.0, paired=True, hook_bound=hb)
                    self.flash_or_glitch("glitch", b.id, "slam.glitch", c.start + 0.1, 0.12, intrinsic=False,
                                         hook_bound=hb, amount=0.5)
            elif v == "sticker":
                self.sfx(b.id, f"{base}.sfx", "slap", c.start, intrinsic=True, hook_bound=hb)
            elif v == "counter":
                lock = c.start + max(0.6, (b.end - b.start) + 0.4)
                self.sfx(b.id, f"{base}.sfx", "ticker", c.start, intrinsic=True, hook_bound=hb, counted=False)
                lock_sound = "cash_register" if self.ge("spicy") and b.prefix in CURRENCY_PREFIXES else "stamp"
                if not self.sfx(b.id, f"{base}.sfx2", lock_sound, lock, intrinsic=True, hook_bound=hb):
                    if lock_sound != "stamp":
                        self.sfx(b.id, f"{base}.sfx2", "stamp", lock, intrinsic=True, hook_bound=hb)
            elif v == "emoji":
                if not any(s == "pop" and abs(t - c.start) < 0.6 for t, s, _ in self.sounds):
                    self.sfx(b.id, f"{base}.sfx", "pop", c.start, intrinsic=True, hook_bound=hb)
            elif v == "emoji_burst":
                self.sfx(b.id, f"{base}.sfx", "pop", c.start, intrinsic=True, hook_bound=hb,
                         variant=1 if int(self.inp.sfx_variants.get("pop", 0) or 0) > 1 else 0)
            elif v == "decode":
                self.sfx(b.id, f"{base}.sfx", "glitch", c.start, intrinsic=True, hook_bound=hb)
                if self.ge("spicy"):
                    self.flash_or_glitch("glitch", b.id, f"{c.id[len(b.id) + 1:]}.glitch", c.start, 0.08,
                                         intrinsic=False, hook_bound=hb, amount=0.4)
            elif v in ("behind", "kinetic"):
                self.sfx(b.id, f"{base}.sfx", "whoosh", c.start, intrinsic=True, hook_bound=hb)
                if v == "kinetic":
                    self.desaturate(b.id, "desat", c.start, c.end, 0.7, 0.2, hook_bound=hb)
            elif v == "doodle":
                if b.strength >= 2:
                    self.sfx(b.id, f"{base}.sfx", "pop", c.start, intrinsic=True, hook_bound=hb)

    # ------------------------------------------------------------------ bundles (§8.4)

    def bundle(self, b: Beat) -> None:
        words = self.beat_words(b)
        key = self.key_word(words)
        accents = self.accent_for(b, words, key)
        self.accent_intrinsics(b, accents)
        if self.tier == "style":
            return
        elig = self.eligible(b)
        handler: Callable[[Beat, list[dict], dict, bool, list[_Cand]], None] = getattr(self, f"_b_{b.kind}")
        handler(b, words, key, elig, accents)

    def _b_hook(self, b, words, key, elig, accents) -> None:
        if not elig:
            return
        self.sfx(b.id, f"{b.id}.sfx", "whoosh", b.start, intrinsic=False, hook_bound=True)
        hook_end = self.beats_meta.hook_end if self.beats_meta.hook_end is not None else b.end
        end = min(b.start + 6.0, max(hook_end, b.end, b.start + 1.0))
        scale = {"chill": 1.04, "spicy": 1.06, "unhinged": 1.08}[self.tier]
        self.zoom(b, "push", "push", scale, b.start, end, hook_bound=True)
        if self.ge("spicy"):
            self.zoom(b, "zoom", "punch", 1.12, key["start"], key["start"] + self.punch_len(key), hook_bound=True)
        if self.tier == "unhinged":
            self.flash_or_glitch("flash", b.id, "flash", key["start"], 0.15, intrinsic=False, hook_bound=True)
            boom = self.sfx(b.id, f"{b.id}.sfx2", "boom", key["start"], intrinsic=False, hook_bound=True)
            self.shake(b, "shake", key["start"], 0.35, 18.0, paired=boom or not self.style.sfx, hook_bound=True)

    def _b_emphasis(self, b, words, key, elig, accents) -> None:
        if not elig or not self.ge("spicy"):
            return
        self.sfx(b.id, f"{b.id}.sfx", "pop", key["start"], intrinsic=False)
        if self.tier == "unhinged":
            scale = self.rand(b.id, "zoom", 1.15, 1.25)
            self.zoom(b, "zoom", "punch", scale, key["start"], key["start"] + self.punch_len(key))
        elif b.strength >= 2:
            self.zoom(b, "zoom", "punch", 1.15, key["start"], key["start"] + self.punch_len(key))

    def _b_number(self, b, words, key, elig, accents) -> None:
        if not elig:
            return
        counter = next((c for c in accents if c.fields["variant"] == "counter"), None)
        hit_at = key["start"]
        if counter is not None:
            # The counter's lock sound is the number's stamp; the shake lands on the lock with it.
            hit_at = counter.start + max(0.6, (b.end - b.start) + 0.4)
            sound_ok = any(s in IMPACT_SOUNDS and bb == b.id and abs(t - hit_at) < 0.05 for t, s, bb in self.sounds)
        else:
            sound = "cash_register" if self.ge("spicy") and b.prefix in CURRENCY_PREFIXES else "stamp"
            sound_ok = self.sfx(b.id, f"{b.id}.sfx", sound, key["start"], intrinsic=False)
            if not sound_ok and sound != "stamp":
                sound_ok = self.sfx(b.id, f"{b.id}.sfx", "stamp", key["start"], intrinsic=False)
        if self.tier == "unhinged":
            self.zoom(b, "zoom", "punch", 1.18, key["start"], key["start"] + self.punch_len(key))
            self.shake(b, "shake", hit_at, 0.25, 10.0, paired=sound_ok or not self.style.sfx)

    def _b_punchline(self, b, words, key, elig, accents) -> None:
        if not elig or not self.ge("spicy"):
            return
        self.zoom(b, "zoom", "punch", 1.15, key["start"], key["start"] + self.punch_len(key))
        self.sfx(b.id, f"{b.id}.sfx", "boom" if b.strength >= 3 else "pop", key["start"], intrinsic=False)
        if self.tier == "unhinged":
            self.flash_or_glitch("glitch", b.id, "glitch", key["start"], 0.15, intrinsic=False, amount=0.6)
            self.slow_tail(b, words)

    def _b_swear(self, b, words, key, elig, accents) -> None:
        # §8.4: the glitch sits ON the bleep, so there is none without one (censor off, approx timing, user-deleted).
        bleep = f"{b.id}.bleep"
        if self.tier == "unhinged" and bleep in self.taken_ids and not self.is_suppressed(bleep):
            self.flash_or_glitch("glitch", b.id, "glitch", b.start, 0.1, intrinsic=False, amount=0.5)

    def _b_emotional_turn(self, b, words, key, elig, accents) -> None:
        if not elig:
            return
        first, _last = self.sentence_bounds(b.start)
        # Push over the sentence that turns (at least 1 s, at most the last 4 s of it).
        sentence_start = self.words[first]["start"] if first is not None else b.start
        start = max(sentence_start, b.end - 4.0)
        if b.end - start < 1.0:
            start = max(0.0, b.end - 1.0)
        self.zoom(b, "push", "push", 1.04 if self.tier == "chill" else 1.06, start, b.end)
        if self.ge("spicy"):
            self.riser(b, b.start)
        if self.tier == "unhinged":
            self.desaturate(b.id, "desat", b.start, b.start + 1.0, 0.6, 0.1)

    def _b_build(self, b, words, key, elig, accents) -> None:
        if not elig or not self.ge("spicy"):
            return
        self.zoom(b, "push", "push", 1.06, b.start, max(b.end, b.start + 1.0))
        landing = next((o for o in sorted(self.beats, key=lambda o: (o.start, o.id))
                        if o.kind in ("payoff", "punchline") and o.start >= b.end - EPS and o.start - b.end <= 8.0),
                       None)
        self.riser(b, landing.start if landing is not None else b.end)
        lo, hi = (1.15, 1.25) if self.tier == "spicy" else (1.2, 1.35)
        runs = [list(words)]
        if self.tier == "unhinged":
            # A wordless span ≥ 0.6 s inside the build plays fast; the speech around it keeps the ramp.
            for i, (prev, nxt) in enumerate(zip(words, words[1:])):
                if nxt["start"] - prev["end"] >= 0.6 - EPS:
                    rate = self.rand(b.id, "fast", 1.5, 2.5)
                    if self.speed(b.id, "fast", "fast", prev["end"] + 0.1, nxt["start"] - 0.1, rate):
                        runs = [list(words[:i + 1]), list(words[i + 1:])]
                        break
        run = max(runs, key=lambda r: (r[-1]["end"] - r[0]["start"], -r[0]["start"]))
        self.speech_ramp(b, run, self.rand(b.id, "ramp", lo, hi))

    def _b_payoff(self, b, words, key, elig, accents) -> None:
        if not elig or not self.ge("spicy"):
            return
        self.wait_for_it(b)
        self.zoom(b, "zoom", "punch", 1.15, key["start"], key["start"] + self.punch_len(key))
        self.sfx(b.id, f"{b.id}.sfx", "impact", key["start"], intrinsic=False)
        froze = False
        if self.tier == "unhinged" or (b.strength >= 3 and self.dur >= 15.0):
            froze = self.payoff_freeze(b, words)
        if not froze and self.tier == "unhinged":
            self.slow_tail(b, words)

    # ------------------------------------------------------------------ bundle pieces

    @staticmethod
    def punch_len(key: dict) -> float:
        return min(1.4, max(0.5, (key["end"] - key["start"]) + 0.35))

    def riser(self, b: Beat, end_at: float) -> bool:
        n = max(1, int(self.inp.sfx_variants.get("riser", 1) or 1))
        options = [v for v in range(min(n, len(RISER_SECONDS))) if end_at - RISER_SECONDS[v] >= QUIET_HEAD - EPS]
        if not options:
            return False
        pick = options[self.cseed(b.id, "riser") % len(options)]
        return self.sfx(b.id, f"{b.id}.sfx2", "riser", end_at - RISER_SECONDS[pick], intrinsic=False, variant=pick)

    def speech_ramp(self, b: Beat, words: list[dict], rate: float) -> bool:
        if not self.warp_allowed() or not words:
            return False
        first, last = words[0], words[-1]
        prev = self.prev_word_before(first["start"] - EPS)
        nxt = self.next_word_after(last["end"] + EPS)
        pad0 = min(0.05, (first["start"] - prev["end"]) / 2) if prev else 0.05
        pad1 = min(0.05, (nxt["start"] - last["end"]) / 2) if nxt else 0.05
        e0, e1 = first["start"] - max(0.0, pad0), last["end"] + max(0.0, pad1)
        if e1 - e0 < 0.4:
            return False
        # The span carries no bleep, no swear, no caption-hiding accent and does not touch the hook.
        if any(_overlaps(e0, e1, a, z) for a, z in self.swear_spans) or any(
            o.kind == "swear" and _overlaps(e0, e1, o.start, o.end) for o in self.beats
        ):
            return False
        if any(c.kind == "bleep" and _overlaps(e0, e1, c.start, c.end) for c in self.placed):
            return False
        if any(_overlaps(e0, e1, a, z) for a, z in self.hides):
            return False
        hook_spans = [self.inp.hook_window] if self.inp.hook_window else []
        if self.hook_beat is not None:
            hook_spans.append((self.hook_beat.start, self.hook_beat.end))
        if any(_interval_gap(e0, e1, a, z) <= EPS for a, z in hook_spans):
            return False
        return self.speed(b.id, "ramp", "ramp", e0, e1, rate)

    def slow_tail(self, b: Beat, words: list[dict]) -> bool:
        last = words[-1]
        nxt = self.next_word_after(last["end"] + EPS)
        gap_end = nxt["start"] if nxt else self.dur
        gap = gap_end - last["end"]
        if gap < 0.25 - EPS or gap > 1.2 + EPS:
            return False
        e0 = math.ceil(last["end"] * FPS - 1e-6) / FPS
        e1 = math.floor(gap_end * FPS + 1e-6) / FPS
        if e1 - e0 < 0.2:
            return False
        if not self.speed(b.id, "slow", "slow", e0, e1, 0.5):
            return False
        self.sfx(b.id, f"{b.id}.slow.sfx", "tape_stop", e0, intrinsic=True)
        return True

    def payoff_freeze(self, b: Beat, words: list[dict]) -> bool:
        last = words[-1]
        nxt = self.next_word_after(last["end"] + EPS)
        gap_end = nxt["start"] if nxt else self.dur
        if gap_end - last["end"] < 0.3 - EPS:
            return False
        t = math.ceil(last["end"] * FPS - 1e-6) / FPS
        if gap_end - t < 0.3 - EPS:                 # rule 7: the freeze sits ≥ 0.3 s before the next word
            return False
        hi = 0.8 if self.tier == "spicy" else 1.0
        hold = self.rand(b.id, "freeze", 0.6, hi)
        label = b.label.strip() or self.text_of(self.key_phrase(words, 3), 40)
        if not self.freeze(b, t, hold, label):
            return False
        self.sfx(b.id, f"{b.id}.freeze.sfx", "record_scratch", t, intrinsic=True)
        return True

    def wait_for_it(self, b: Beat) -> bool:
        if not self.style.wait_for_it:
            return False
        hook_end = self.beats_meta.hook_end or 0.0
        lead = 6.0 if self.tier == "spicy" else 4.0
        if b.start - hook_end < lead - EPS:
            return False
        if self.tier == "spicy" and self.dur < 20.0:
            return False
        end = b.start - 0.15
        floor = max(hook_end + 0.3, QUIET_HEAD)
        if self.inp.hook_window:
            floor = max(floor, self.inp.hook_window[1])
        start = max(end - 1.6, floor)
        if end - start < 0.8 - EPS:
            return False
        if "g.wait" in self.user_ids or self.user_collides("sticker", start, end):
            return False
        span = self.quiet(start, end, instant=False, hook_bound=False, min_len=0.8)
        if span is None or not self.family_ok("wait", span[0], ""):
            return False
        if not self.add(_Cand("sticker", "g.wait", span[0], span[1], b.id,
                              {"sticker": "wait_for_it", "text": ""}, "wait")):
            return False
        self.count("wait", span[0], "")
        self.sfx(b.id, "g.wait.sfx", "slap", span[0], intrinsic=True)
        return True

    # ------------------------------------------------------------------ global steps

    def place_user_cues(self) -> None:
        family_of = {"accent": "accent", "shake": "shake", "sfx": "sfx", "flash": "hit", "glitch": "hit",
                     "freeze": "freeze", "reaction": "reaction"}
        for c in self.user_cues:
            if c.kind == "zoom":
                family = "punch" if c.mode == "punch" else "push"
            elif c.kind == "speed":
                family = "slow" if c.rate < 1 else "ramp"
            elif c.kind == "sticker" and c.sticker == "wait_for_it":
                family = "wait"
            else:
                family = family_of.get(c.kind, "")
            if family:
                self.count(family, c.start, "")
            if c.kind in ("flash", "glitch", "transition", "shake") or (c.kind == "accent" and c.variant == "slam"):
                self.hits.append((c.start, ""))
            if c.kind in ("flash", "glitch"):
                self.ffg.append((c.start, c.end))
            elif c.kind == "transition":
                half = TRANSITION_HALF.get(c.style, 0.1)
                self.ffg.append((c.start - half, c.start + half))
                self.transitions.append(c.start)
            elif c.kind == "zoom":
                self.zooms.append((c.start, c.end))
            elif c.kind == "sfx":
                self.sounds.append((c.start, c.sound, ""))
            elif c.kind == "freeze":
                self.holds.append((c.start, c.hold))
            elif c.kind == "speed":
                length = c.end - c.start
                self.speeds.append((c.start, c.end, round(length / c.rate * FPS) / FPS - length))
            elif c.kind == "reaction":
                self.reaction_windows.append((c.start, c.end))
            elif c.kind == "accent":
                self.accent_spans.append((c.start, c.end, c.variant))
                if c.variant in HIDES_CAPTIONS:
                    self.hides.append((c.start, c.end))

    def place_bleeps(self) -> None:
        """Rule 2: compliance, not decoration. Word timing only; budgets and spacing ignored.

        Style-only clips (intensity off) change no audio, so they get no bleeps (coordinator decision D3).
        """
        if not self.style.active or self.style.censor == "off" or self.timing != "word":
            return
        cover = {"bleep": "none", "bleep_bar": "bar", "bleep_pixelate": "pixelate"}[self.style.censor]
        for b in self.beats:
            if b.kind != "swear":
                continue
            start = max(0.0, b.start - 0.02)
            end = min(self.dur, b.end + 0.02)
            cue_id = f"{b.id}.bleep"
            if end <= start or cue_id in self.user_ids or self.user_collides("bleep", start, end):
                continue
            self.add(_Cand("bleep", cue_id, start, end, b.id, {"tone": True, "cover": cover}))

    def place_reactions(self) -> None:
        """Rule 11: one ReactionCue per kept idea, 2.0 s from the word, never in B-roll windows."""
        if not (self.style.reactions and self.inp.reactions_available) or self.serious or not self.ge("spicy"):
            return
        if self.timing == "none":
            return
        landings = [o for o in self.beats if o.kind in ("punchline", "payoff")]

        def rank(item):
            k, idea = item
            near = any(abs(o.start - idea.at) <= 1.5 or o.start - EPS <= idea.at <= o.end + EPS for o in landings)
            return (not near, idea.at, k)

        for k, idea in sorted(enumerate(self.beats_meta.reactions), key=rank):
            cue_id = f"g.react{k}"
            if cue_id in self.user_ids:
                continue
            span = self.quiet(idea.at, idea.at + 2.0, instant=False, hook_bound=False, min_len=1.0)
            if span is None:
                continue
            start, end = span
            if self.in_excl(start, end) or self.user_collides("reaction", start, end):
                continue
            if not self.family_ok("reaction", start, cue_id):
                continue
            if self.add(_Cand("reaction", cue_id, start, end, "",
                              {"query": idea.query[:60], "provider": "giphy", "asset_id": "", "url": ""},
                              "reaction")):
                self.count("reaction", start, cue_id)
                self.reaction_windows.append((start, end))

    def place_transitions(self) -> None:
        """Rule 6: visual-only transitions at junction centres, ranked by the strongest beat within 2 s."""
        if self.tier == "style" or not self.style.transitions:
            return
        cap = transition_cap(self.tier, self.dur)
        breaks = [float(p) for p in self.inp.passage_breaks]

        def is_break(c: float) -> bool:
            return any(abs(c - p) <= 1e-3 for p in breaks)

        def strength_near(c: float) -> tuple[int, int]:
            near = [(o.strength, PRIORITY[o.kind]) for o in self.beats if _interval_gap(o.start, o.end, c, c) <= 2.0]
            return max(near) if near else (0, -1)

        candidates = []
        for k, c in enumerate(self.inp.cuts):
            c = float(c)
            if self.tier == "chill" and not is_break(c):
                continue
            candidates.append((not is_break(c), tuple(-x for x in strength_near(c)), c, k))
        placed = 0
        for _brk, _strength, c, k in sorted(candidates):
            if placed >= cap:
                break
            cue_id = f"t{k}.tr"
            if cue_id in self.user_ids:
                continue
            pool = list(TRANSITION_POOLS[self.tier])
            if self.template == "decode_glitch":
                pool += ["glitch", "glitch"]
            mood = self.beats_meta.mood
            if self.serious or mood in CALM_MOODS:
                pool = [s for s in pool if s in ("light_leak", "flash", "whip")] + ["light_leak"]
            style = pool[self.seed_of("", cue_id) % len(pool)]
            half = TRANSITION_HALF[style]
            if self.quiet(c, c, instant=True, hook_bound=False) is None or c < QUIET_HEAD:
                continue
            if self.inside_word(c) or self.in_excl(c - half, c + half):
                continue
            if self.user_collides("transition", c, c) or not self.hit_ok(c, cue_id) or not self.ffg_ok(c - half,
                                                                                                        c + half):
                continue
            if any(_interval_gap(a, z, c, c) < ZOOM_TRANSITION_GAP - EPS for a, z in self.zooms):
                continue
            if not self.add(_Cand("transition", cue_id, c, c, "", {"style": style}, "transition")):
                continue
            placed += 1
            self.hits.append((c, cue_id))
            self.ffg.append((c - half, c + half))
            self.transitions.append(c)
            if style in ("whip", "zoom_through", "spin"):
                self.sfx("", f"{cue_id}.sfx", "whoosh", max(0.0, c - 0.12), intrinsic=True)
            elif style == "glitch":
                self.sfx("", f"{cue_id}.sfx", "glitch", c, intrinsic=True)

    def place_kinetic_without_hook(self) -> None:
        """kinetic_stack's signature needs a hook sentence; without a hook beat, the first sentence plays that role."""
        if self.template != "kinetic_stack" or not self.words or self.timing == "none":
            return
        if any(c.kind == "accent" and c.variant == "kinetic" for c in self.user_cues):
            return
        if self.hook_beat is not None:
            return
        _first, last = self.sentence_bounds(self.words[0]["start"])
        sentence = self.words[: min((last or 0), 11) + 1]
        c = self.place_accent("g.kinetic", "", "kinetic", sentence[0]["start"], sentence[-1]["end"] + 0.3,
                              text=self.text_of(sentence), hook_bound=True)
        if c is not None:
            self.sfx("", "g.kinetic.sfx", "whoosh", c.start, intrinsic=True, hook_bound=True)
            self.desaturate("", "g.kinetic.desat", c.start, c.end, 0.7, 0.2, hook_bound=True)

    def place_highlighter_words(self) -> None:
        """highlighter_swipe at unhinged: every content word ≥4 letters (accent caps apply)."""
        if self.template != "highlighter_swipe" or self.tier != "unhinged" or self.timing == "none":
            return
        for i, w in enumerate(self.words):
            text = str(w.get("text", ""))
            if _letters(text) < 4 or _norm(text) in STOPWORDS:
                continue
            if any(_overlaps(w["start"], w["end"], a, z) for a, z, _ in self.accent_spans):
                continue
            self.place_accent(f"w{i}.accent", "", "highlight", w["start"],
                              min(max(w["end"], w["start"] + 0.8), w["start"] + 2.5), text=self.censored(w))

    def place_wordless_speedups(self) -> None:
        """Unhinged: wordless spans ≥0.6 s play at 1.5-2.5x (0.1 s margin each side), away from landings."""
        if self.tier != "unhinged" or not self.warp_allowed():
            return
        guarded = [(o.start - 0.3, o.end + 0.3) for o in self.beats
                   if o.kind in ("payoff", "punchline", "hook", "swear")]
        gaps = []
        for i, (prev, nxt) in enumerate(zip(self.words, self.words[1:])):
            g0, g1 = prev["end"], nxt["start"]
            if g1 - g0 >= 0.6 - EPS and not any(_overlaps(g0, g1, a, z) for a, z in guarded):
                gaps.append((-(g1 - g0), i, g0, g1))
        for _neg, i, g0, g1 in sorted(gaps):
            if self.counts.get("fast", 0) >= family_cap(self.budget("fast"), self.dur):
                break
            rate = 1.5 + 1.0 * hash01(self.seed_of("", f"g.fast{i}"), 0)
            self.speed("", f"g.fast{i}", "fast", g0 + 0.1, g1 - 0.1, rate)

    # ------------------------------------------------------------------ globals (rules 9, 10, 5)

    def music(self, cues: Sequence[FxCue]) -> Optional[MusicBed]:
        if not (self.style.active and self.style.music):
            return None
        if self.serious:
            mood = None if self.style.music_mood == "auto" else "inspiring"
        else:
            mood = self.beats_meta.mood if self.style.music_mood == "auto" else self.style.music_mood
        if mood is None:
            return None
        ids = tuple(self.inp.beds.get(mood, ()))
        if not ids:
            return None
        bed = ids[self.seed_of("", "g.music") % len(ids)]
        gain, duck = MUSIC_LEVELS[self.tier]
        if self.timing == "none":
            gain, duck = NO_TIMING_MUSIC_DB, 0.0
        offset, fade_in, fade_out = 0.0, 0.5, 0.8
        if self.loop:
            length = float(self.inp.bed_lengths.get(bed, 0.0) or 0.0)
            # F length of the emitted cues through the real warp, so the bed seam lands on the clip seam exactly.
            out = build_warp(cues, self.dur).out_duration
            if length > 0:
                offset = (length - out % length) % length
            fade_in = fade_out = 0.02
        return MusicBed(mood=mood, bed=bed, gain_db=gain, duck_db=duck, offset=_r(offset), fade_in=fade_in,
                        fade_out=fade_out)

    def grade(self) -> str:
        if not self.style.active:
            return "none"
        if self.style.grade != "auto":
            return self.style.grade
        if self.tier == "spicy":
            return "punchy"
        if self.tier == "unhinged":
            return "teal_orange" if self.beats_meta.mood in ("dramatic", "dark") else "punchy"
        return "none"

    # ------------------------------------------------------------------ run

    def run(self) -> FxPlan:
        self.place_user_cues()
        self.slam_ids = self.pick_slams()
        self.place_bleeps()
        self.place_reactions()
        self.place_transitions()
        self.place_kinetic_without_hook()      # stands in for the hook treatment, so it goes before the bundles
        order = sorted(self.beats, key=lambda o: (-PRIORITY[o.kind], -o.strength, o.start, o.id))
        for b in order:
            self.bundle(b)
        self.place_highlighter_words()
        self.place_wordless_speedups()

        cues: list[FxCue] = list(self.user_cues)
        for c in self.placed:
            if self.is_suppressed(c.id):
                continue
            cue = self.build(c)
            if cue is not None:
                cues.append(cue)
        cues.sort(key=lambda c: (c.start, c.id))
        if len(cues) > 400:
            keep = {c.id for c in cues if c.origin == "user" or c.kind == "bleep"}
            budget = 400 - len(keep)
            rest = [c for c in cues if c.id not in keep][:max(0, budget)]
            allowed = keep | {c.id for c in rest}
            cues = [c for c in cues if c.id in allowed]

        music = self.music(cues)
        hook_title_suppressed = self.template == "kinetic_stack" or (
            self.inp.hook_window is not None and any(
                c.kind == "accent" and c.variant == "slam"
                and self.inp.hook_window[0] - EPS <= c.start < self.inp.hook_window[1]
                for c in cues
            )
        )
        active = self.style.active
        return FxPlan(
            style=self.style,
            template=self.template,
            seed=self.seed,
            edit_duration=self.dur,
            sensitivity=self.beats_meta.sensitivity,
            timing=self.timing,
            mood=music.mood if music is not None else self.beats_meta.mood,
            cues=tuple(cues),
            music=music,
            grade=self.grade(),
            grain=GRAIN.get(self.tier, 0.0) if active and self.style.grain else 0.0,
            progress_bar=bool(active and self.style.progress_bar),
            loop_ending="crossfade" if self.loop else "none",
            hook_title_suppressed=hook_title_suppressed,
        )

    def is_suppressed(self, cue_id: str) -> bool:
        """Rule 12. A deleted cue still holds its budget (the rest of the plan stays put), and the cues that only
        exist because of it (`<id>.sfx`, `<id>.flash`, `<id>.glitch`, ...: its intrinsic sound and hits) go with it."""
        suppressed = self.inp.suppressed
        if not suppressed:
            return False
        if cue_id in suppressed:
            return True
        parts = cue_id.split(".")
        return any(".".join(parts[:i]) in suppressed for i in range(2, len(parts)))

    def pick_slams(self) -> frozenset[str]:
        if self.template != "slam" or not self.beats:
            return frozenset()
        n = max(1, min(3, int(self.dur // 20) + 1))
        pool = [b for b in self.beats if b.kind in SLAM_TEMPLATE_KINDS]
        strongest = sorted(pool, key=lambda o: (-o.strength, -PRIORITY[o.kind], o.start, o.id))
        # §8.4: the hook's template treatment is the slam (any strength); §8.7: the strongest strength-3 hook,
        # punchline and payoff beats fill the remaining slots (1-3 per clip by length).
        hook = [self.hook_beat] if self.hook_beat is not None else []
        top = hook + [b for b in strongest if b.strength >= 3 and b not in hook][:max(0, n - len(hook))]
        if not top:
            top = [b for b in strongest if b.strength >= 2][:1]
        return frozenset(b.id for b in top)

    def build(self, c: _Cand) -> Optional[FxCue]:
        if len(c.id) > MAX_CUE_ID:
            return None
        start = _r(min(max(c.start, 0.0), self.dur))
        end = _r(min(max(c.end, 0.0), self.dur))
        if c.kind in ("sfx", "freeze", "transition"):
            end = start
        elif end <= start:
            return None
        common = {"id": c.id, "start": start, "end": end, "seed": self.seed_of(c.beat, c.id), "beat": c.beat[:24]}
        f = c.fields
        kind = c.kind
        if kind == "accent":
            return AccentCue(**common, **f)
        if kind == "zoom":
            return ZoomCue(**common, **f)
        if kind == "shake":
            return ShakeCue(**common, **f)
        if kind == "flash":
            return FlashCue(**common, **f)
        if kind == "glitch":
            return GlitchCue(**common, **f)
        if kind == "desaturate":
            return DesaturateCue(**common, **f)
        if kind == "transition":
            return TransitionCue(**common, **f)
        if kind == "sfx":
            return SfxCue(**common, **f)
        if kind == "bleep":
            return BleepCue(**common, **f)
        if kind == "speed":
            return SpeedCue(**common, **f)
        if kind == "freeze":
            return FreezeCue(**common, **f)
        if kind == "sticker":
            return StickerCue(**common, **f)
        if kind == "reaction":
            return ReactionCue(**common, **f)
        return None


def resolve(inp: ResolveInput, style: EditStyle, template: str, seed: int) -> FxPlan:
    """§8: beats + style + template → `FxPlan` (cue times in E). Never emits kb{n} pushes (AR-M9)."""
    return _Planner(inp, style, template, seed).run()
