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

Order of work (§8.2, §8.5; fx-v2 follows the reference study, docs/dynamic-edits.md "Reference edits"):
  1. user cues (kept verbatim; they occupy budget and spacing)
  2. bleeps (compliance: ignore budgets and spacing)
  3. reactions (their windows exclude zoom/shake/transition/glitch/behind/doodle)
  4. transitions at real cuts only: hard cuts by default, a 3-frame flash or a 6-frame light leak, rarely
  5. look templates (highlighter, emoji drop, behind the head): at most one keyword per caption phrase group,
     behind-the-head words (each lands on a reframe), then emoji under keywords
  6. reframes: hard punch-ins that HOLD until the next reframe or cut, alternating in and out, on beat key words and
     sentence starts; then short 11-frame eased "nudge" zooms on emphasis words, held until the next reframe
  7. hidden templates' own accents (slam, counter, ...) per beat, and "wait for it" before a payoff
  8. sound effects on visual events only (reframes, graphic pop-ins, transitions), never on plain talking
  9. music (a steady bed under the voice), grade, grain, retention, hook band
Shake, glitch/flash/desaturate hits, speed ramps, slow-motion tails, freezes, risers and emoji bursts are never placed
automatically (none of the 26 reference clips used them); user cues of those kinds still render.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .caption_groups import LOOK_LEAD, LOOK_MAX_WORDS, PhraseGroup, grouped_words, phrase_groups
from .censor import censor_text
from .constants import TONE_EMOJI
from .contracts import (
    LOOK_TEMPLATES,
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
    caption_look_for,
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
TIERS: tuple[str, ...] = ("style", "subtle", "bold", "max")
_RANK = {tier: i for i, tier in enumerate(TIERS)}

# Family budgets (§8.3). ("rate", per-60-s rate, min gap s) or ("count", per-clip count, 0). fx-v2: the families the
# references never use (shake, hits, time warp, bursts, slow pushes) have no automatic budget; reframes, nudges,
# keywords, emoji and behind words have their own per-10-s tables below.
Budget = tuple[str, float, float]
_NONE: Budget = ("count", 0, 0)
BUDGETS: dict[str, dict[str, Budget]] = {
    "accent":   {"style": ("rate", 10, 3.0), "subtle": ("rate", 10, 3.0), "bold": ("rate", 20, 1.5),
                 "max": ("rate", 40, 0.8)},
    "punch":    {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "push":     {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "shake":    {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "sfx":      {"style": ("rate", 10, 1.0), "subtle": ("rate", 6, 2.5), "bold": ("rate", 18, 0.8),
                 "max": ("rate", 36, 0.35)},
    "hit":      {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "freeze":   {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "ramp":     {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "fast":     {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "slow":     {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "burst":    {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
    "reaction": {"style": _NONE, "subtle": _NONE, "bold": ("count", 1, 0), "max": ("count", 2, 0)},
    "wait":     {"style": _NONE, "subtle": _NONE, "bold": _NONE, "max": _NONE},
}
# Transitions at real cuts (spec §3.6): none at subtle; bold ≤ 1 per 40 s (0.25 per 10 s) on passage breaks only;
# max ≤ 1 per 20 s (0.5 per 10 s).
TRANSITION_EVERY: dict[str, float] = {"bold": 40.0, "max": 20.0}
TRANSITION_POOLS: dict[str, tuple[str, ...]] = {"bold": ("flash",), "max": ("flash", "light_leak", "flash")}

# Reframes (spec §3.5): hard punch events per 10 s, minimum spacing, punch-in scale range. An "in" event steps to the
# scale and holds it to the next event (the "out", back to the base framing) or the next real cut.
REFRAME_RATE: dict[str, float] = {"subtle": 1.2, "bold": 2.6, "max": 5.5}   # events (in + out): spec punch-ins x2
REFRAME_GAP: dict[str, float] = {"subtle": 3.0, "bold": 1.2, "max": 0.8}
REFRAME_SCALE: dict[str, tuple[float, float]] = {"subtle": (1.10, 1.15), "bold": (1.12, 1.20), "max": (1.20, 1.35)}
# Nudges: +6-10 % over 11 frames, then held to the next reframe.
NUDGE_RATE: dict[str, float] = {"subtle": 0.5, "bold": 2.5, "max": 4.5}
NUDGE_SCALE: dict[str, tuple[float, float]] = {"subtle": (1.05, 1.06), "bold": (1.06, 1.08), "max": (1.08, 1.10)}
NUDGE_GAP = 1.0            # a nudge starts at least this long after/before any reframe or nudge
NUDGE_FRAMES = 11          # length of a nudge's ramp (== fx-core NUDGE_SECONDS * 30)
NUDGE_MIN_HOLD = 0.6       # ... and holds at least this long before the next one
# Look templates (spec §3.2-3.4): keywords, emoji and behind words per 10 s and their minimum gaps (s).
KEYWORD_RATE: dict[str, float] = {"style": 4.0, "subtle": 1.25, "bold": 4.0, "max": 5.5}
KEYWORD_GAP: dict[str, float] = {"style": 1.2, "subtle": 4.0, "bold": 1.2, "max": 0.8}
EMOJI_RATE: dict[str, float] = {"style": 1.0, "subtle": 0.5, "bold": 1.0, "max": 2.5}
EMOJI_GAP: dict[str, float] = {"style": 3.0, "subtle": 6.0, "bold": 3.0, "max": 1.5}
EMOJI_LEAD = 2 / 30        # an emoji appears 2 frames before its keyword is spoken
EMOJI_HOLD = (0.5, 1.8)    # ... and holds until its caption group changes, within these bounds
BEHIND_EVERY: dict[str, float] = {"style": 20.0, "subtle": 30.0, "bold": 20.0, "max": 12.0}
BEHIND_HOLD = 1.5          # spec 1.2-2.0 s; ends on the next reframe
# Share of reframes (and real cuts) that carry a hit: spec §3.7 says bold 30 %, max 60 %; bold is raised to 50 % so
# a bold clip lands near Hormozi's measured 2-3 sounds per 10 s with its emoji pops (82 % of aC52's cuts carry one).
EVENT_SFX_SHARE: dict[str, float] = {"style": 0.0, "subtle": 0.0, "bold": 0.5, "max": 0.6}
EVENT_SFX_LEAD = 1 / 30    # a cut's hit starts one frame before the picture changes

# SFX bus gain (§8.3), calibrated on renders measured with the study's scripts (SFX peak against the separated voice
# peak): hits land about 16 / 12 / 6 dB under the voice peak at subtle / bold / max (spec §3.7); pops sit lower.
SFX_BUS_GAIN_DB: dict[str, float] = {"style": -16.0, "subtle": -20.0, "bold": -16.0, "max": -10.0}
# Music under speech, relative to the voice: subtle -11 dB constant (podcast clippers), bold -19 dB constant
# (Hormozi), max -6 dB ducking 7 dB in pauses (MrBeast). Calibrated on renders measured with the study's scripts
# (separated music stem against the voice under speech). (gain_db, duck_db, attack, release).
MUSIC_LEVELS: dict[str, tuple[float, float, float, float]] = {
    "subtle": (-8.0, 0.0, 0.08, 0.35), "bold": (-15.0, 0.0, 0.08, 0.35), "max": (3.0, -7.0, 0.25, 0.6),
}
# Moods whose bed drives the edit sit higher in the mix (all beds are -20 LUFS): a drill beat under a rant is felt,
# not a whisper. Aggressive at Bold lands about 15 dB under the voice instead of about 19 dB.
MOOD_MUSIC_TRIM_DB: dict[str, float] = {"aggressive": 4.0, "hype": 2.0}
GRAIN: dict[str, float] = {"subtle": 0.15, "bold": 0.20, "max": 0.30}

# Sounds removed by serious mode (rule 1), template-intrinsic ones included.
SERIOUS_DROP_SOUNDS = frozenset({"pop", "boom", "record_scratch", "cash_register", "slap", "tape_stop"})
# Relative per-sound trims (dB). The intensity bus gain (§8.3) is applied by the mixer, never baked in here.
SFX_TRIM_DB: dict[str, float] = {"ticker": -6.0, "riser": -4.0, "whoosh": -2.0, "glitch": -3.0, "tape_stop": -2.0}
CURRENCY_PREFIXES = frozenset({"$", "€", "£", "¥", "₹", "₩", "₽", "R$"})
HIDES_CAPTIONS = frozenset({"slam", "kinetic", "behind"})   # the behind word IS the caption while it shows
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
# Filler and hedge words that never make a keyword, however long ("basically", "something"), in addition to STOPWORDS.
FILLERS = frozenset("""
actually basically literally honestly obviously probably definitely seriously totally certainly simply exactly
anyway anyways something somebody someone somewhere anything anybody anyone everything everybody everyone nothing
thing things stuff kind sort lot lots bit way ways guy guys know knew think thought mean means meant say says said
tell told get gets got getting go goes going gone went come comes came make makes made take takes took want wanted
need needed see saw look looked feel felt let lets let's also even still just only much many more most some any
here now then well right yes no not about because maybe sure ever never always again already other another every
being doing having one two own same such there's here's what's who's i'd you'd we'd they'd i'll you'll we'll
they'll isn't aren't wasn't weren't don't doesn't didn't can't couldn't won't wouldn't shouldn't haven't hasn't
like okay yeah gonna wanna gotta kinda sorta whatever whenever wherever however
though although whether while since until unless upon within without across through throughout over under after
before against between among around above below into onto toward towards behind beyond during except inside
outside rather quite pretty enough else which whose whom where why how yourself myself himself herself itself
ourselves themselves people person
""".split())

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
    """Transitions per clip: none at subtle; bold one per 40 s, max one per 20 s (at least one on a clip with cuts)."""
    every = TRANSITION_EVERY.get(tier)
    if not every:
        return 0
    return max(1, math.floor(duration / every + 0.5))


def per10_cap(rate: float, duration: float, *, at_least_one: bool = True) -> int:
    """A per-10-s rate as a per-clip cap: round half up, at least one on clips of 6 s or more when the rate is > 0."""
    if rate <= 0:
        return 0
    raw = math.floor(rate * duration / 10.0 + 0.5)
    return max(1, raw) if at_least_one and duration >= 6.0 else raw


def tier_for(style: EditStyle, sensitivity: str) -> str:
    """Budget column: style-only when intensity is off; serious mode forces subtle (rule 1)."""
    if not style.active:
        return "style"
    if sensitivity == "serious":
        return "subtle"
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
        # Look templates (fx-v2): caption phrase groups as fx-core draws them, keywords, reframe events.
        self.look = caption_look_for(template, style.intensity, inp.beats.sensitivity)
        self.look_template = template in LOOK_TEMPLATES
        self.lead = LOOK_LEAD.get(self.look, 0.0)
        self.gwords: list[dict] = grouped_words(self.words) if self.timing != "none" else []
        self.groups: list[PhraseGroup] = (
            phrase_groups(self.gwords, LOOK_MAX_WORDS.get(self.look, 3)) if self.look_template else []
        )
        self.group_of: dict[int, int] = {i: k for k, g in enumerate(self.groups) for i in range(g.first, g.last + 1)}
        self.keyword_words: dict[int, str] = {}           # gwords index -> producing beat id ("" = heuristic)
        self.events: list[float] = []                     # reframe event times (E), sorted after place_reframes
        self.event_beat: dict[float, str] = {}
        self.forced_events: list[tuple[float, str]] = []  # (t, beat): reframes a behind word lands on

    # ------------------------------------------------------------------ helpers

    def ge(self, tier: str) -> bool:
        return _RANK[self.tier] >= _RANK[tier]

    def eligible(self, b: Beat) -> bool:
        """§8.3 strength gate for non-accent extras. Hook, number and swear are eligible at any strength."""
        if self.tier == "style":
            return False
        if b.kind in ("hook", "number", "swear"):
            return True
        need = {"subtle": 3, "bold": 2, "max": 1}[self.tier]
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
    def is_content(text: str) -> bool:
        """A word that can carry a keyword: letters or digits, not a stopword, filler or hedge."""
        norm = _norm(text)
        if any(ch.isdigit() for ch in text):
            return True
        return _letters(text) > 0 and norm not in STOPWORDS and norm not in FILLERS

    @classmethod
    def key_word(cls, words: Sequence[dict]) -> dict:
        """The word an accent or punch lands on: a number if present, else a long distinctive content word (≥ 7
        letters), else the LAST content word (English phrase stress falls on it: "...while you SLEEP"). Fillers
        ("basically", "something") never win over a content word."""
        def score(item: tuple[int, dict]) -> tuple:
            i, w = item
            text = str(w.get("text", ""))
            digit = any(ch.isdigit() for ch in text)
            content = cls.is_content(text)
            return (digit, content, content and _letters(text) >= 7, i)
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
        """Template accent(s) for one beat (§8.7). Legacy templates get none; look templates (highlighter, emoji drop,
        behind the head) get theirs from place_keywords / place_emoji / place_behind."""
        if not self.fx_template or self.look_template:
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
            burst = (self.tier == "bold" and b.strength >= 3) or (self.tier == "max" and b.strength >= 2)
            placed = None
            if burst and self.family_ok("burst", key["start"], b.id):
                placed = put("emoji_burst", key["start"], key["start"] + 1.0, purpose="burst", text=text,
                             emoji=emoji, value=5.0 if self.tier == "bold" else 8.0)
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
                self.sfx(b.id, f"{base}.sfx", "boom" if self.tier == "max" and not self.serious else "impact",
                         c.start, intrinsic=True, hook_bound=hb)
                if self.ge("bold"):
                    self.shake(b, "slam.shake", c.start, 0.25, 14.0, paired=True, hook_bound=hb)
                    self.flash_or_glitch("glitch", b.id, "slam.glitch", c.start + 0.1, 0.12, intrinsic=False,
                                         hook_bound=hb, amount=0.5)
            elif v == "counter":
                lock = c.start + max(0.6, (b.end - b.start) + 0.4)
                self.sfx(b.id, f"{base}.sfx", "ticker", c.start, intrinsic=True, hook_bound=hb, counted=False)
                lock_sound = "cash_register" if self.ge("bold") and b.prefix in CURRENCY_PREFIXES else "stamp"
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
                if self.ge("bold"):
                    self.flash_or_glitch("glitch", b.id, f"{c.id[len(b.id) + 1:]}.glitch", c.start, 0.08,
                                         intrinsic=False, hook_bound=hb, amount=0.4)
            elif v in ("behind", "kinetic"):
                self.sfx(b.id, f"{base}.sfx", "whoosh", c.start, intrinsic=True, hook_bound=hb)
                if v == "kinetic":
                    self.desaturate(b.id, "desat", c.start, c.end, 0.7, 0.2, hook_bound=hb)
            elif v == "doodle":
                if b.strength >= 2:
                    self.sfx(b.id, f"{base}.sfx", "pop", c.start, intrinsic=True, hook_bound=hb)

    # ------------------------------------------------------------------ per-beat accents (hidden templates)

    def bundle(self, b: Beat) -> None:
        """The template's own accent for a beat (hidden templates only) and its intrinsic visuals and sounds, plus
        "wait for it" before a payoff. Zooms, sounds on cuts and transitions are global steps (fx-v2)."""
        words = self.beat_words(b)
        key = self.key_word(words)
        accents = self.accent_for(b, words, key)
        self.accent_intrinsics(b, accents)
        if b.kind == "payoff" and self.ge("bold") and self.eligible(b):
            self.wait_for_it(b)

    # ------------------------------------------------------------------ look templates (spec §3.1-3.4)

    def beat_key_index(self, b: Beat) -> Optional[int]:
        """gwords index of a beat's key word (None when it has no timed word)."""
        words = self.beat_words(b)
        key = self.key_word(words)
        for i, w in enumerate(self.gwords):
            if abs(w["start"] - key["start"]) < 1e-6 and w.get("text") == key.get("text"):
                return i
        return None

    def group_end(self, index: int) -> float:
        """Display end (E, lead included) of the caption group holding gwords[index]."""
        k = self.group_of.get(index)
        if k is None:
            w = self.gwords[index]
            return max(w["end"], w["start"] + 0.4) + self.lead
        return self.groups[k].end + self.lead

    def keyword_candidates(self) -> list[tuple[int, str]]:
        """(gwords index, beat id) in priority order: beat key words (strongest, highest priority first), then the
        strongest content word of each remaining group (numbers, then words of 5+ letters)."""
        out: list[tuple[int, str]] = []
        seen: set[int] = set()
        for b in sorted(self.beats, key=lambda o: (-o.strength, -PRIORITY[o.kind], o.start, o.id)):
            if b.kind not in ACCENT_KINDS:
                continue
            i = self.beat_key_index(b)
            if i is None or i in seen or not self.is_content(str(self.gwords[i].get("text", ""))):
                continue
            seen.add(i)
            out.append((i, b.id))
        rest: list[tuple[tuple, int]] = []
        for g in self.groups:
            best: Optional[tuple[tuple, int]] = None
            for i in range(g.first, g.last + 1):
                text = str(self.gwords[i].get("text", ""))
                if i in seen or not self.is_content(text):
                    continue
                digit = any(ch.isdigit() for ch in text)
                if not digit and _letters(text) < 5:
                    continue
                rank = (digit, _letters(text), i)
                if best is None or rank > best[0]:
                    best = (rank, i)
            if best is not None:
                rest.append(((not best[0][0], -best[0][1], best[1]), best[1]))
        out += [(i, "") for _rank, i in sorted(rest)]
        return out

    def place_keywords(self) -> None:
        """At most one keyword per caption group (`highlight` accent from the word to the group's end), at the look's
        rate and spacing. The caption drawer boxes or colours it on the frame the word appears (0 frames)."""
        if not self.look_template or self.timing == "none" or not self.groups:
            return
        tier = self.tier
        cap = per10_cap(KEYWORD_RATE[tier], self.dur)
        gap = KEYWORD_GAP[tier]
        used_groups: set[int] = set()
        starts: list[float] = []
        for i, beat_id in self.keyword_candidates():
            if len(starts) >= cap:
                break
            k = self.group_of.get(i)
            if k is None or k in used_groups:
                continue
            w = self.gwords[i]
            t = w["start"]
            if any(abs(t - o) < gap - EPS for o in starts):
                continue
            end = max(self.group_end(i), t + 0.25)
            cue_id = f"w{i}.kw"
            if cue_id in self.user_ids or self.user_collides("accent", t, end):
                continue
            span = self.quiet(t, end, instant=False, hook_bound=True, min_len=0.1)
            if span is None or any(_overlaps(span[0], span[1], a, z) for a, z in self.hides):
                continue
            cand = _Cand("accent", cue_id, span[0], span[1], beat_id,
                         {"variant": "highlight", "text": self.censored(w).strip()[:120], "emoji": "",
                          "doodle": "sparkles", "value": None, "prefix": "", "suffix": ""}, "accent", True)
            if not self.add(cand):
                continue
            used_groups.add(k)
            starts.append(t)
            self.keyword_words[i] = beat_id
            self.accent_spans.append((span[0], span[1], "highlight"))

    def place_behind(self) -> None:
        """behind_head: one giant word behind the speaker on the strongest beats (spec §3.4), hard cut in on a
        reframe at the word's onset + 1 frame, held BEHIND_HOLD and out on the next reframe."""
        if self.template != "behind_head" or self.timing == "none":
            return
        cap = max(1, math.floor(self.dur / BEHIND_EVERY[self.tier] + 0.5)) if self.dur >= 6.0 else 0
        pool = [b for b in self.beats if b.kind in ACCENT_KINDS and (b.kind == "hook" or b.strength >= 2)]
        pool.sort(key=lambda o: (-o.strength, -PRIORITY[o.kind], o.start, o.id))
        placed: list[float] = []
        for b in pool:
            if len(placed) >= cap:
                break
            i = self.beat_key_index(b)
            if i is None:
                continue
            w = self.gwords[i]
            if not self.is_content(str(w.get("text", ""))):
                continue
            t = _frame(w["start"] + 1 / FPS)
            if any(abs(t - o) < BEHIND_EVERY[self.tier] * 0.5 for o in placed):
                continue
            end = min(self.dur - QUIET_TAIL, t + BEHIND_HOLD)
            if end - t < 1.0 or t < QUIET_HEAD + 0.25 - EPS:
                continue
            # Its two reframes keep clear of transitions and real cuts, and it never holds across a cut.
            near = [*self.transitions, *(float(c) for c in self.inp.cuts)]
            if any(abs(t - c) < ZOOM_TRANSITION_GAP or abs(end - c) < ZOOM_TRANSITION_GAP or t < c < end
                   for c in near):
                continue
            c = self.accent(b, "behind", "behind", t, end, text=self.text_of([w]), hook_bound=True)
            if c is None:
                continue
            placed.append(t)
            self.forced_events.append((c.start, b.id))
            self.forced_events.append((c.end, b.id))
            self.sfx(b.id, f"{c.id}.sfx", "boom", c.start, intrinsic=True, hook_bound=True)

    def place_emoji(self) -> None:
        """emoji_drop: an emoji under a keyword (spec §3.3), 2 frames before it is spoken, held until its caption
        group changes. Only beats whose emoji depicts a thing the words name (the model's pick, else the keyword
        map); never a tone face. One soft pop for every third emoji."""
        if self.template != "emoji_drop" or self.serious or self.timing == "none":
            return
        cap = per10_cap(EMOJI_RATE[self.tier], self.dur)
        gap = EMOJI_GAP[self.tier]
        pool = []
        for b in self.beats:
            if b.kind not in ACCENT_KINDS:
                continue
            if b.tone == "sad":
                continue          # no emoji on a vulnerable moment
            if b.emoji:
                pool.append((0, -b.strength, b.start, b))
            # No tone-emoji fallback: faces on abstract words read cheap; the references only show objects.
        pool.sort(key=lambda x: (x[0], x[1], x[2], x[3].id))
        placed: list[float] = []
        count = 0
        for _rank, _s, _t, b in pool:
            if len(placed) >= cap:
                break
            i = self.beat_key_index(b)
            if i is None:
                continue
            w = self.gwords[i]
            start = max(0.0, w["start"] - EMOJI_LEAD)
            if any(abs(start - o) < gap - EPS for o in placed):
                continue
            lo, hi = EMOJI_HOLD
            end = min(max(self.group_end(i), start + lo), start + hi)
            c = self.place_accent(f"{b.id}.accent", b.id, "emoji", start, end, text=self.text_of([w]), emoji=b.emoji,
                                  hook_bound=True)
            if c is None:
                continue
            placed.append(start)
            if count % 3 == 0:
                self.sfx(b.id, f"{c.id}.sfx", "pop", c.start, intrinsic=True, hook_bound=True)
            count += 1

    # ------------------------------------------------------------------ reframes and nudges (spec §3.5)

    def reframe_candidates(self) -> list[tuple[float, str]]:
        """(time, beat id) in priority order: beat key words (eligible by strength), then sentence starts."""
        out: list[tuple[float, str]] = []
        for b in sorted(self.beats, key=lambda o: (-PRIORITY[o.kind], -o.strength, o.start, o.id)):
            if b.kind not in ACCENT_KINDS or not self.eligible(b):
                continue
            key = self.key_word(self.beat_words(b))
            out.append((_frame(max(0.0, key["start"] - 1 / FPS)), b.id))
        for i, w in enumerate(self.words):
            if i == 0:
                continue
            prev = self.words[i - 1]
            if _ends_sentence(prev["text"]) or w["start"] - prev["end"] >= 0.5:
                out.append((_frame(max(0.0, w["start"] - 1 / FPS)), ""))
        return out

    def reframe_ok(self, t: float, spacing: float, taken: Sequence[float]) -> bool:
        if t < QUIET_HEAD + 0.25 - EPS or t > self.dur - 0.6 + EPS:
            return False
        if any(abs(t - o) < spacing - EPS for o in taken):
            return False
        if any(abs(t - float(c)) < 0.4 for c in self.inp.cuts):
            return False
        return not self.in_excl(t, t) and all(abs(t - c) >= ZOOM_TRANSITION_GAP - EPS for c in self.transitions)

    def place_reframes(self) -> None:
        """Hard reframes that hold (spec §3.5): events alternate in (scale up about the face) and out (back to the
        base framing); a real cut also resets the framing. Forced events (behind words) always count."""
        if self.tier == "style" or not self.style.zooms or self.timing == "none":
            for t, beat in self.forced_events:
                self.events.append(t)
                self.event_beat[t] = beat
            self.events.sort()
            return
        tier = self.tier
        cap = per10_cap(REFRAME_RATE[tier], self.dur)
        spacing = REFRAME_GAP[tier]
        chosen: list[float] = []
        beats: dict[float, str] = {}
        for t, beat in self.forced_events:
            t = _frame(t)
            if t not in beats:
                chosen.append(t)
                beats[t] = beat
        for t, beat in self.reframe_candidates():
            if len(chosen) >= max(cap, len(self.forced_events)):
                break
            if not self.reframe_ok(t, spacing, chosen):
                continue
            chosen.append(t)
            beats[t] = beat
        chosen.sort()
        self.events = chosen
        self.event_beat = beats
        cuts = sorted(float(c) for c in self.inp.cuts)
        lo, hi = REFRAME_SCALE[tier]
        n = 0
        k = 0
        while k < len(chosen):
            t = chosen[k]
            nxt = chosen[k + 1] if k + 1 < len(chosen) else self.dur
            cut = next((c for c in cuts if t + EPS < c < nxt - EPS), None)
            end = cut if cut is not None else nxt
            for a, z in self.excl + self.reaction_windows:
                if _overlaps(t, end, a, z):
                    end = min(end, a)
            beat_id = beats.get(t, "")
            cue_id = f"z{round(t * FPS)}.zoom"      # frame-numbered: stable while its moment stays
            n += 1
            if end - t >= 0.4 and cue_id not in self.user_ids and not self.user_collides("zoom", t, end):
                scale = lo + (hi - lo) * hash01(self.seed_of("", cue_id), 0)
                if self.add(_Cand("zoom", cue_id, t, end, beat_id,
                                  {"mode": "punch", "scale": _r(scale), "anchor": "face"}, "punch")):
                    self.zooms.append((t, end))
            # The "out" event is the next one; a cut inside the hold already reset the framing, so the next event
            # after it is an "in" again.
            k += 1 if cut is not None else 2

    def place_nudges(self) -> None:
        """Short eased zooms (spec §3.5): +6-10 % over 11 frames on emphasis words. Between two reframes they
        alternate: a nudge in holds until the next nudge, which eases back out over 11 frames (a `pull`); the last
        one holds until the reframe or cut that ends the stretch. They never accumulate."""
        if self.tier == "style" or not self.style.zooms or self.timing == "none":
            return
        tier = self.tier
        cap = per10_cap(NUDGE_RATE[tier], self.dur)
        lo, hi = NUDGE_SCALE[tier]
        bounds = sorted({*self.events, *(float(c) for c in self.inp.cuts), self.dur})
        candidates: list[tuple[float, str]] = []
        for b in sorted(self.beats, key=lambda o: (-o.strength, -PRIORITY[o.kind], o.start, o.id)):
            if b.kind in ACCENT_KINDS:
                key = self.key_word(self.beat_words(b))
                candidates.append((_frame(max(0.0, key["start"] - 1 / FPS)), b.id))
        for i in sorted(self.keyword_words):
            candidates.append((_frame(max(0.0, self.gwords[i]["start"] - 1 / FPS)), self.keyword_words[i]))
        # Then long content words (what an editor leans on when the model marked nothing there).
        for w in self.words:
            text = str(w.get("text", ""))
            if self.is_content(text) and (_letters(text) >= 6 or any(ch.isdigit() for ch in text)):
                candidates.append((_frame(max(0.0, w["start"] - 1 / FPS)), ""))
        chosen: dict[float, str] = {}
        for t, beat_id in candidates:
            if len(chosen) >= cap:
                break
            if t < QUIET_HEAD + 0.25 - EPS or t > self.dur - 1.0 or t in chosen:
                continue
            if any(abs(t - e) < NUDGE_GAP - EPS for e in [*self.events, *chosen]):
                continue
            end = next((x for x in bounds if x > t + EPS), self.dur)
            if end - t < NUDGE_MIN_HOLD - EPS or self.in_excl(t, end):
                continue
            if not all(_interval_gap(t, end, c, c) >= ZOOM_TRANSITION_GAP - EPS for c in self.transitions):
                continue
            chosen[t] = beat_id
        times = sorted(chosen)
        n = 0
        k = 0
        while k < len(times):
            t = times[k]
            end = next((x for x in bounds if x > t + EPS), self.dur)
            nxt = times[k + 1] if k + 1 < len(times) and times[k + 1] < end - EPS else None
            cue_id = f"n{round(t * FPS)}.zoom"
            n += 1
            scale = _r(lo + (hi - lo) * hash01(self.seed_of("", cue_id), 0))
            hold_end = nxt if nxt is not None else end
            if cue_id in self.user_ids or self.user_collides("zoom", t, hold_end):
                k += 1
                continue
            if self.add(_Cand("zoom", cue_id, t, hold_end, chosen[t], {"mode": "nudge", "scale": scale,
                                                                        "anchor": "face"}, "push")):
                self.zooms.append((t, hold_end))
            if nxt is None:
                k += 1
                continue
            # The next nudge eases back out to the base framing over 11 frames.
            out_end = min(end, nxt + NUDGE_FRAMES / FPS)
            out_id = f"n{round(nxt * FPS)}.zoom"
            n += 1
            if out_end - nxt > 2 / FPS and out_id not in self.user_ids and self.add(
                    _Cand("zoom", out_id, nxt, out_end, chosen[nxt], {"mode": "pull", "scale": scale,
                                                                      "anchor": "face"}, "push")):
                self.zooms.append((nxt, out_end))
            k += 2

    # ------------------------------------------------------------------ sounds on visual events (spec §3.7)

    def place_event_sfx(self) -> None:
        """A short low hit one frame before a share of the reframes and real cuts (bold 30 %, max 60 %), strongest
        beats first. Graphic pop-ins and transitions carry their own sounds; plain talking gets none."""
        share = EVENT_SFX_SHARE[self.tier]
        if share <= 0 or not self.style.sfx:
            return
        events = [(t, self.event_beat.get(t, "")) for t in self.events]
        events += [(float(c), "") for c in self.inp.cuts
                   if not any(abs(float(c) - t) < 0.2 for t in [*self.events, *self.transitions])]
        if not events:
            return
        strength = {b.id: (b.strength, PRIORITY[b.kind]) for b in self.beats}
        ranked = sorted(events, key=lambda e: (tuple(-x for x in strength.get(e[1], (0, -1))), e[0]))
        want = max(1, math.floor(share * len(events) + 0.5))
        for t, beat_id in ranked[:want]:
            at = _frame(max(0.0, t - EVENT_SFX_LEAD))
            cue_id = f"e{round(t * FPS)}.sfx"
            pick = hash01(self.seed_of("", cue_id), 0)
            sound = "boom" if pick < (0.5 if self.tier == "max" else 0.35) else "impact"
            self.sfx("", cue_id, sound, at, intrinsic=False, hook_bound=True)

    def wait_for_it(self, b: Beat) -> bool:
        if not self.style.wait_for_it:
            return False
        hook_end = self.beats_meta.hook_end or 0.0
        lead = 6.0 if self.tier == "bold" else 4.0
        if b.start - hook_end < lead - EPS:
            return False
        if self.tier == "bold" and self.dur < 20.0:
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
        self.sfx(b.id, "g.wait.sfx", "pop", span[0], intrinsic=True)
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
        if not (self.style.reactions and self.inp.reactions_available) or self.serious or not self.ge("bold"):
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
        """Rule 6 (spec §3.6): hard cuts by default. Bold: at most one per 40 s, on passage breaks only, a 3-frame
        flash; max: at most one per 20 s, a flash or a 6-frame light leak. Never inside a word or B-roll. A flash
        carries a short whoosh-hit starting 2 frames before the cut."""
        if self.tier in ("style", "subtle") or not self.style.transitions:
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
            if self.tier == "bold" and not is_break(c):
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
            if self.serious or self.beats_meta.mood in CALM_MOODS:
                pool = ["light_leak"] if self.tier == "max" else pool
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
            if style == "flash":
                self.sfx("", f"{cue_id}.sfx", "whoosh", _frame(max(0.0, c - 2 / FPS)), intrinsic=True)

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
        gain, duck, attack, release = MUSIC_LEVELS[self.tier]
        gain += MOOD_MUSIC_TRIM_DB.get(mood, 0.0)
        if self.timing == "none":
            gain, duck = gain + duck, 0.0       # no speech spans to duck under: hold the under-speech level
        offset, fade_in, fade_out = 0.0, 0.5, 0.8
        if self.loop:
            length = float(self.inp.bed_lengths.get(bed, 0.0) or 0.0)
            # F length of the emitted cues through the real warp, so the bed seam lands on the clip seam exactly.
            out = build_warp(cues, self.dur).out_duration
            if length > 0:
                offset = (length - out % length) % length
            fade_in = fade_out = 0.02
        return MusicBed(mood=mood, bed=bed, gain_db=gain, duck_db=duck, attack=attack, release=release,
                        offset=_r(offset), fade_in=fade_in, fade_out=fade_out)

    def grade(self) -> str:
        if not self.style.active:
            return "none"
        if self.style.grade != "auto":
            return self.style.grade
        # Spec §3.10: subtle and bold keep the picture neutral; max gets the punchy saturation.
        return "punchy" if self.tier == "max" else "none"

    # ------------------------------------------------------------------ run

    def run(self) -> FxPlan:
        self.place_user_cues()
        self.slam_ids = self.pick_slams()
        self.place_bleeps()
        self.place_reactions()
        self.place_transitions()
        self.place_behind()                    # behind words first: they hide the captions and fix two reframes
        self.place_keywords()
        self.place_emoji()
        self.place_reframes()
        self.place_nudges()
        self.place_kinetic_without_hook()      # stands in for the hook treatment, so it goes before the bundles
        order = sorted(self.beats, key=lambda o: (-PRIORITY[o.kind], -o.strength, o.start, o.id))
        for b in order:
            self.bundle(b)
        self.place_event_sfx()

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
