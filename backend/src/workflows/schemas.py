"""What the workflow planners ask the model to return.

Rules every schema here follows (the API rejects strict schemas that are too
large or too clever, and local models fill simple ones more reliably):

- Every field is required. No ``Optional``, no defaults. "Nothing" is spelled
  with a sentinel: ``-1`` for no unit, ``""`` for no text, ``[]`` for none.
- Short ``Literal`` enums only. The model never echoes transcript text: text
  is grounded in code from unit ids.
- A free-text thinking field comes first in every plan, so the model reasons
  before it commits to ids.
- Numbers are clamped and validated in code; the schema never trusts them.

``test_workflow_schemas.py`` keeps every model under 6000 characters of JSON
schema and free of defaults.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChapterKind = Literal["content", "story", "intro", "outro", "sponsor", "tangent", "qa", "banter"]
VideoFormat = Literal["interview", "podcast", "monologue", "tutorial", "lecture", "vlog", "news", "story", "other"]


# -- source outline ------------------------------------------------------------


class OutlineChapter(BaseModel):
    end_unit: int = Field(description="Last unit of this chapter; the next chapter starts right after it")
    title: str = Field(description="2-6 words")
    summary: str = Field(description="At most 25 words, concrete: names, numbers, claims")
    kind: ChapterKind
    importance: int = Field(description="1-5: how much a viewer who wants the substance loses by skipping it")


class OutlinePoint(BaseModel):
    statement: str = Field(description="At most 15 words")
    first_unit: int = Field(description="Start of the fullest self-contained statement of the point")
    last_unit: int
    core_first: int = Field(description="Start of the shortest range inside it that still states the point")
    core_last: int
    importance: int = Field(description="1-5")


class OutlineArc(BaseModel):
    first_unit: int
    last_unit: int
    title: str = Field(description="2-6 words")
    strength: int = Field(description="1-5: how gripping the storyline is")
    shape: str = Field(description="'setup: ... -> turn: ... -> payoff: ...' in at most 30 words")


class OutlinePayoff(BaseModel):
    first_unit: int
    last_unit: int
    what: str = Field(description="The answer, result, twist or punchline it reveals, at most 15 words")


class SourceOutline(BaseModel):
    analysis: str = Field(description="Think first, at most 150 words")
    format: VideoFormat
    language: str = Field(description="ISO 639-1 code of what the speakers mainly speak")
    one_line: str = Field(description="What the video is, in one sentence")
    central_question: str = Field(description="The question the whole video answers or explores")
    throughline: str = Field(description="One sentence connecting the strongest material")
    key_topics: list[str] = Field(description="At most 12 specific nouns or phrases")
    chapters: list[OutlineChapter] = Field(description="Contiguous, in order, covering every unit; at most 30")
    main_points: list[OutlinePoint] = Field(description="Best first, at most 15")
    story_arcs: list[OutlineArc] = Field(description="Strongest first, at most 5")
    payoffs: list[OutlinePayoff] = Field(description="At most 10")


class OutlineWindow(BaseModel):
    analysis: str = Field(description="Think first, at most 100 words")
    chapters: list[OutlineChapter] = Field(description="Contiguous, in order, covering this stretch")
    main_points: list[OutlinePoint] = Field(description="Best first, at most 6")
    story_arcs: list[OutlineArc] = Field(description="At most 3; may run to the edge of this stretch")
    payoffs: list[OutlinePayoff]
    open_threads: list[str] = Field(description="Questions raised here and not answered here")


class OutlineReduce(BaseModel):
    analysis: str = Field(description="Think first, at most 150 words")
    format: VideoFormat
    language: str = Field(description="ISO 639-1 code of what the speakers mainly speak")
    one_line: str
    central_question: str
    throughline: str
    key_topics: list[str] = Field(description="At most 12")
    chapter_importance: list[int] = Field(description="One rating (1-5) per listed chapter, in order")
    main_point_order: list[int] = Field(description="Candidate point numbers (k of Pk), best first, at most 15")
    story_arcs: list[OutlineArc] = Field(description="At most 5; ids from the chapter or arc ids listed")
    payoffs: list[OutlinePayoff] = Field(description="At most 10, copied from the candidate payoffs")


# -- series --------------------------------------------------------------------


class SeriesScope(BaseModel):
    analysis: str = Field(description="Think first, at most 100 words")
    story_found: bool
    first_unit: int
    last_unit: int
    premise: str = Field(description="At most 20 words")


class SeriesSkip(BaseModel):
    first_unit: int
    last_unit: int
    reason: str = Field(description="A few words: sponsor read, tangent, repeated point...")


class SeriesPartDraft(BaseModel):
    end_unit: int = Field(description="Last unit of this part; the next part starts right after it")
    title: str = Field(description="2-7 words, no numbering")
    delivers: str = Field(description="At most 20 words: what the viewer gets in this part")
    ends_on: str = Field(description="At most 15 words: the open question it leaves; empty for the last part")
    post_caption: str = Field(description="1-2 sentences for the post description")


class SeriesDraft(BaseModel):
    story_analysis: str = Field(description="At most 150 words: setup, complications, payoff, where the cliffhangers are")
    series_title: str = Field(description="2-6 words")
    start_unit: int
    skips: list[SeriesSkip]
    parts: list[SeriesPartDraft]


class SeriesFix(BaseModel):
    part: int = Field(description="1-based part number")
    action: Literal["end_earlier", "end_later", "drop_units", "restore_units", "retitle", "none"]
    unit: int = Field(description="end_earlier/end_later: the unit to end on; drop/restore: first unit; else -1")
    last_unit: int = Field(description="drop/restore: last unit; else -1")
    new_title: str = Field(description="retitle only, else empty")
    reason: str


class SeriesCritique(BaseModel):
    viewer_notes: str = Field(description="At most 120 words, written as a first-time viewer")
    verdict: Literal["ship", "fix"]
    fixes: list[SeriesFix] = Field(description="At most 6")


class PartCopy(BaseModel):
    part: int
    title: str
    delivers: str
    ends_on: str
    post_caption: str


class SeriesCopy(BaseModel):
    series_title: str
    parts: list[PartCopy]


# -- topic ---------------------------------------------------------------------


class ChapterRelevance(BaseModel):
    chapter: int = Field(description="Chapter number from the outline (C1 is 1)")
    relevance: int = Field(description="0 not about it, 1 passing, 2 substantial, 3 squarely about it")


class TopicLocate(BaseModel):
    interpretation: str = Field(description="What the creator most likely means")
    canonical: str = Field(description="The topic as a short clean name, 1-4 words, in the video's language")
    counts: list[str] = Field(description="4-8 concrete sub-themes or examples that count")
    does_not_count: list[str] = Field(description="3-6 near-misses that do not count")
    keywords: list[str] = Field(description="5-15 words or phrases speakers might say, incl. inflections and synonyms")
    chapters: list[ChapterRelevance]
    suggested_topics: list[str] = Field(description="3-6 subjects this video IS about")


class TopicMoment(BaseModel):
    first_unit: int
    last_unit: int
    evidence_unit: int = Field(description="The unit that proves it is about the topic")
    relevance: int = Field(description="0-100")
    standalone: int = Field(description="0-100")
    hook_title: str = Field(description="3-9 words")
    hook_type: Literal["question", "statement", "statistic", "story", "contrast", "none"]
    hook_score: int = Field(description="0-25")
    engagement_score: int = Field(description="0-25")
    value_score: int = Field(description="0-25")
    shareability_score: int = Field(description="0-25")
    why: str = Field(description="At most 20 words: what it says about the topic")


class TopicExtraction(BaseModel):
    window_notes: str = Field(description="Think first, at most 80 words")
    moments: list[TopicMoment]


# -- recap ---------------------------------------------------------------------


class UnitSpan(BaseModel):
    first_unit: int = Field(description="-1 for none")
    last_unit: int = Field(description="-1 for none")


class RecapPoint(BaseModel):
    label: str = Field(description="2-6 words, shown in the UI")
    first_unit: int = Field(description="Fullest clear statement, 6-25 s")
    last_unit: int
    core_first: int = Field(description="Shortest self-contained statement inside it, at least 4 s")
    core_last: int
    importance: int = Field(description="1 essential, 2 important, 3 nice to have")


class RecapDraft(BaseModel):
    editorial_notes: str = Field(description="Think first, at most 120 words")
    title: str = Field(description="3-9 words")
    opener: UnitSpan = Field(description="Frames the whole video; plays first; at most 10 s")
    points: list[RecapPoint] = Field(description="In video order")
    closer: UnitSpan = Field(description="Lands the takeaway; -1/-1 when the last point already concludes")


class JunctionFix(BaseModel):
    beat: int = Field(description="1-based beat number")
    action: Literal["extend_back", "use_full", "use_core", "drop_beat", "none"]
    reason: str


class RecapCritique(BaseModel):
    viewer_notes: str = Field(description="At most 120 words, as someone who never saw the video")
    verdict: Literal["ship", "fix"]
    fixes: list[JunctionFix] = Field(description="At most 5")


# -- trailer -------------------------------------------------------------------


class TrailerPayoff(BaseModel):
    first_unit: int
    last_unit: int
    what: str = Field(description="What it reveals, at most 15 words")


class TrailerBeat(BaseModel):
    role: Literal["cold_open", "context", "escalation", "button"]
    first_unit: int
    last_unit: int
    why: str = Field(description="At most 15 words")


class TrailerDraft(BaseModel):
    premise: str = Field(description="What the full video promises, one sentence")
    payoffs: list[TrailerPayoff] = Field(description="Every reveal to hide, beyond the [OFF LIMITS] units")
    title: str = Field(description="3-9 words, curiosity without the answer")
    beats: list[TrailerBeat] = Field(description="Play order, 4-9 beats")


class SpoilerVerdict(BaseModel):
    reasoning: str = Field(description="Think first, at most 80 words")
    reveals_payoff: bool
    offending_beat: int = Field(description="1-based beat that gives a reveal away, -1 if none")
    button_unresolved: bool = Field(description="True when the last beat leaves the viewer hanging")
    curiosity: int = Field(description="0-100")


# Every model the planners send to a model, for the schema tests.
LLM_SCHEMAS: tuple[type[BaseModel], ...] = (
    SourceOutline,
    OutlineWindow,
    OutlineReduce,
    SeriesScope,
    SeriesDraft,
    SeriesCritique,
    SeriesCopy,
    TopicLocate,
    TopicExtraction,
    RecapDraft,
    RecapCritique,
    TrailerDraft,
    SpoilerVerdict,
)
