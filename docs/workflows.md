# Workflows

A task makes one of five things, stored as `tasks.workflow`:

| `workflow` | Output |
|---|---|
| `highlights` (default) | The best standalone clips. The original pipeline, unchanged. |
| `series` | Part 1..N of one story, consecutive and numbered. Adjacent parts share one exact cut. |
| `topic` | Every strong standalone moment about a typed topic. Off-topic moments are never added. |
| `recap` | One clip stitched from several source ranges, covering the main points in order. |
| `trailer` | One short clip of beats that builds curiosity and never reveals the payoff. |

Free-text `instructions` steer any workflow, highlights included. Highlights
without instructions is byte-identical to the pipeline before workflows.

The `clip_length` chip means part length for a series, total length for a
recap or trailer, and per-clip length for highlights and topic clips.

## Access

| Variable | Default | Meaning |
|---|---|---|
| `WORKFLOWS_ACCESS` | `admins` | `off`: highlights without instructions only. `admins`: admins plus `WORKFLOWS_ALLOWED_EMAILS`. `all`: everyone. Unknown values fall back to `admins`. Self-hosters set `all`. |
| `WORKFLOWS_ALLOWED_EMAILS` | empty | Comma-separated beta emails when access is `admins`. |
| `WORKFLOW_TOPIC_MAX_CLIPS` | `15` | Most clips a topic run returns. |
| `WORKFLOW_PLAN_TIMEOUT_SECONDS` | `1800` | Wall-clock bound on planning; past it the task fails with `plan_failed`. |
| `WORKFLOW_FULL_CONTEXT_MAX_WORDS` | `0` | Word budget for reading a whole transcript in one outline call. `0` uses the provider default. |

The gate keeps the deploy safe: ship with `admins`, deploy the worker and API
together, then switch to `all`. An old worker never sees a workflow task from a
regular user in between.

## How a workflow runs

The API validates the request (`workflows/options.py`), checks the source length
before charging, and stores `workflow` and `workflow_options` on the task row
in the same INSERT as the minute charge. The ARQ job and the resume route are
the same for every workflow; the worker reads the row.

Download and transcription are shared with highlights. After that:

1. **Units** (`units.py`). The word-level transcript becomes numbered units:
   sentences (clauses for trailers), split on sentence punctuation in any
   script, speaker changes and pauses, with long runs force-split and tiny
   fragments merged. Every cut a planner makes is a unit edge, converted to
   word-exact seconds by code, so no planner ever addresses the video by
   timestamp and cuts land between words. Transcripts without word timings
   fall back to one unit per timed line (note `COARSE_TRANSCRIPT`); none at all
   fails with `transcript_untimed`.
2. **Source outline** (`outline.py`). One cached reading of the whole source:
   chapters (with intro, sponsor, tangent and outro marked), main points with a
   full and a core span, story arcs, payoffs, key topics and the central
   question. Videos too long for one call are read in windows and reduced; a
   window that still fails after a retry fails the task (`analysis_incomplete`)
   rather than leaving a gap. The outline is shared across workflows and users.
3. **Planner**. Each planner drafts with the model, validates, allows one
   repair round, then hands the hard constraints (lengths, contiguity,
   budgets, spoilers) to a deterministic solver. One critique reads the
   finished cut as a viewer would; its fixes become solver constraints for a
   final solve.
   - **Series** (`series.py`, `partition.py`): the whole content, or the
     strongest story arc when the video is too long to serialize whole (note
     `STORY_ARC_SELECTED`). Parts are expressed as end units only, so gaps or
     overlaps between parts cannot exist; adjacent parts share one cut. Dead
     weight inside the story (sponsor reads, tangents) is removed as whole-unit
     skips, never at a part edge and never more than 40 % of a part. A dynamic
     program picks the cuts: strong boundaries, open loops before the answer,
     no part opening on "that's why". An explicit part count that cannot fit
     is reduced (`PART_COUNT_REDUCED`).
   - **Topic** (`topic.py`): the model defines what counts and what does not,
     keyword recall finds every region that mentions it (across languages and
     scripts), windows are mined concurrently, and candidates are gated on
     relevance and standalone quality, fitted to the clip length, deduplicated
     and reranked. Off-topic moments are never added; nothing qualifying fails
     with `topic_not_found` and suggested topics. One or two clips is
     `coverage: thin`.
   - **Recap** (`recap.py`, `assembly.py`): an opener, the main points in
     source order and a closer, each with a full and a core span; a knapsack
     grows or shrinks them to the total length, then a coherence critique fixes
     junctions that would confuse a viewer.
   - **Trailer** (`trailer.py`): clause units, a deterministic spoiler set
     (payoffs, the end of each arc) the model never sees as usable, beats
     (cold open, context, escalation, button) that may jump around, and a
     spoiler judge. A trailer that would leak the ending falls back to a
     deterministic one, or fails with `trailer_unbuildable`.
4. **Plan**. The result is persisted to `tasks.workflow_plan` before anything
   renders, and the segments are a pure function of it. Plans are cached by
   source, options and models (`WORKFLOW_PLAN_VERSION`), so a resume re-renders
   without re-planning.
5. **Render**. Planned segments are locked: no start snapping, end extension or
   cold-open teaser. Multi-range clips get explicit output offsets from their
   junction fades, shared by video, captions, speaker framing, B-roll and the
   source map. Captions are recomputed from the ranges. Each segment carries
   its own render cap (`max_seconds`). Progress reads "Creating part 2/5…",
   "Creating recap 1/1…".

Highlights with instructions take the original pipeline with one extra block
in the user prompt and a derived cache key.

## Failures, refunds, cancel and resume

A workflow never pads its output. When it cannot deliver what it promised it
raises a `WorkflowError`; the error path deletes the task's clips (so the
minute refund fires), merges `{code, details}` into `workflow_plan.error`, and
sets `error_code` and an English `progress_message`. Series, recaps and
trailers are all-or-nothing for any other failure too: a missing required part
is retried once, then fails with `render_incomplete`. Cancelling keeps the
parts already rendered; resume renders the series again from the cached plan,
without re-planning.

## In the app

- **Create** (`home-app.tsx`, `workflow-picker.tsx`): "What do you want to
  make?", the topic field, the part count chip, instructions, and the length
  chip relabelled per workflow.
- **Task page** (`app/tasks/[id]/page.tsx`): a workflow badge, the plan title
  and notes under the title, and an "Instructions" tooltip.
  - Series render as an ordered list (`series-view.tsx`): "Part 2 of 5", the
    part's title, what it ends on, its post caption with a copy button,
    Download / Edit / Publish per part and "Download all parts", which
    downloads them one after another in order. A cancelled series that is
    missing parts says where it stopped and offers Resume.
  - Recaps and trailers render as one 9:16 hero (`hero-clip-view.tsx`) with the
    recap's points or the trailer's beats; clicking one seeks the player, and
    the one playing is highlighted. A split recap falls back to the grid.
  - Topic clips use the grid under "About {topic} · n moments"; a tile's title
    tooltip says why it was picked.
  - Highlights are unchanged. Scores, "Top pick" and the sort tabs only appear
    for highlights and topic clips.
  - Failures (`workflow-failure.tsx`) say what happened, that the minutes were
    refunded, and offer the next step: suggested topics as chips and "Make
    highlights instead" for `topic_not_found`, a recap instead of a series
    or trailer that could not be built (highlights when the source is under 3
    minutes), "Try again" (resume) for the retryable codes. The new task reuses
    the source, captions, font, framing, cleanup and instructions.
  - While processing, the steps and the counter use the workflow's words
    ("Mapping the story", "Part 2 of 4") and recaps/trailers hold one hero slot.
- **Library** (`generation-card.tsx`): "Series · 5 parts", "Topic · 4
  moments", "Recap", "Trailer"; scores only for scored workflows.
- **Share page**: series in part order with "Part n of N" badges, recaps and
  trailers as the hero, the workflow badge in the header. Only the public
  subset of the plan and metadata is served.
- **Publishing** prefills a part's planned post caption (and does not replace
  it with a generated one); untitled parts are "Part n".
- **Editor** clip picker: "Part 2 · The bank calls back" for parts, else the
  hook title.

## MCP

`supoclip_create_clip_task` takes `workflow`, `topic`, `part_count` (0 = auto)
and `instructions`, validated locally with the API's rules, and
`supoclip_list_workflows` reads `GET /tasks/workflows`. See `mcp/README.md`.

## Interfaces (frozen)

Every workflow package codes against these. Change them only together with
this section.

### `backend/src/workflows/contracts.py`

```python
WORKFLOWS = ("highlights", "series", "topic", "recap", "trailer")
WORKFLOW_PLAN_VERSION = "wf-v1"   # bump to invalidate cached plans (not transcripts)
OUTLINE_VERSION = "outline-v1"    # bump to invalidate cached outlines

@dataclass(frozen=True)
class WorkflowOptions:
    topic: str = ""                    # topic workflow only, normalized
    part_count: Optional[int] = None   # series only; None = auto
    instructions: str = ""             # any workflow, normalized
    def to_json(self) -> dict          # canonical, no empty values; stored in tasks.workflow_options
    @classmethod
    def from_json(cls, raw) -> WorkflowOptions   # lenient, never raises
    def for_workflow(self, workflow) -> WorkflowOptions   # drops options that do not apply

@dataclass(frozen=True)
class WorkflowSpec:
    workflow: str
    options: WorkflowOptions
    clip_length: str                   # auto | short | medium | long
    legacy: bool                       # property: highlights without instructions
    uses_performance_context: bool     # property: highlights, topic
    topic_key: str                     # property: casefolded, whitespace collapsed
    def fingerprint(self) -> str       # sha256 of canonical JSON: workflow, version, clip_length,
                                       # topic_key, part_count, sha256(instructions)[:16]

def workflow_spec_from_task(task_row, clip_length) -> WorkflowSpec
    # missing / unknown workflow (pre-migration row, MagicMock) -> highlights, empty options

ProgressFn = Callable[[int, str], Awaitable[None]]   # (percent, message); raises on cancel

class PlanCache(Protocol):
    async def load(self, key: str) -> Optional[str]
    async def store(self, key: str, payload: str) -> None

@dataclass
class PlanContext:
    spec: WorkflowSpec
    task_id: Optional[str]
    transcript_text: str             # "[MM:SS - MM:SS] Speaker: ..." analysis transcript
    transcript_data: Optional[dict]  # word sidecar (ms), from load_cached_transcript_data
    source_seconds: float            # ffprobe duration of the analysed file
    cleanup_settings: dict           # normalize_clip_cleanup_settings(...) output
    performance_context: Optional[str]
    progress: ProgressFn
    plan_cache: PlanCache
    outline_cache_key: str

@dataclass
class WorkflowResult:
    segments: list[dict]             # PlannedSegment dicts, in render order
    plan: dict                       # -> tasks.workflow_plan
    summary: Optional[str]
    key_topics: list[str]
    units_fingerprint: str
    def to_cache_json(self) -> str
    @classmethod
    def from_cache_json(cls, raw, workflow, units_fingerprint) -> Optional[WorkflowResult]
        # None unless version, workflow and units fingerprint all match
```

### `backend/src/workflows/pipeline.py`

```python
async def plan_workflow(ctx: PlanContext) -> WorkflowResult
```

Called only for `workflow != "highlights"`. Raises `WorkflowError` for
user-facing failures and lets `TaskCancelled` / `Exception("Task cancelled")`
propagate. Bounded by `WORKFLOW_PLAN_TIMEOUT_SECONDS`; a timeout raises
`WorkflowError("plan_failed", ...)`.

### `backend/src/workflows/errors.py`

```python
class WorkflowRequestError(ValueError):   # rejected at the API, before any charge
    code: str; message: str; status: int = 422; details: dict   # 403 for WORKFLOW_NOT_AVAILABLE
    def to_detail(self) -> dict            # {**details, "code", "message"}

class WorkflowError(ValueError):          # raised in the worker; str(e) == message (English, user-facing)
    code: str; message: str; details: Optional[dict]
```

Worker codes (`WorkflowError.code`, the task's `error_code`, `workflow_plan.error.code`):
`source_too_short`, `no_story`, `topic_not_found`, `recap_unsupported`,
`trailer_unbuildable`, `analysis_incomplete`, `plan_failed`,
`render_incomplete`, `transcript_untimed`. Any other code raises `ValueError`.

### `backend/src/workflows/durations.py`

```python
SERIES_MIN_PARTS = 2; SERIES_MAX_PARTS = 10
SERIES_SOURCE_HEADROOM = 1.25            # explicit N needs D >= N * part.min * 1.25
WORKFLOW_RENDER_HARD_MAX_SECONDS = 185.0
MIN_SOURCE_SECONDS = {"highlights": 0, "series": 120, "topic": 60, "recap": 180, "trailer": 180}

@dataclass(frozen=True)
class Budget:                            # seconds of speech after cleanup
    min: float; ideal_min: float; ideal_max: float; max: float
    render_cap: float                    # renderer safety cap (max_seconds on planned segments)
    target: Optional[float] = None       # recap / trailer total
    shortened: bool = False              # recap target reduced for a short source (TARGET_SHORTENED)
    ideal: float                         # property: target, else the ideal midpoint
    maximum: float; minimum: float       # properties: aliases of max / min
    def contains(self, seconds) -> bool

part_budget(clip_length) -> Budget                    # series part
recap_budget(clip_length, source_seconds) -> Budget   # window [0.85 T, 1.15 T]
trailer_budget(clip_length) -> Budget                 # window [0.8 T, 1.1 T]
clip_budget(clip_length) -> Budget                    # highlights / topic: clip_duration presets
workflow_budget(workflow, clip_length, source_seconds=0) -> Budget
max_series_parts(source_seconds, clip_length) -> int
```

| `clip_length` | series part min / ideal / max / cap | recap target | trailer target |
|---|---|---|---|
| auto | 45 / 60–100 / 150 / 155 | 45 (≤ 10 min), 60 (≤ 30 min), 90 (≤ 90 min), else 120 | 30 |
| short | 30 / 35–50 / 60 / 65 | 45 | 20 |
| medium | 45 / 55–80 / 90 / 95 | 75 | 40 |
| long | 90 / 100–150 / 175 / 180 | 120 | 60 |

A recap target above `0.6 · D` becomes `max(30, round(0.4 · D))` with
`shortened=True`. Recap render cap `min(180, 1.15 T + 5)`, trailer
`1.1 T + 5`, topic clips `maximum + 5`.

### `backend/src/workflows/options.py`

```python
TOPIC_MIN_CHARS, TOPIC_MAX_CHARS = 2, 120
INSTRUCTIONS_MAX_CHARS = 500
normalize_text(value, *, keep_newlines) -> str   # also drops lone surrogates and invisible characters
topic_is_searchable(topic) -> bool                # a letter or digit; 2+ characters, or one Han/kana/Hangul word
parse_workflow_request(data, clip_length) -> WorkflowSpec           # raises WorkflowRequestError
check_source_duration(spec, duration_seconds) -> None               # raises WorkflowRequestError
workflow_runtime_minimum_error(spec, source_seconds) -> Optional[WorkflowError]
async workflows_enabled_for(db, user_id) -> bool
workflows_info(enabled) -> dict                                      # GET /tasks/workflows body
```

`workflow_runtime_minimum_error` checks only the per-workflow minimum. An
explicit part count that no longer fits is reduced by the partitioner (note
`PART_COUNT_REDUCED`) rather than failing a running task.

### Deterministic engine (WP2: `units.py`, `partition.py`, `assembly.py`, `titles.py`, `language.py`)

```python
build_units(transcript_data, transcript_text, *, granularity="sentence") -> UnitIndex
units_fingerprint(transcript_data, transcript_text) -> str
partition_series(index, span, skips, budget, part_count, llm_ends, constraints, cleanup) -> PartitionResult
    # span: (first_unit, last_unit); skips: list[(first, last)]; constraints: PartitionConstraints
    # PartitionResult(parts: list[(first, last)], skips_by_part: list[list[(first, last)]], notes: list[dict], relaxed: bool)
fit_span(index, first, last, budget, *, anchor) -> tuple[int, int] | None
fit_recap(index, draft, budget, outline, cleanup) -> RecapCut       # draft: RecapDraftIn (assembly.py)
fit_trailer(index, beats, spoilers, budget, cleanup) -> TrailerCut   # beats: list[TrailerBeatIn] (assembly.py)
compose_title(title, *, prefix="", max_chars=64, max_words=10) -> str | None
part_label(code, n) -> str
language_name(code) -> str
opens_with_back_reference(text, code) -> bool
```

`UnitIndex` (ids are 1-based, seconds are floats): `unit(uid)`, `clamp_id(uid)`,
`range_seconds(first, last)`, `ranges_for(first, last, skips=())`,
`text(first, last, skips=())`, `speech_seconds(first, last, skips=(), cleanup=None)`,
`boundary_strength(uid)`, `unit_at(seconds)`, `listing(ids=None, *, with_durations=True)`;
attributes `units`, `words`, `granularity`, `language`, `coarse`, `fingerprint`.
`fit_recap` / `fit_trailer` take plain dataclasses defined in `assembly.py`, so
the engine never imports the LLM schemas.

### PlannedSegment (planner output, renderer input)

Highlights segments are unchanged and carry none of these keys.

```python
{
  "start_time": "12:04", "end_time": "13:20",      # labels and filename: first range, MM:SS
  "source_ranges": [[724.31, 801.92], [815.10, 840.55]],  # float seconds, play order (trailers may jump back)
  "planned": True,                                  # locked: no snapping, extension or teaser
  "junction_fades": [0.10],                         # len(source_ranges) - 1; seconds; 0 = hard cut
  "max_seconds": 155.0,                             # render safety cap (budget render_cap)
  "required": True,                                 # series / recap / trailer True, topic False
  "text": "...",                                    # display only; the renderer recomputes it
  "hook_title": "Part 2: The bank calls back",
  "reasoning": "...", "relevance_score": 1.0,
  "virality_score": 0, "hook_score": 0, "engagement_score": 0, "value_score": 0, "shareability_score": 0,
  "hook_type": None,                                # topic: the model's hook type
  "workflow_role": "part",                          # part | topic | recap | trailer
  "part_number": 2,                                 # series only
  "workflow_meta": {...},                           # stored as generated_clips.workflow_meta
}
```

### `tasks.workflow_plan`

```jsonc
{
  "version": "wf-v1", "workflow": "series",
  "language": "en", "coarse": false, "mode": "full",   // mode: full | long
  "title": "How we almost lost the company",           // series / recap / trailer title, topic canonical
  "summary": "...", "key_topics": ["..."],
  "parts":  [{"part_number": 1, "title": "...", "delivers": "...", "ends_on": "...", "post_caption": "...",
              "first_unit": 40, "last_unit": 71, "ranges": [[61.42, 133.9]], "seconds": 72.1}],
  "points": [{"label": "...", "ranges": [[...]], "output_start": 12.4}],
  "beats":  [{"role": "cold_open", "ranges": [[...]], "output_start": 0.0}],
  "topic":  {"query": "...", "canonical": "...", "coverage": "strong|thin", "found": 4,
             "suggested_topics": ["pricing", "hiring"]},
  "notes":  [{"code": "PART_COUNT_REDUCED", "params": {"requested": 8, "actual": 5}}],
  "error":  {"code": "topic_not_found", "details": {"suggested_topics": [], "mentions": ["12:30"]}},
  "stats":  {"llm_calls": 4, "repairs": 1, "critique_fixes": 2, "plan_seconds": 41.2}
}
```

Note codes: `PART_COUNT_REDUCED`, `STORY_ARC_SELECTED`, `NO_SINGLE_STORY`,
`TOPIC_THIN`, `TARGET_SHORTENED`, `COARSE_TRANSCRIPT`, `SPONSOR_SKIPPED`.

`TaskRepository.save_workflow_plan(db, task_id, plan)` replaces the plan;
`TaskRepository.merge_workflow_plan_error(db, task_id, code, details)` adds
`error` and keeps the rest. Shared results (`GET /tasks/shared/{token}`)
expose only `version`, `workflow`, `title`, `parts[].{part_number,title}`,
`points[].label`, `beats[].role`, `topic.{query,canonical,coverage,found}`
and `notes`.

### `generated_clips.workflow_meta`

Every planned clip: `{"planned": true, "ranges": [[s, e], ...], "junction_fades": [...], "max_seconds": 155}`.
`ranges` is the database copy of the planned ranges, so a lost
`.source_map.json` never falls back to the min–max span.

- part: `+ {"series_title", "part_count", "title", "delivers", "ends_on", "post_caption", "first_unit", "last_unit"}`
- topic: `+ {"topic", "topic_relevance": 0-100, "why"}`
- recap: `+ {"points": [{"label", "kind", "range_index", "output_start"}]}`
- trailer: `+ {"beats": [{"role", "range_index", "output_start"}]}`
- after a split: `+ {"split": 1|2}`

`ClipRepository.create_clip(..., workflow_role=, part_number=, workflow_meta=)`
writes them (also on the `(task_id, clip_order)` upsert);
`get_clips_by_task` and `get_clip_by_id` (the SSE `clip_ready` payload) return
them, with `workflow_meta` `{}` for highlights clips. Shared results expose
`part_number`, `workflow_role` and `workflow_meta.{title, ends_on, points, beats}`.

### `POST /tasks/`

```jsonc
{
  "source": {"url": "https://youtu.be/..."},
  "workflow": "series",                                    // default "highlights"
  "workflow_options": {"topic": "...", "part_count": "auto"},
  "instructions": "Focus on the story about the first startup",   // any workflow
  "clip_length": "auto"
}
```

`workflow_options.instructions` is also accepted; the top level wins when it is
non-empty. Options that do not apply to the workflow are dropped. The worker
reads `workflow` and `workflow_options` from the task row, so the job
arguments and the resume route are the same for every workflow.

Rejections happen before anything is charged (`detail = {"code", "message", ...}`):

| Status | Code | When |
|---|---|---|
| 422 | `INVALID_WORKFLOW` | `workflow` is present and not one of the five ids (never coerced) |
| 422 | `INVALID_WORKFLOW_OPTIONS` | `workflow_options` is not an object |
| 422 | `TOPIC_REQUIRED` / `TOPIC_TOO_LONG` | topic workflow with no letter or digit, under 2 characters (one Han, kana or Hangul character is enough), or over 120 |
| 422 | `INVALID_PART_COUNT` | series `part_count` other than `"auto"`, null or 2–10 |
| 422 | `INSTRUCTIONS_TOO_LONG` | over 500 characters after normalization |
| 403 | `WORKFLOW_NOT_AVAILABLE` | a workflow other than highlights, or instructions, outside the gate |
| 422 | `SOURCE_TOO_SHORT_FOR_SERIES` / `_RECAP` / `_TRAILER` / `_TOPIC` | source under 120 / 180 / 180 / 60 s (`min_source_seconds`; for an explicit part count that fits fewer than 2 parts, what 2 parts of that length need, e.g. 225 for long) |
| 422 | `TOO_MANY_PARTS` | explicit part count does not fit (`max_parts`) |

### `GET /tasks/workflows`

```json
{"enabled": true,
 "workflows": [
   {"id": "highlights", "available": true, "min_source_seconds": 0},
   {"id": "series", "available": true, "min_source_seconds": 120, "part_count": {"min": 2, "max": 10},
    "lengths": {"auto": [45, 150], "short": [30, 60], "medium": [45, 90], "long": [90, 175]}},
   {"id": "topic", "available": true, "min_source_seconds": 60},
   {"id": "recap", "available": true, "min_source_seconds": 180, "lengths": {"auto": null, "short": 45, "medium": 75, "long": 120}},
   {"id": "trailer", "available": true, "min_source_seconds": 180, "lengths": {"auto": 30, "short": 20, "medium": 40, "long": 60}}],
 "topic_max_chars": 120, "instructions_max_chars": 500}
```

`enabled` false means only highlights without instructions.

### Read paths

- `GET /tasks/{id}`: `workflow`, `workflow_options`, `workflow_plan`,
  `source_duration_seconds`, `share_enabled`, `error_code`; clips carry
  `workflow_role`, `part_number`, `workflow_meta`.
- `GET /tasks/`: `workflow`, `workflow_title` (plan title), `part_count`
  (planned parts). Series, recap and trailer covers are the first clip, not the
  best-scored one.

### Frontend: `frontend/src/lib/workflows.ts`

`WORKFLOWS`, `Workflow`, `isWorkflow`, `taskWorkflow`, `SINGLE_OUTPUT`,
`SCORED`, `PART_COUNT_OPTIONS`, `TOPIC_MIN_CHARS`, `TOPIC_MAX_CHARS`, `INSTRUCTIONS_MAX_CHARS`,
`normalizeTopic` and `topicIssue` (the API's topic check, counting characters, not UTF-16 units),
`MIN_SOURCE_SECONDS`, `WORKFLOW_STORAGE_KEY`, the `WorkflowOptions`,
`WorkflowPlan`, `WorkflowMeta`, `WorkflowsInfo` and error-code types,
`defaultSort`, `expectedHighlightCount` and `expectedOutputs`.

### Data model

Migration `backend/src/migrations/sql/20261031_0001_task_workflows.sql`,
mirrored in `init.sql` and `backend/src/models.py`:

- `tasks.workflow VARCHAR(20) NOT NULL DEFAULT 'highlights'` with
  `tasks_workflow_check`, `tasks.workflow_options JSONB NOT NULL DEFAULT '{}'`,
  `tasks.workflow_plan JSONB`, index `idx_tasks_user_workflow (user_id, workflow)`.
- `generated_clips.workflow_role VARCHAR(20)`, `generated_clips.part_number INTEGER`,
  `generated_clips.workflow_meta JSONB NOT NULL DEFAULT '{}'`. No unique
  `(task_id, part_number)`: a split copies the part number onto both halves.

Plain highlights tasks are inserted without the new columns, and clip reads
and writes fall back when a worker runs ahead of the migration. A workflow task
is never stored without them: it would run as highlights.
