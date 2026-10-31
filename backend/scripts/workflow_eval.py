"""Quality evaluation for the workflow planners (series, topic, recap, trailer).

Runs every requested workflow on real transcript sidecars, checks the hard
metrics that must hold 100 % (cuts on word boundaries, series continuity,
budgets, spoilers, topic relevance, titles, short-source rejection) and
writes an HTML report with plans, boundary excerpts, scripts and per-stage
model use. Optionally renders the clips (fixtures that have video) and asks
a judge model to score each plan against the rubric in docs/workflows-eval.md.

Not collected by pytest. Uses the production model (``LLM``, default
``anthropic:claude-sonnet-5-5``; ``RERANK_LLM`` defaults to it), so every run
costs real tokens. Load ``ANTHROPIC_API_KEY`` from a private env file first:

    cd backend
    set -a; . /path/to/anthropic.env; set +a
    .venv/bin/python scripts/workflow_eval.py --fixtures 04,07 --workflows series,recap
    .venv/bin/python scripts/workflow_eval.py --fixtures 02,07,pr163,price,05 --judge

Outlines are cached in ``<out>/outline_cache.json`` (keyed by fixture and
model), so re-running a workflow re-plans it without re-reading the video.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as dt
import hashlib
import html
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("LLM", "anthropic:claude-sonnet-5-5")

SOURCES = Path("/home/loki/Work/supoclip/output/clip-quality-20261027/sources")
ARTICLES = Path("/home/loki/Work/supoclip/output/article-clips-20261026")


@dataclass
class Fixture:
    name: str
    sidecar: Optional[Path]
    topic: str
    video: Optional[Path] = None
    note: str = ""
    expect_rejected: tuple[str, ...] = ()
    build: Optional[str] = None  # "synthetic" | "coarse"


FIXTURES: dict[str, Fixture] = {
    "01": Fixture("01", SOURCES / "01.transcript_cache.json", "Tampa", note="en interview, 16 min"),
    "02": Fixture("02", SOURCES / "02.transcript_cache.json", "caffeine", note="en podcast, 35 min"),
    "03": Fixture("03", SOURCES / "03.transcript_cache.json", "skill gap", note="en reaction, 14 min"),
    "04": Fixture("04", SOURCES / "04.transcript_cache.json", "attention", note="en explainer, 27 min"),
    "05": Fixture(
        "05", SOURCES / "05.transcript_cache.json", "eggs", note="en, 1.6 min (too short)",
        expect_rejected=("series", "recap", "trailer"),
    ),
    "06": Fixture("06", SOURCES / "06.transcript_cache.json", "virtual DOM", note="en, 2.1 min"),
    "07": Fixture("07", SOURCES / "07.transcript_cache.json", "Endlager", note="de news, 16 min"),
    "08": Fixture("08", SOURCES / "08.transcript_cache.json", "Harry Kane", note="en football, 16 min"),
    "09": Fixture("09", SOURCES / "09.transcript_cache.json", "sound", note="en, 1.5 min"),
    "10": Fixture("10", SOURCES / "10.transcript_cache.json", "dopamine", note="en podcast, 34 min"),
    "pr163": Fixture(
        "pr163", Path("/home/loki/.cache/supoclip-pr163/uploads/original-upload.transcript_cache.json"),
        "डॉक्टर", video=Path("/home/loki/.cache/supoclip-pr163/uploads/original-upload.mkv"),
        note="hi/mixed, 57 min, no language code",
    ),
    "price": Fixture(
        "price", ARTICLES / "price/uploads/price.transcript_cache.json", "the bet",
        video=ARTICLES / "price/uploads/price.webm", note="Whisper, no speakers, 29 min",
    ),
    "ton": Fixture(
        "ton", ARTICLES / "ton/uploads/ton.transcript_cache.json", "money",
        video=ARTICLES / "ton/uploads/ton.ogv", note="Whisper, no speakers, 12 min",
    ),
    "vlz": Fixture(
        "vlz",
        Path("/home/loki/Work/supoclip/output/overlap-local-224/generation/uploads/youtube-vLzdmlIqyGg.transcript_cache.json"),
        "crime",
        video=Path("/home/loki/Work/supoclip/output/overlap-local-224/generation/uploads/youtube-vLzdmlIqyGg.mp4"),
        note="Whisper, 9 min",
    ),
    "synthetic3h": Fixture("synthetic3h", None, "sleep", build="synthetic", note="02+04+10+pr163 stitched, ~2.5 h"),
    "04coarse": Fixture("04coarse", None, "attention", build="coarse", note="04 without words (coarse lines)"),
}
DEFAULT_FIXTURES = ["02", "04", "05", "07", "pr163", "price", "vlz"]
WORKFLOW_NAMES = ("series", "topic", "recap", "trailer")


# -- inputs -------------------------------------------------------------------------------


def _load_sidecar(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _synthetic() -> dict:
    """02 + 04 + 10 + pr163 back to back, 5 s apart, as one sidecar."""
    words: list[dict] = []
    utterances: list[dict] = []
    offset = 0
    for name in ("02", "04", "10", "pr163"):
        data = _load_sidecar(FIXTURES[name].sidecar)  # type: ignore[arg-type]
        for word in data["words"]:
            words.append({**word, "start": word["start"] + offset, "end": word["end"] + offset})
        for utterance in data.get("utterances") or []:
            utterances.append(
                {
                    **utterance,
                    "start": utterance["start"] + offset,
                    "end": utterance["end"] + offset,
                    "words": [
                        {**word, "start": word["start"] + offset, "end": word["end"] + offset}
                        for word in utterance.get("words") or []
                    ],
                }
            )
        offset = words[-1]["end"] + 5000
    return {"version": 2, "words": words, "utterances": utterances, "text": "", "language_code": "en"}


def analysis_transcript(data: dict) -> str:
    """The analysis transcript the worker builds from the same sidecar."""
    from src.media.transcription import format_transcript_for_analysis

    def word(item: dict) -> SimpleNamespace:
        return SimpleNamespace(
            text=item.get("text", ""), start=int(item["start"]), end=int(item["end"]),
            confidence=item.get("confidence", 1.0), speaker=item.get("speaker"),
        )

    utterances = [
        SimpleNamespace(
            text=item.get("text", ""), start=int(item["start"]), end=int(item["end"]),
            speaker=item.get("speaker"), words=[word(w) for w in item.get("words") or []],
        )
        for item in data.get("utterances") or []
    ]
    transcript = SimpleNamespace(utterances=utterances, words=[word(w) for w in data.get("words") or []])
    return "\n".join(format_transcript_for_analysis(transcript))


def load_fixture(fixture: Fixture) -> tuple[Optional[dict], str]:
    if fixture.build == "synthetic":
        data = _synthetic()
        return data, analysis_transcript(data)
    if fixture.build == "coarse":
        data = _load_sidecar(FIXTURES["04"].sidecar)  # type: ignore[arg-type]
        return None, analysis_transcript(data)
    data = _load_sidecar(fixture.sidecar)  # type: ignore[arg-type]
    return data, analysis_transcript(data)


class FileCache:
    """PlanCache on a JSON file, so outlines survive between eval runs."""

    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    async def load(self, key: str) -> Optional[str]:
        return self.items.get(key)

    async def store(self, key: str, payload: str) -> None:
        self.items[key] = payload
        self.path.write_text(json.dumps(self.items))


# -- hard metrics ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Run:
    fixture: str
    workflow: str
    seconds: float = 0.0
    error: Optional[dict] = None
    result: Any = None
    checks: list[Check] = field(default_factory=list)
    judge: Optional[dict] = None
    rendered: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)


def _words(index) -> list:
    return index.words


def check_unit_edges(index, segments: list[dict]) -> Check:
    """I3: no range edge cuts through a word."""
    if index.coarse:
        return Check("cuts on word boundaries (I3)", True, "coarse transcript: line bounds")
    words = index.words
    starts = [word.start for word in words]
    import bisect

    problems = []
    for segment in segments:
        for start, end in segment["source_ranges"]:
            position = bisect.bisect_right(starts, start) - 1
            if 0 <= position < len(words) and words[position].start < start - 1e-3 and words[position].end > start + 1e-3:
                problems.append(f"{start:.2f} cuts '{words[position].text}'")
            position = bisect.bisect_left(starts, end) - 1
            if 0 <= position < len(words) and words[position].start < end - 1e-3 and words[position].end > end + 1e-3:
                problems.append(f"{end:.2f} cuts '{words[position].text}'")
    return Check("cuts on word boundaries (I3)", not problems, "; ".join(problems[:5]))


def _range_words(data: dict, ranges: list) -> list[tuple]:
    from src.media.timeline import get_absolute_words_in_range

    keys = []
    for start, end in ranges:
        for word in get_absolute_words_in_range(data, start, end):
            keys.append((word["start"], word["end"], word["text"]))
    return keys


def check_series_continuity(data: Optional[dict], index, result) -> Check:
    """I2 (cleanup off): adjacent parts share no word, and every word between
    Part 1's start and the last part's end plays exactly once unless it sits in
    a planned skip."""
    if not data or index.coarse:
        return Check("series continuity (I2)", True, "coarse transcript")
    segments, plan = result.segments, result.plan
    skipped: set[tuple] = set()
    for first, last in plan.get("skips") or []:
        for uid in range(first, last + 1):
            unit = index.unit(uid)
            skipped.update((w.start, w.end) for w in index.words[unit.w0 : unit.w1 + 1])
    parts = [_range_words(data, segment["source_ranges"]) for segment in segments]
    problems = []
    for number, (left, right) in enumerate(zip(parts, parts[1:]), start=1):
        shared = set(left) & set(right)
        if shared:
            problems.append(f"parts {number}/{number + 1} repeat {len(shared)} word(s)")
    played = [key for part in parts for key in part]
    if len(played) != len(set(played)):
        problems.append("a word plays twice")
    first_unit, last_unit = plan["parts"][0]["first_unit"], plan["parts"][-1]["last_unit"]
    start, end = index.unit(first_unit).w0, index.unit(last_unit).w1
    heard = {(round(s, 3), round(e, 3)) for s, e, _ in played}
    missing = [
        word.text
        for word in index.words[start : end + 1]
        if (word.start, word.end) not in skipped and (round(word.start, 3), round(word.end, 3)) not in heard
    ]
    if missing:
        problems.append(f"{len(missing)} word(s) never play: {' '.join(missing[:8])}")
    return Check("series continuity (I2)", not problems, "; ".join(problems[:5]))


def check_budget(workflow: str, result, clip_length: str, source_seconds: float) -> Check:
    from src.workflows.durations import workflow_budget

    budget = workflow_budget(workflow, clip_length, source_seconds)
    plan = result.plan
    if workflow == "series":
        bad = [f"part {p['part_number']} {p['seconds']}s" for p in plan["parts"] if not budget.min - 0.5 <= p["seconds"] <= budget.max + 0.5]
        return Check("part lengths in budget", not bad, ", ".join(bad) or f"{budget.min:.0f}-{budget.max:.0f}s")
    if workflow in ("recap", "trailer"):
        seconds = plan.get("seconds", 0)
        ok = budget.min - 0.5 <= seconds <= budget.max + 0.5 or (
            workflow == "recap" and any(n["code"] == "TARGET_SHORTENED" for n in plan["notes"]) and seconds <= budget.max + 0.5
        )
        return Check("one output inside its window (I4)", ok and len(result.segments) == 1, f"{seconds}s in {budget.min:.0f}-{budget.max:.0f}s")
    estimate = []
    for segment in result.segments:
        start, end = segment["source_ranges"][0]
        if end - start > budget.max + 3:
            estimate.append(f"{end - start:.0f}s")
    return Check("topic clip lengths", not estimate, ", ".join(estimate))


def check_trailer_spoilers(plan_input, clauses, result) -> Check:
    from src.workflows import trailer

    spoilers = trailer._to_clause_ids(plan_input, clauses, trailer.spoiler_sentences(plan_input))
    problems = []
    for start, end in result.segments[0]["source_ranges"]:
        heard = [unit.id for unit in clauses.units if unit.start < end - 1e-3 and unit.end > start + 1e-3]
        touched = [uid for uid in heard if uid in spoilers]
        if touched:
            problems.append(f"{start:.1f}-{end:.1f} uses spoiler units {touched[:3]}")
    return Check("no trailer beat touches a spoiler", not problems, "; ".join(problems))


def check_topic_relevance(result) -> Check:
    low = [s["workflow_meta"]["topic_relevance"] for s in result.segments if s["workflow_meta"]["topic_relevance"] < 70]
    return Check("topic outputs relevance >= 70 (I5)", not low, str(low))


_STOPWORDS = {
    "en": {"the", "a", "of", "and", "to", "is", "how", "why", "what", "you", "your", "in", "it", "that", "this", "with"},
    "de": {"der", "die", "das", "und", "ist", "wie", "warum", "was", "ein", "eine", "nicht", "mit", "für", "im", "in", "zu"},
    "es": {"el", "la", "los", "las", "y", "es", "que", "por", "cómo", "una", "un", "con", "para", "del"},
    "pt": {"o", "a", "os", "as", "e", "é", "que", "por", "como", "uma", "um", "com", "para", "do", "da"},
    "fr": {"le", "la", "les", "et", "est", "que", "pour", "comment", "une", "un", "avec", "du", "des"},
}


def _title_language(title: str) -> Optional[str]:
    from src.workflows.language import detect_script_language

    script = detect_script_language(title)
    if script:
        return script
    tokens = {token.strip(".,:;!?¿¡").casefold() for token in title.split()}
    hits = {code: len(tokens & words) for code, words in _STOPWORDS.items()}
    best = max(hits.values())
    leaders = [code for code, count in hits.items() if count == best]
    return leaders[0] if best and len(leaders) == 1 else None


def check_titles(workflow: str, result, language: Optional[str]) -> Check:
    from src.workflows.language import part_label

    problems = []
    for number, segment in enumerate(result.segments, start=1):
        title = segment.get("hook_title") or ""
        if not title.strip():
            problems.append(f"clip {number}: empty title")
            continue
        if len(title) > 64:
            problems.append(f"clip {number}: {len(title)} chars")
        if workflow == "series" and not title.startswith(part_label(language, number)):
            problems.append(f"part {number}: label missing in '{title}'")
        detected = _title_language(title)
        if language in _STOPWORDS and detected in _STOPWORDS and detected != language:
            problems.append(f"clip {number}: '{title}' reads as {detected}")
        if language in ("hi", "zh", "ja", "ko", "ar") and detected is None and workflow != "series":
            problems.append(f"clip {number}: '{title}' not in the source script")
    return Check("titles non-empty, <= 64 chars, in the source language", not problems, "; ".join(problems[:5]))


# -- report -----------------------------------------------------------------------------------


def _excerpt(text: str, words: int, tail: bool) -> str:
    parts = text.split()
    return " ".join(parts[-words:] if tail else parts[:words])


def boundary_excerpts(result) -> list[str]:
    segments = result.segments
    lines = []
    for number, (left, right) in enumerate(zip(segments, segments[1:]), start=1):
        lines.append(
            f"…{_excerpt(left['text'], 12, True)} ‖ Part {number + 1}: {_excerpt(right['text'], 12, False)}…"
        )
    return lines


def render_report(runs: list[Run], out: Path, model: str) -> Path:
    rows = []
    for run in runs:
        status = "error" if run.error and not run.checks else ("pass" if run.passed else "FAIL")
        rows.append(
            f"<tr class='{status}'><td>{run.fixture}</td><td>{run.workflow}</td><td>{status}</td>"
            f"<td>{run.seconds:.0f}s</td><td>{html.escape(json.dumps(run.judge or {}))[:200]}</td></tr>"
        )
    sections = []
    for run in runs:
        body = [f"<h2 id='{run.fixture}-{run.workflow}'>{run.fixture} · {run.workflow}</h2>"]
        if run.error:
            body.append(f"<p class='err'>{html.escape(json.dumps(run.error, ensure_ascii=False))}</p>")
        for check in run.checks:
            body.append(f"<div class='{'pass' if check.ok else 'FAIL'}'>{'✓' if check.ok else '✗'} {html.escape(check.name)} <small>{html.escape(check.detail)}</small></div>")
        if run.result is not None:
            plan = run.result.plan
            body.append(f"<p><b>{html.escape(plan.get('title') or '')}</b> — {html.escape(plan.get('summary') or '')}</p>")
            if plan.get("notes"):
                body.append(f"<p>Notes: {html.escape(json.dumps(plan['notes'], ensure_ascii=False))}</p>")
            body.append("<ol>")
            for segment in run.result.segments:
                ranges = ", ".join(f"{s:.1f}–{e:.1f}" for s, e in segment["source_ranges"])
                body.append(
                    f"<li><b>{html.escape(segment.get('hook_title') or '')}</b> <small>[{ranges}]</small>"
                    f"<br><small>{html.escape(segment.get('reasoning') or '')}</small>"
                    f"<p>{html.escape(segment['text'])}</p></li>"
                )
            body.append("</ol>")
            if run.workflow == "series":
                body.append("<h3>Boundaries</h3><ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in boundary_excerpts(run.result)) + "</ul>")
            body.append(f"<details><summary>plan</summary><pre>{html.escape(json.dumps(plan, indent=2, ensure_ascii=False))}</pre></details>")
        for path in run.rendered:
            body.append(f"<video controls width='270' src='{html.escape(path)}'></video>")
        sections.append("\n".join(body))
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Workflow eval {dt.date.today()}</title>
<style>body{{font:14px system-ui;max-width:1100px;margin:2em auto}}.FAIL{{color:#b00}}.pass{{color:#070}}.err{{color:#b00}}
td{{padding:2px 8px}}pre{{white-space:pre-wrap;font-size:12px}}li p{{margin:.3em 0 1em}}</style></head><body>
<h1>Workflow eval</h1><p>Model: {html.escape(model)} · {dt.datetime.now():%Y-%m-%d %H:%M}</p>
<table><tr><th>fixture</th><th>workflow</th><th>hard metrics</th><th>time</th><th>judge</th></tr>{''.join(rows)}</table>
{''.join(sections)}</body></html>"""
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


# -- judge ----------------------------------------------------------------------------------------


JUDGE_PROMPT = """You review short-form edits planned from a long video. Score each criterion 1-5 (5 = a top editor would ship it as is, 1 = broken) and explain in at most 80 words.

series: continuity (no repeated or missing sentence between parts), open_loops (every part but the last ends on a question or tension), payoff (the last part resolves the story), titles.
topic: precision (every clip is substantively about the topic), standalone (each opens and ends cleanly), titles.
recap: coverage (the main points are there), coherence (it plays as one piece, no dangling references), titles.
trailer: curiosity (makes you want the full video), no_spoilers (never shows the outcome), titles."""


async def judge(run: Run, outline_text: str) -> dict:
    from pydantic import BaseModel

    from src.workflows.llm import build_agent
    from src.workflows.llm import run as run_model

    class Score(BaseModel):
        reasoning: str
        criteria: list[str]
        scores: list[int]

    agent = build_agent(Score, JUDGE_PROMPT, structural=True)
    clips = "\n\n".join(
        f"[{n}] {s.get('hook_title') or ''}\n{s['text']}" for n, s in enumerate(run.result.segments, start=1)
    )
    prompt = f"Workflow: {run.workflow}\n\nWhat the video is about:\n{outline_text}\n\nThe edit:\n{clips}"
    output = (await run_model(agent, prompt)).output
    return {"reasoning": output.reasoning, **dict(zip(output.criteria, output.scores))}


# -- main ------------------------------------------------------------------------------------------


async def evaluate(args: argparse.Namespace) -> int:
    from src.clip_cleanup import normalize_clip_cleanup_settings
    from src.config import get_config
    from src.workflows import pipeline, trailer
    from src.workflows.contracts import OUTLINE_VERSION, PlanContext, WorkflowOptions, WorkflowSpec
    from src.workflows.errors import WorkflowError
    from src.workflows.llm import PlanStats
    from src.workflows.outline import get_outline
    from src.workflows.planning import PlanInput, plan_language
    from src.workflows.units import build_units

    cfg = get_config()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = FileCache(out / "outline_cache.json")
    cleanup = normalize_clip_cleanup_settings(
        cut_long_pauses=args.cleanup, remove_filler_words=args.cleanup
    )
    runs: list[Run] = []
    names = list(FIXTURES) if args.all else [name.strip() for name in args.fixtures.split(",") if name.strip()]
    workflows = [name.strip() for name in args.workflows.split(",") if name.strip()]
    for name in names:
        fixture = FIXTURES[name]
        data, transcript = load_fixture(fixture)
        index = build_units(data, transcript)
        source_seconds = index.unit(len(index)).end
        language = plan_language(index, data)
        outline_key = hashlib.sha256(f"eval:{name}|{OUTLINE_VERSION}|{cfg.llm}|{cfg.rerank_llm}".encode()).hexdigest()
        print(f"\n== {name} ({fixture.note}): {len(index)} units, {source_seconds / 60:.1f} min, language {language}")
        for workflow in workflows:
            options = WorkflowOptions(
                topic=args.topic or fixture.topic if workflow == "topic" else "",
                part_count=args.parts if workflow == "series" else None,
                instructions=args.instructions or "",
            )
            spec = WorkflowSpec(workflow, options, args.clip_length)
            current = Run(name, workflow)

            async def progress(percent: int, message: str) -> None:
                print(f"   [{percent}%] {message}")

            ctx = PlanContext(
                spec=spec, task_id=None, transcript_text=transcript, transcript_data=copy.deepcopy(data),
                source_seconds=source_seconds, cleanup_settings=cleanup, performance_context=None,
                progress=progress, plan_cache=cache, outline_cache_key=outline_key,
            )
            print(f" -> {workflow}")
            started = time.monotonic()
            try:
                current.result = await pipeline.plan_workflow(ctx)
            except WorkflowError as error:
                current.error = {"code": error.code, "message": error.message, "details": error.details}
            current.seconds = time.monotonic() - started
            if workflow in fixture.expect_rejected:
                current.checks.append(
                    Check(
                        "short source rejected",
                        current.error is not None and current.error["code"] == "source_too_short",
                        json.dumps(current.error),
                    )
                )
                runs.append(current)
                continue
            if current.result is None:
                current.checks.append(Check("planned", workflow == "topic" and current.error and current.error["code"] == "topic_not_found", json.dumps(current.error, ensure_ascii=False)))
                runs.append(current)
                print(f"    {current.error}")
                continue
            result = current.result
            current.checks.append(check_unit_edges(index, result.segments))
            current.checks.append(check_budget(workflow, result, args.clip_length, source_seconds))
            current.checks.append(check_titles(workflow, result, result.plan.get("language")))
            if workflow == "series":
                current.checks.append(check_series_continuity(data, index, result))
            if workflow == "topic":
                current.checks.append(check_topic_relevance(result))
            if workflow == "trailer":
                outline = await get_outline(ctx, index, "", PlanStats())
                plan_input = PlanInput(ctx=ctx, index=index, outline=outline, language=language, stats=PlanStats())
                clauses = await trailer.clause_units(plan_input)
                current.checks.append(check_trailer_spoilers(plan_input, clauses, result))
            if args.judge:
                outline = await get_outline(ctx, index, "", PlanStats())
                summary = f"{outline.outline.one_line}\n{outline.outline.central_question}"
                try:
                    current.judge = await judge(current, summary)
                except Exception as error:  # the judge is advisory
                    current.judge = {"error": type(error).__name__}
            if args.render and fixture.video and fixture.video.exists():
                current.rendered = await render(fixture, result, out, cleanup, workflow)
            for check in current.checks:
                print(f"    {'✓' if check.ok else '✗'} {check.name} {check.detail}")
            stats = result.plan.get("stats", {})
            print(f"    {len(result.segments)} output(s), {stats.get('llm_calls')} calls, {current.seconds:.0f}s")
            (out / f"{name}-{workflow}.json").write_text(
                result.to_cache_json() if hasattr(result, "to_cache_json") else "{}", encoding="utf-8"
            )
            runs.append(current)
    report = render_report(runs, out, cfg.llm)
    failed = [run for run in runs if not run.passed]
    print(f"\nReport: {report}\n{len(runs) - len(failed)}/{len(runs)} runs pass every hard metric")
    return 1 if failed else 0


async def render(fixture: Fixture, result, out: Path, cleanup: dict, workflow: str) -> list[str]:
    from src.services.video_service import VideoService

    rendered = []
    clip_dir = out / "clips" / f"{fixture.name}-{workflow}"
    clip_dir.mkdir(parents=True, exist_ok=True)
    for position, segment in enumerate(result.segments):
        info = await VideoService.create_single_clip(
            fixture.video, segment, position, clip_dir, cleanup_settings=cleanup,
            max_clip_seconds=float(segment.get("max_seconds") or 60),
        )
        if info and info.get("path"):
            rendered.append(os.path.relpath(info["path"], out))
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures", default=",".join(DEFAULT_FIXTURES), help=f"comma list of {', '.join(FIXTURES)}")
    parser.add_argument("--all", action="store_true", help="every fixture")
    parser.add_argument("--workflows", default=",".join(WORKFLOW_NAMES))
    parser.add_argument("--clip-length", default="auto", choices=("auto", "short", "medium", "long"))
    parser.add_argument("--parts", type=int, default=None, help="explicit series part count")
    parser.add_argument("--topic", default="", help="override every fixture's topic")
    parser.add_argument("--instructions", default="")
    parser.add_argument("--cleanup", action="store_true", help="plan with pause and filler cleanup on")
    parser.add_argument("--judge", action="store_true", help="score each plan with RERANK_LLM")
    parser.add_argument("--render", action="store_true", help="render clips for fixtures with video")
    parser.add_argument("--out", default=str(REPO / "output" / "workflow-eval" / f"{dt.date.today():%Y-%m-%d}"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
