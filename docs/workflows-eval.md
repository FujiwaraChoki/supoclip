# Workflow quality evaluation

How we check that series, topic, recap and trailer plans are good enough to
ship, and how to run the check. The script is
`backend/scripts/workflow_eval.py` (not collected by pytest).

## Running it

```bash
cd backend
# The production model: LLM=anthropic:claude-sonnet-5-5, RERANK_LLM unset (defaults to LLM).
# Every run costs real tokens; keep the key in a private env file, never in the repo.
set -a; . /path/to/anthropic.env; set +a
.venv/bin/python -u scripts/workflow_eval.py --fixtures 04,07 --workflows series,recap
.venv/bin/python -u scripts/workflow_eval.py --fixtures 02,07,pr163,price,05 --judge
```

`LLM` defaults to `anthropic:claude-sonnet-5-5` when unset. Any other configured
model works the same way (`LLM=ollama:<model>` with `OLLAMA_BASE_URL` for a
local one); the outline cache is keyed by model, so switching models re-reads.

Options:

| Flag | Meaning |
|---|---|
| `--fixtures a,b` / `--all` | Which fixtures (default `02,04,05,07,pr163,price,vlz`) |
| `--workflows` | Comma list of `series,topic,recap,trailer` (default all four) |
| `--clip-length` | `auto` (default), `short`, `medium`, `long` |
| `--parts N` | Explicit series part count (default Auto) |
| `--topic` | Override every fixture's topic |
| `--instructions` | Creator instructions for every run |
| `--cleanup` | Plan with pause and filler cleanup on |
| `--judge` | Score each plan with `RERANK_LLM` against the rubric below |
| `--render` | Render the clips of fixtures that have video (`VideoService.create_single_clip`) |
| `--out` | Report directory (default `output/workflow-eval/<date>/`) |

The script rebuilds the analysis transcript from each sidecar with
`format_transcript_for_analysis`, exactly as the worker does, and calls the
real `plan_workflow`. Outlines are cached in `<out>/outline_cache.json`, keyed by
fixture and model, so changing a planner prompt and re-running re-plans without
re-reading every video. Delete that file after changing the outline prompt.

The exit code is 1 when any hard metric fails.

## Fixtures

| Name | Source | Why |
|---|---|---|
| `01`–`10` | `output/clip-quality-20261027/sources/` | English formats (interview, podcast, reaction, explainer, sports); `07` is German news with four speakers; `05` (1.6 min) must be rejected for series, recap and trailer |
| `pr163` | `~/.cache/supoclip-pr163/uploads/` | Hindi/English mix, 57 min, no language code, has video |
| `price`, `ton` | `output/article-clips-20261026/*/uploads/` | Whisper, no speakers, have video |
| `vlz` | `output/overlap-local-224/generation/uploads/` | Whisper, has video |
| `synthetic3h` | built | 02 + 04 + 10 + pr163 stitched (about 2.5 h): long mode, map-reduce, timeouts |
| `04coarse` | built | 04 without its word sidecar: the coarse (line) fallback |

Each fixture has a topic for the topic workflow (`FIXTURES` in the script).

## Hard metrics (must be 100 %)

| Check | What it verifies |
|---|---|
| cuts on word boundaries (I3) | No range edge falls inside a word |
| series continuity (I2) | Adjacent parts share no word, and no word is lost at the cut (cleanup off; the unit test covers cleanup on) |
| part lengths in budget | Every series part is inside its `clip_length` window |
| one output inside its window (I4) | Recap and trailer are one clip inside `[min, max]` (a recap on a short source may be shorter, with `TARGET_SHORTENED`) |
| no trailer beat touches a spoiler | Beats avoid outline payoffs, arc endings and story tails |
| topic outputs relevance ≥ 70 (I5) | No off-topic padding; `topic_not_found` counts as a pass |
| titles | Non-empty, at most 64 characters, in the transcript's language (script or stopword check), series titles carry the localized part label |
| short source rejected | `05` fails series, recap and trailer with `source_too_short` |

## Judge rubric (1–5, judge model plus founder review)

| Workflow | Criteria |
|---|---|
| Series | continuity (nothing repeated or missing between parts), open loops (every part but the last ends on tension), payoff in the last part, titles |
| Topic | precision (every clip substantively on topic), recall against hand labels on 02 and 04, standalone openings and endings, titles |
| Recap | coverage of the main points, coherence (plays as one piece, no dangling references), titles |
| Trailer | curiosity, no spoilers, titles |

`--judge` asks `RERANK_LLM` for these scores; the founder reviews the report
and has the final say.

## Ship bar

- Hard metrics 100 % across en, de, hi, Whisper, short, 1 h and the synthetic 3 h.
- Judge average ≥ 4 with no 1s.
- Highlights golden tests green (`test_ai_prompt.py`, the I1 tests in
  `test_task_service_workflows.py`), and the unit, integration and vitest suites green.
- One live prod-like run per workflow: the YouTube audio-first proxy path, an
  upload and a Twitch VOD.
- Deploy with `WORKFLOWS_ACCESS=admins`, review in prod, then switch to `all`.

## Report

`<out>/index.html` lists every run with its hard metrics, the plan (title,
notes, segments with their source ranges and text), series boundary excerpts
(`…last 12 words of Part N ‖ first 12 words of Part N+1…`), recap and trailer
scripts, judge scores, rendered clips (with `--render`) and per-stage model
use (`workflow_plan.stats.stages`: calls, input and output tokens, seconds).
Each plan is also written as `<fixture>-<workflow>.json` (the plan-cache payload).

## Results log

| Date | Model | Fixtures | Hard metrics | Notes |
|---|---|---|---|---|
| 2026-10-31 | anthropic:claude-sonnet-5-5 | 01 (series), 04 (topic), 07 (recap, trailer), pr163 (trailer) | all pass | Smoke runs on the production model during the WP3 review (07 trailer after the fixes, the rest before). Every schema is accepted by native structured output. 01: 8 parts, I2 holds, one repair. 04 "attention": 4 clips, relevance 0.92-0.95, reranked 92-55. 07: a 68 s recap of the bulletin's four stories and a 30 s trailer ending on "Netanjahu oder Eisenkot?". pr163 (57 min Hindi, full mode on this model): a 28 s, 7-beat trailer in Hindi. A series draft used up to 7k output tokens, close to the 8192 cap hosted models are built with, so workflow agents now allow 16384. |
| 2026-10-31 | ollama:qwen3.8:27b | 01 (series), 04 (all four), 07 (all four), 05 (all four), pr163 (all four) | all pass after the fixes below | Development sanity runs, not the ship eval. Fixed from them: important "outro" chapters that hold the finale stay in a series; recap points that start mid-sentence snap to the sentence; listings are sized by estimated tokens, not words (57 min of Hindi overflowed the local context in "full" mode; it now maps in three windows); series plans record their skips so the I2 audit can tell a planned skip from a lost word; long-mode map progress stays below the planners' 54 %. 07's topic "Bundestag" correctly returned topic_not_found (the bulletin never mentions it); the fixture now uses "Endlager". |
