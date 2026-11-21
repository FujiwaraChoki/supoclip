"""Caption phrase groups and the intensity caption looks' timing (mirror of fx-core ``captions/groups.ts``).

The look captions (``FxCaptionStyle.look`` subtle/bold/max) show words in phrase groups of 1..``max_words`` words, the
way short-form editors cut captions: a group breaks at sentence or clause punctuation, at a pause of at least
``PAUSE_BREAK`` seconds, before a conjunction once it holds two words, at a run change (studio segments) and when it is
full. fx-core lays the groups out and draws them; the resolver uses the same groups to place at most one keyword per
group and to end an emoji with its group. Pinned by ``shared/fx/fixtures/groups``.

Times are E seconds and do not include the look's lead (fx-core adds it when drawing).

Camera cuts (``cuts``: where an interview clip's caption row changes, ``caption_rows.row_cuts``) end a group: a word
starting before ``cut - CUT_LEAD`` stays in the group on the old row, the rest start a new group (grouped as usual
from there). A group whose first word starts within ``CUT_LEAD`` before a cut belongs to the shot after it and shows
from the cut (``cut``, also when it starts exactly at it: the look's lead never shows it early); a group still on screen at a cut leaves at the cut (``until``). Without cuts nothing changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

PAUSE_BREAK = 0.25          # a gap of at least this many seconds starts a new group
BRIDGE = 0.30               # a group stays up until the next one when the gap is shorter (== scene.CAPTION_BRIDGE)
MIN_HOLD = 0.40             # a group is on screen at least this long (never past the next group's start)
CONJUNCTIONS = frozenset({"and", "but", "so", "because", "or", "then", "cause", "cuz"})
LOOK_MAX_WORDS: dict[str, int] = {"subtle": 4, "bold": 3, "max": 3}
# Display time minus the transcript's word start (s). The references show a word about one frame after its energy
# onset; measured through the study's own scripts, a lead of -1 frame lands our renders there (transcript word starts
# sit 1-3 frames after the energy onset), and stays inside the allowed -1..+2 frames with exact timestamps.
LOOK_LEAD: dict[str, float] = {"subtle": -2 / 30, "bold": -1 / 30, "max": -1 / 30}

# A word starting this close before a camera cut goes with the next shot's group (no word flashes up for a few
# frames on the old shot).
CUT_LEAD = 0.15

_BREAK_AFTER = re.compile(r"[.,!?;:…。！？，、][\"'”’)\]]*$")


def _norm(text: str) -> str:
    return re.sub(r"[^\w']+", "", (text or "").lower(), flags=re.UNICODE).replace("'", "")


def breaks_after(text: str) -> bool:
    """True when a word ends a phrase: sentence or clause punctuation, closing quotes allowed after it."""
    return bool(_BREAK_AFTER.search((text or "").strip()))


@dataclass(frozen=True)
class PhraseGroup:
    first: int              # index into the sorted, non-blank word list
    last: int
    start: float            # first word start (E)
    end: float              # display end (E): bridged to the next group, at least MIN_HOLD
    cut: float | None = None    # the camera cut this group opens at (it shows from the cut), else None
    until: float | None = None  # the camera cut this group leaves at (display ends exactly there), else None


def _sorted_words(words: Sequence[dict]) -> list[dict]:
    """Words with text, by start (stable, like the TS ``[...].sort``)."""
    shown = [w for w in words if str(w.get("text") or "").strip()]
    return sorted(shown, key=lambda w: float(w["start"]))


def phrase_groups(words: Sequence[dict], max_words: int, cuts: Sequence[float] = ()) -> list[PhraseGroup]:
    """Phrase groups over ``words`` ({start, end, text[, run]}), in time order. ``max_words`` is clamped to 1..8.
    ``cuts`` (sorted E seconds) end groups, see the module docstring."""
    limit = max(1, min(8, int(max_words)))
    ordered = _sorted_words(words)
    cuts = sorted(float(c) for c in cuts)

    def cut_between(a: float, b: float) -> bool:
        return any(a < c - CUT_LEAD <= b for c in cuts)

    spans: list[tuple[int, int]] = []
    first = 0
    for i in range(1, len(ordered)):
        prev, word = ordered[i - 1], ordered[i]
        size = i - first
        gap = float(word["start"]) - float(prev["end"])
        if (size >= limit or breaks_after(str(prev.get("text") or "")) or gap >= PAUSE_BREAK - 1e-9
                or (size >= 2 and _norm(str(word.get("text") or "")) in CONJUNCTIONS)
                or str(word.get("run") or "clip") != str(prev.get("run") or "clip")
                or (cuts and cut_between(float(prev["start"]), float(word["start"])))):
            spans.append((first, i - 1))
            first = i
    if ordered:
        spans.append((first, len(ordered) - 1))
    groups: list[PhraseGroup] = []
    for k, (a, b) in enumerate(spans):
        start = float(ordered[a]["start"])
        end = max(start, float(ordered[b]["end"]))
        if k + 1 < len(spans):
            nxt = float(ordered[spans[k + 1][0]]["start"])
            end = nxt if nxt - end < BRIDGE else end
            end = min(max(end, start + MIN_HOLD), max(nxt, start))
        else:
            end = max(end, start + MIN_HOLD)
        groups.append(PhraseGroup(first=a, last=b, start=start, end=end))
    if not cuts:
        return groups
    opened = [next((c for c in cuts if c - CUT_LEAD <= g.start <= c), None) for g in groups]
    out: list[PhraseGroup] = []
    for k, g in enumerate(groups):
        shown = opened[k] if opened[k] is not None else g.start
        leaves = [c for c in cuts if shown < c < g.end]
        if k + 1 < len(groups) and opened[k + 1] is not None and g.end >= groups[k + 1].start - 1e-9:
            leaves.append(opened[k + 1])        # bridged into a group that opens at a cut: stay up until the cut
        out.append(PhraseGroup(first=g.first, last=g.last, start=g.start, end=g.end, cut=opened[k],
                               until=min(leaves) if leaves else None))
    return out


def grouped_words(words: Sequence[dict]) -> list[dict]:
    """The word list ``phrase_groups`` indexes into."""
    return _sorted_words(words)
