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
| `--settings` | Advanced settings as JSON, applied where they exist: `'{"cold_open": true}'` |
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
| no trailer beat touches a spoiler | Beats avoid outline payoffs, arc endings and story tails, except clauses the plan lists in `reclaimed` (setups set free on purpose, never in a story or a vlog) |
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
| 2026-11-01 | anthropic:claude-sonnet-5-5 | 02 (outline, series ×2) | all pass | Sanity run of the second WP3 review. Outline (full mode, 20k in / 3.9k out, 25 s): 17 chapters with concrete summaries, 15 main points, 3 arcs, 8 payoffs; intro and sign-off marked. Series (arc mode, 10:45–27:02, 9 parts of 82–131 s): parts 1–4 each end on the interviewer's question and the next part opens with the answer; skips cut a disclaimer and an aside. Run 1 found three defects, fixed before run 2: Part 6 ended on the answer to its own ends_on question (critique and retitle prompts now check the last two units), the series ended on a listener's "Right." (trailing replies are trimmed), and a critique skip cut a sentence in half ("sleep aids, [skip] We know..."; skips must now cover whole sentences). The draft wrote 14.9k output tokens (thinking counts) against the old 16k cap, so workflow agents now allow 32k. |
| 2026-11-01 | anthropic:claude-sonnet-5-5 | 07 (series), 02 (topic "caffeine"), 04 (recap, trailer), 05 (series, recap, trailer) | all pass after the eval fix below | Integration smoke, one fixture per workflow. 07 series (de news bulletin, 16 min, whole-story mode): 10 parts of 67–128 s, German titles ("Teil 1: Rekordandrang bei Israels Wahl"), 5 calls, 179 s. 02 topic: 3 clips, relevance 95–96, all about caffeine timing (half-life, the crash, the cut-off time), 4 calls, 23 s. 04 recap: 4 points in order, 68.0 s in the 51–69 s window. 04 trailer: 5 beats (cold open, 3 escalations, button), 25.0 s in 24–33 s, no spoiler units. 05: all three rejected with `source_too_short` before any model call. The first 07 report flagged an I2 miss ("Das" never plays). It was the eval's check: part 2's cut-in sits half a millisecond after part 1's end, so the clipped word's times no longer matched its source times. The check now keys played words by source word, and still catches a real gap or overlap. Quality notes for the judged eval: in 07, part 4 opens on the anchor's sign-off "Vielen Dank, Björn Daake in Tel Aviv." (the critique moved part 3's end one unit earlier) and part 8 opens on "Diese Frist ließ Bolsonaro aber verstreichen." (German demonstratives now count as back-references in the partitioner). In the 04 trailer, the button ends on a clause cut ("…bits of background knowledge—") rather than a question. |
| 2026-11-05 | anthropic:claude-sonnet-5-5 | r4 and r5: recap and series on 01, 02, 04, 04coarse, 07, pr163, price, synthetic3h, vlz | all pass | Recap with on-screen points and whole-video coverage: judge mean 2.63 (r3), 2.74 (r4, thirds), 2.70 (r5, quarters), flat within judge noise; no 1s in r5. Coverage itself moved: the latest source second played went from 235 to 1392 on 04 (27 min) and from 2486 to 3246 on pr163 (57 min). `target_seconds: 120` on 02, 04, 07, price: 3.0 against 2.42 on the same four (single runs). Series cold open, same parts judged twice with and without it: opening 2.69 to 2.88, the other four criteria 3.22 to 2.97, so it stays off by default. The judge now sees on-screen labels and marks cold opens, and series gains an `opening` criterion, left out of averages compared with r3. |
| 2026-11-03 | anthropic:claude-sonnet-5-5 | Planner tuning round 2 (smoke only, no judge): 02 (all four), price (series, trailer), 04 and 07 (topic, recap, trailer), 01 and pr163 (series, recap) | all pass after the fixes noted | Editors' critiques of r2 applied; see the commit. Series now cut a season from chosen chapters instead of the first N minutes (02: caffeine, alcohol, melatonin, a bad night, the worry journal). Topic allows 90 s on auto, and a finishing pass moves edges. Recap's critique returns the final cut itself. Trailers sell one thread, with titles heard in the beats; 04 did not fall back in this run. A 07 recap failed with `recap_unsupported` on the first code. The likely cause, not reproduced, is the opener merge pushing it over its maximum; the merge is now guarded and the one rerun passed. The series draft no longer writes copy, because its thinking hit the 32k output cap on `price` (a list of seven items) with the fuller contract; that draft still used 31k, so it stays the plan closest to the cap. Cost on 02: trailer 7 calls and 20k output tokens (r2: 3 and 6k, a redraft was triggered), recap and topic about the same. Outlines were re-read (`outline-v2`), so an r3 needs a fresh `outline_cache.json`. Outputs are in `output/workflow-eval/r3-smoke*`. |
| 2026-11-01 | anthropic:claude-sonnet-5-5 | Planner tuning round 1: 04, 07, vlz (recap, trailer); 04, 07, 02 (series, topic, trailer); price, vlz, pr163 (render) | all pass on the final code | Recap now uses one exact selection over core/full candidates in the window instead of grow-then-shrink. `TARGET_SHORTENED` appears only for a short source. Recaps: 04 63.2 s, 07 65.5 s, vlz 49.7 and 49.0 s, price 65.4 s (each was 30-41 s before). Trailers hard-cut (`TRAILER_JUNCTION_FADE = 0`). In the renders, video and audio are within 0.07 s of each other on every clip (pr163 trailer 29.93/30.00 s; it used to freeze at 15.3 s). Series span limit is now 10 × ideal_max with no 0.7 inflation. 04 produced 5-7 parts; the draft fell from 26k to 4k output tokens. 07 produced 3 Israel parts with question titles; 02 produced 9 parts opening on the REM-paralysis hook. One 04 trailer came back `trailer_unbuildable` before the outline-fallback fix and built on rerun. Outputs are in `output/workflow-eval/round1-tune*`. |
