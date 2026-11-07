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


class UnitSpan(BaseModel):
    first_unit: int = Field(description="-1 for none")
    last_unit: int = Field(description="-1 for none")


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
    self_summaries: list[UnitSpan] = Field(description="Where a speaker restates the video's main points; [] for none")


class OutlineWindow(BaseModel):
    analysis: str = Field(description="Think first, at most 100 words")
    chapters: list[OutlineChapter] = Field(description="Contiguous, in order, covering this stretch")
    main_points: list[OutlinePoint] = Field(description="Best first, at most 6")
    story_arcs: list[OutlineArc] = Field(description="At most 3; may run to the edge of this stretch")
    payoffs: list[OutlinePayoff]
    self_summaries: list[UnitSpan] = Field(description="Where a speaker restates the main points; [] for none")
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
    keep_chapters: list[int] = Field(description="Chapter numbers (C3 is 3) that become the series, in source order")
    spare_chapters: list[int] = Field(description="Other ★4-5 chapters on the same subject, best first, used if room is left")
    list_items: int = Field(description="Items the video announces and walks through, like 'seven habits'; 0 when none")
    premise: str = Field(description="At most 20 words")


class SeriesSkip(BaseModel):
    first_unit: int
    last_unit: int
    reason: str = Field(description="A few words: sponsor read, tangent, repeated point...")


class SeriesPartDraft(BaseModel):
    end_unit: int = Field(description="Last unit of this part; the next part starts right after it")
    title: str = Field(description="A working label of 2-5 words; final titles are written once the cuts are final")
    best_line: int = Field(description="The unit in this part a viewer would quote")


class SeriesDraft(BaseModel):
    story_analysis: str = Field(description="At most 100 words: setup, complications, payoff, where the cliffhangers are")
    premise: str = Field(description="What this series is about, as a viewer would say it, at most 20 words")
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


class JoinCheck(BaseModel):
    part: int = Field(description="1-based part whose ending this checks (every part but the last)")
    leaves_open: Literal["question", "promise", "decision", "none"] = Field(
        description="What the part's last lines leave open for the next part"
    )
    next_part_answers: bool = Field(description="True when the next part's first lines pick that up")
    next_opens_cold: bool = Field(description="True when the next part makes sense from its first sentence")
    better_end: int = Field(description="A unit within 8 of the end that would leave a real loop open, -1 if none")


class SeriesCritique(BaseModel):
    viewer_notes: str = Field(description="At most 120 words, written as a first-time viewer")
    verdict: Literal["ship", "fix"]
    joins: list[JoinCheck] = Field(description="One entry per join, in order")
    finale_lands: bool = Field(description="True when the last part feels like a finish, not a stop")
    unkept_promises: list[int] = Field(description="Units that announce something the series never delivers")
    fixes: list[SeriesFix] = Field(description="At most 6")


class SeriesListSkips(BaseModel):
    analysis: str = Field(description="Think first, at most 60 words")
    skips: list[SeriesSkip] = Field(description="Best first")


class SeriesOpen(BaseModel):
    start_unit: int = Field(description="The candidate whose first sentence stops a stranger")
    stop_scroll_score: int = Field(description="1-5: how hard that sentence stops a stranger")


class SeriesColdOpen(BaseModel):
    reasoning: str = Field(description="At most 40 words")
    candidate: int = Field(description="The number of the best line, -1 when none is strong enough")


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
    fit: Literal["exact", "narrower", "broader", "different"] = Field(
        description="How the reading this video serves relates to what was typed"
    )
    canonical: str = Field(description="The topic as a short clean name, 1-4 words, in the video's language")
    counts: list[str] = Field(description="4-8 concrete sub-themes or examples that count")
    does_not_count: list[str] = Field(description="3-6 near-misses that do not count")
    keywords: list[str] = Field(description="5-15 words or phrases speakers might say, incl. inflections and synonyms")
    chapters: list[ChapterRelevance]
    suggested_topics: list[str] = Field(description="3-6 subjects this video IS about")


class TopicMoment(BaseModel):
    first_unit: int
    last_unit: int
    skips: list[UnitSpan] = Field(description="Whole sentences inside the moment a viewer won't miss; [] for none")
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


class TopicFinishClip(BaseModel):
    clip_id: int
    first_unit: int
    last_unit: int
    drop: bool = Field(description="True when no edit makes it stand alone")


class TopicFinish(BaseModel):
    notes: str = Field(description="Think first, at most 80 words")
    clips: list[TopicFinishClip] = Field(description="One entry per clip")


class TopicRank(BaseModel):
    candidate_id: int
    on_topic: int = Field(description="0-100: how centrally and substantively it is about the topic")
    standalone: int = Field(description="0-100, as a stranger who saw no other candidate; at most 50 if it needs one")
    score: int = Field(description="0-100: comparative value as a clip about the topic")
    same_point_as: int = Field(description="candidate_id of a better-ranked clip making the same point, else -1")
    title: str = Field(description="3-9 words for the clip exactly as shown; every claim in it is said in the clip")
    reason: str = Field(description="One short sentence")


class TopicRanking(BaseModel):
    analysis: str = Field(description="Think first, at most 80 words: compare the candidates")
    ranking: list[TopicRank] = Field(description="Every candidate exactly once, best first")


# -- recap ---------------------------------------------------------------------


class RecapPoint(BaseModel):
    label: str = Field(description="2-5 words shown on screen as this point plays: says what the point is, not a quote")
    subject: str = Field(description="1-3 words copied exactly from the transcript: what this point is about")
    first_unit: int = Field(description="Fullest clear statement")
    last_unit: int
    core_first: int = Field(description="Shortest self-contained statement inside it, at least 4 s")
    core_last: int
    setup_first: int = Field(description="Shortest earlier sentence naming what the core leans on; -1 for none")
    setup_last: int = Field(description="-1 for none")
    importance: int = Field(description="1 essential, 2 important, 3 nice to have")


class RecapDraft(BaseModel):
    editorial_notes: str = Field(description="Think first, at most 120 words")
    spine: str = Field(description="One sentence: the question this recap answers and the answer it reaches")
    title: str = Field(description="3-9 words")
    opener: UnitSpan = Field(description="Frames the whole video; plays first; at most 10 s")
    opener_answered_by: int = Field(description="1-based point that answers the opener, 0 when none")
    points: list[RecapPoint] = Field(description="In video order")
    closer: UnitSpan = Field(description="Lands the takeaway; -1/-1 when the last point already concludes")


class RecapFinalBeat(BaseModel):
    first_unit: int
    last_unit: int
    label: str = Field(description="2-5 words shown on screen as this beat plays: says what the point is, not a quote")


class RecapFinalCut(BaseModel):
    viewer_notes: str = Field(description="At most 120 words, as someone who never saw the video")
    verdict: Literal["ship", "fix"] = Field(description="ship when the cut you were shown already works")
    beats: list[RecapFinalBeat] = Field(description="The complete final cut in play order")
    title: str = Field(description="3-9 words: the recap's answer as a claim the viewer hears proven")
    why: str = Field(description="What this cut says, in order, naming only what plays, at most 45 words")


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


class TrailerReclaimed(BaseModel):
    first_unit: int
    last_unit: int
    why: str = Field(description="Why this only raises the question")


class TrailerDraft(BaseModel):
    premise: str = Field(description="What the full video promises, one sentence")
    thread: str = Field(description="The one question or tension this trailer sells, at most 15 words")
    payoffs: list[TrailerPayoff] = Field(description="Every reveal to hide, beyond the [OFF LIMITS] units")
    reclaimed: list[TrailerReclaimed] = Field(description="Up to 5 [OFF LIMITS] ranges that only raise the question")
    beats: list[TrailerBeat] = Field(description="Play order, 4-6 beats")
    stranger_read: str = Field(description="At most 60 words: the beats as a stranger hears them; what gets lost?")
    title_candidates: list[str] = Field(description="Written last: exactly 3 titles, 3-8 words, every noun heard in the beats")


class TrailerTitles(BaseModel):
    titles: list[str] = Field(description="Exactly 3 titles, 3-8 words, every noun heard in the beats")


class ButtonPick(BaseModel):
    reasoning: str = Field(description="At most 40 words")
    candidate: int = Field(description="The number of the best line, -1 to keep the current last line")


class SpoilerVerdict(BaseModel):
    reasoning: str = Field(description="Think first, at most 80 words")
    reveals_payoff: bool
    offending_beat: int = Field(description="1-based beat that gives a reveal away, -1 if none")
    chopped_beat: int = Field(description="1-based beat that starts or ends mid-phrase and sounds broken, -1 if none")
    unclear_beat: int = Field(description="1-based beat with a pronoun or term a viewer of only this trailer can't resolve, -1 if none")
    weakest_beat: int = Field(description="1-based beat that adds least to the thread, -1 if each earns its place")
    thread_holds: bool = Field(description="True when every beat makes the one question more urgent")
    button_unresolved: bool = Field(description="True when the last line asks or leaves hanging the question this trailer raised")
    best_title: int = Field(description="1-based: the most intriguing title the beats deliver without the outcome; -1 if none")
    curiosity: int = Field(description="0-100")


# Every model the planners send to a model, for the schema tests.
LLM_SCHEMAS: tuple[type[BaseModel], ...] = (
    SourceOutline,
    OutlineWindow,
    OutlineReduce,
    SeriesScope,
    SeriesDraft,
    SeriesCritique,
    SeriesListSkips,
    SeriesOpen,
    SeriesCopy,
    TopicLocate,
    TopicExtraction,
    TopicFinish,
    TopicRanking,
    RecapDraft,
    RecapFinalCut,
    TrailerDraft,
    TrailerTitles,
    ButtonPick,
    SpoilerVerdict,
)
