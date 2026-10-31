"""Shared fakes for the workflow planner tests: transcripts, outlines and a scripted model.

Not a test module. The scripted model replaces ``build_agent`` in every
planner module and ``ai._run_transcript_analysis`` (the one runner every
call goes through), answering by output schema from a queue or a function.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Optional

from src.workflows import outline as outline_module
from src.workflows import recap as recap_module
from src.workflows import series as series_module
from src.workflows import topic as topic_module
from src.workflows import trailer as trailer_module
from src.workflows.contracts import PlanContext, WorkflowOptions, WorkflowSpec
from src.workflows.llm import PlanStats
from src.workflows.outline import OutlineResult
from src.workflows.planning import PlanInput
from src.workflows.schemas import (
    OutlineArc,
    OutlineChapter,
    OutlinePayoff,
    OutlinePoint,
    SourceOutline,
)
from src.workflows.units import build_units

WORD = 0.3
WORD_GAP = 0.05
SENTENCE_GAP = 0.4


def story_data(
    sentences: int,
    *,
    words_per_sentence: int = 12,
    speakers: tuple[str, ...] = ("A", "B"),
    turn_every: int = 5,
    questions: tuple[int, ...] = (),
    texts: Optional[dict[int, str]] = None,
    language: Optional[str] = "en",
) -> dict:
    """A word sidecar (ms) of numbered sentences: "S7 w1 w2 ... w11." with speaker turns.

    ``texts`` replaces sentence n's words (1-based) with the given text.
    ``questions`` lists sentences that end in "?".
    """
    words: list[dict] = []
    cursor = 1.0
    texts = texts or {}
    for number in range(1, sentences + 1):
        speaker = speakers[((number - 1) // turn_every) % len(speakers)] if speakers else None
        tokens = texts[number].split() if number in texts else [f"S{number}"] + [
            f"w{k}" for k in range(1, words_per_sentence)
        ]
        end_mark = "?" if number in questions else "."
        for position, token in enumerate(tokens):
            text = token
            if position == len(tokens) - 1 and not token.endswith((".", "?", "!")):
                text += end_mark
            words.append(
                {
                    "text": text,
                    "start": round(cursor * 1000),
                    "end": round((cursor + WORD) * 1000),
                    "confidence": 0.99,
                    "speaker": speaker,
                }
            )
            cursor += WORD + WORD_GAP
        cursor += SENTENCE_GAP - WORD_GAP
    return {"version": 2, "words": words, "utterances": [], "text": "", "language_code": language}


def analysis_text(data: dict) -> str:
    lines = []
    for word in data["words"]:
        start, end = word["start"] // 1000, max(word["start"] // 1000 + 1, word["end"] // 1000)
        lines.append(f"[{start // 60:02d}:{start % 60:02d} - {end // 60:02d}:{end % 60:02d}] {word['text']}")
    return "\n".join(lines)


class MemoryCache:
    def __init__(self, items: Optional[dict[str, str]] = None):
        self.items = dict(items or {})
        self.stored: list[str] = []

    async def load(self, key: str) -> Optional[str]:
        return self.items.get(key)

    async def store(self, key: str, payload: str) -> None:
        self.items[key] = payload
        self.stored.append(key)


def plan_context(
    workflow: str,
    data: Optional[dict],
    *,
    transcript_text: str = "",
    topic: str = "",
    part_count: Optional[int] = None,
    instructions: str = "",
    clip_length: str = "auto",
    cache: Optional[MemoryCache] = None,
    source_seconds: Optional[float] = None,
    cleanup: Optional[dict] = None,
    performance_context: Optional[str] = None,
    progress: Optional[Callable] = None,
) -> PlanContext:
    messages: list[tuple[int, str]] = []

    async def record(percent: int, message: str) -> None:
        messages.append((percent, message))

    if source_seconds is None:
        source_seconds = (data["words"][-1]["end"] / 1000 + 1.0) if data else 600.0
    ctx = PlanContext(
        spec=WorkflowSpec(workflow, WorkflowOptions(topic=topic, part_count=part_count, instructions=instructions), clip_length),
        task_id="task-1",
        transcript_text=transcript_text,
        transcript_data=data,
        source_seconds=source_seconds,
        cleanup_settings=cleanup or {},
        performance_context=performance_context,
        progress=progress or record,
        plan_cache=cache if cache is not None else MemoryCache(),
        outline_cache_key="outline-key",
    )
    ctx.messages = messages  # type: ignore[attr-defined]
    return ctx


def chapter(end_unit: int, kind: str = "content", importance: int = 3, title: str = "Chapter") -> OutlineChapter:
    return OutlineChapter(end_unit=end_unit, title=title, summary=f"{title} summary", kind=kind, importance=importance)


def point(first: int, last: int, core: Optional[tuple[int, int]] = None, importance: int = 4, statement: str = "A point") -> OutlinePoint:
    core_first, core_last = core or (first, last)
    return OutlinePoint(
        statement=statement, first_unit=first, last_unit=last, core_first=core_first, core_last=core_last, importance=importance
    )


def make_outline(
    *,
    chapters: list[OutlineChapter],
    points: Optional[list[OutlinePoint]] = None,
    arcs: Optional[list[OutlineArc]] = None,
    payoffs: Optional[list[OutlinePayoff]] = None,
    format: str = "podcast",
    language: str = "en",
    key_topics: Optional[list[str]] = None,
) -> SourceOutline:
    return SourceOutline(
        analysis="",
        format=format,  # type: ignore[arg-type]
        language=language,
        one_line="A video about building things.",
        central_question="Will the company survive?",
        throughline="Every decision costs something.",
        key_topics=key_topics or ["pricing", "hiring"],
        chapters=chapters,
        main_points=points or [],
        story_arcs=arcs or [],
        payoffs=payoffs or [],
    )


def plan_input(ctx: PlanContext, outline: SourceOutline, *, mode: str = "full", language: Optional[str] = "en") -> PlanInput:
    index = build_units(ctx.transcript_data, ctx.transcript_text)
    return PlanInput(
        ctx=ctx,
        index=index,
        outline=OutlineResult(outline=outline, mode=mode, cached=True),
        language=language,
        stats=PlanStats(),
    )


class ScriptedModel:
    """Answers each model call by output schema name: a list (consumed in order) or a function of the prompt."""

    def __init__(self, answers: Optional[dict[str, Any]] = None):
        self.answers = dict(answers or {})
        self.calls: list[SimpleNamespace] = []

    def agent(self, output_type, system_prompt, *, structural):
        return SimpleNamespace(output_type=output_type, system_prompt=system_prompt, structural=structural)

    async def run(self, agent, prompt, timeout_seconds=None):
        name = agent.output_type.__name__
        self.calls.append(
            SimpleNamespace(schema=name, system=agent.system_prompt, prompt=prompt, structural=agent.structural)
        )
        answer = self.answers.get(name)
        if answer is None:
            raise AssertionError(f"Unexpected model call for {name}")
        if callable(answer):
            value = answer(prompt)
        else:
            if not answer:
                raise AssertionError(f"No scripted answer left for {name}")
            value = answer.pop(0)
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(output=value, usage=lambda: SimpleNamespace(input_tokens=100, output_tokens=20))

    def schemas(self) -> list[str]:
        return [call.schema for call in self.calls]

    def install(self, monkeypatch) -> "ScriptedModel":
        for module in (outline_module, series_module, topic_module, recap_module, trailer_module):
            monkeypatch.setattr(module, "build_agent", self.agent)
        monkeypatch.setattr("src.ai._run_transcript_analysis", self.run)
        return self
