# Dynamic edits

Dynamic edits make a clip look cut by a short-form editor: animated captions, sound effects, a music bed, zooms,
transitions, speed ramps and freezes, colour grades and retention overlays, each placed on a moment that matters.
An LLM finds the moments ("beats"); deterministic code decides what happens on them; one TypeScript renderer
draws every frame, in the browser and on the worker.

The feature is behind the `dynamic_edits` feature flag. With intensity **Off** and a classic caption template,
nothing new runs: prompts, cache keys, job arguments and renders are byte-identical to before (pinned by
`backend/tests/unit/test_dynamic_edits_legacy_pins.py` and the existing legacy pins).

Contents: [For users](#for-users) · [Caption styles](#animated-caption-styles) · [Effects](#effects) ·
[Intensity budgets](#intensity-and-budgets) · [Configuration](#configuration) · [Architecture](#architecture) ·
[Assets and drop-ins](#assets-and-self-hoster-drop-ins) · [Rollout](#rollout) · [Fallbacks](#fallbacks) ·
[Known gaps](#known-gaps) · [Troubleshooting](#troubleshooting)

## For users

### On the create screen

Open **Settings** in the composer:

- **Edits** tab: **Edit intensity** is Off, Subtle, Bold or Max.
  - **Subtle**: a few zooms and soft sound effects, a quiet music bed.
  - **Bold**: punch-in zooms, whooshes and impacts, transitions where the clip cuts, a speed ramp or a freeze
    before a payoff, the "Wait for it" sticker.
  - **Max**: everything, more often: camera shake, glitches, emoji bursts, slow-motion tails, louder sound.
  - Once it's on, switches turn off what you don't want: sound effects, music (and its mood), zooms, camera
    shake, transitions, speed ramps and freezes, the swearing treatment (leave, bleep, bleep and cover the
    mouth with a bar, bleep and pixelate the mouth), the "Wait for it" sticker, a progress bar, a loop ending,
    and GIF reactions where the server offers them.
- **Captions** tab: an **Animated** group with the [ten styles](#animated-caption-styles). Each tile is the
  real renderer drawing over a demo scene. An animated style works with intensity Off too ("style only"): the
  captions are animated and nothing else changes.

The create screen remembers the choice on your account. If dynamic edits aren't available to you (flag off,
older backend), the request drops `edit_style` and swaps an animated caption style for its closest classic one.

### On the results page

The task's settings sheet has the same intensity and switches. Applying them to existing clips re-renders them
in the background: the clips show "Rendering edits…" until they're done. Trims, splits, merges and caption edits
of clips with dynamic edits also re-render in the background. A clip may show:

- "This video had no word timing, so captions and effects follow approximate timing." (`fx_meta.timing != "word"`)
- "Some effects were simplified for this clip." (`fx_meta.fx_level != "full"`, see [Fallbacks](#fallbacks))

### In the clip studio

The **Edits** tab in the inspector changes the clip's intensity and switches, lists the beats (click to seek,
delete one, change its kind), and edits the selected effect. "Plan edits" asks the AI for beats when a clip has
none; "Re-plan with AI" asks again. "Remove all edits" clears everything. **Grade** and **grain** live in the
**Effects** tab. The timeline gains an **FX lane** (drag to move, double-click to edit, Delete to remove,
double-click an empty spot to add a sound) and a **Music lane** showing how the bed ducks under the voice. Every
change is one undoable step. See [editor.md](editor.md#dynamic-edits).

### Through the API and MCP

`POST /tasks/` takes `edit_style` ([API reference](api-reference.md#dynamic-edits)). The MCP tool
`supoclip_create_clip_task` takes `intensity` and an `edit_style` passthrough; `supoclip_list_edit_styles` lists
the choices, and `supoclip_list_caption_templates` includes the animated styles when they are enabled.

## Animated caption styles

Template ids live in `FX_CAPTION_TEMPLATES_DICT` (`backend/src/caption_templates.py`), separate from the seven
classic templates. Each names the classic template whose libass captions replace it when a clip renders without
the drawing stage (`ass_fallback`).

| Id | What it does | Classic fallback | Needs |
|---|---|---|---|
| `marker_circle` | Clean captions; key words get a hand-drawn circle, underline, strike or an arrow to a number, drawn in and "boiling" at 8 fps. | `default` | – |
| `emoji_drop` | Karaoke captions; an emoji springs up under punchlines and pops away; bursts of emoji at Bold and Max. | `tiktok` | – |
| `slam` | The keyword alone, 60 % of the frame wide, slams in (1.6 → 0.95 → 1.0 in 180 ms) and hides the captions while it holds. | `mrbeast` | – |
| `behind_head` | One or two giant words sit behind the speaker: the person is cut out (matte) and drawn over the text. Without a person in frame the text goes in front. | `minimal` | face, matte |
| `stat_counter` | A spoken number counts up from 0 with tabular digits, keeps its prefix and suffix ($, %, M), and stamps into place. | `hormozi` | – |
| `sticker_slap` | Die-cut sticker words, white border, slight tilt, slapped around the frame away from the face; up to three stacked. | `tiktok` | – |
| `kinetic_stack` | The hook stacks up the screen in rows of mixed sizes, alternating white and accent; replaces the hook title. | `mrbeast` | – |
| `decode_glitch` | Every line scrambles and locks letter by letter with scanlines and an RGB split; key words do it bigger. | `neon` | – |
| `highlighter_swipe` | Dark text on a light card; a rough highlighter swipes across key words at the speed they're spoken. | `minimal` | – |
| `face_doodles` | Karaoke captions; sparkles, arrows, horns, a heart or an anger mark drawn around the speaker's head. Without a face at that moment, a sticker instead. | `default` | face |

Sizes are in units of `min(W, H) / 1080`, so styles survive the 720p cap. Non-Latin text falls back to the system
Noto fonts on both renderers.

## Effects

What the resolver can place (cue kinds in `backend/src/edit_fx/contracts.py`, drawers in
`frontend/src/lib/fx/core/`):

- **Accents**: the caption style's own move on a word (circle, emoji, slam, counter, sticker, highlight, …).
- **Zooms**: `punch` (a hard cut in to 112–125 % and back), `push`/`pull` (a slow ease over a sentence). Anchored
  on the face when one is tracked. They replace the static Ken Burns push, so there is only ever one zoom source.
- **Camera shake** on the hardest hits, only together with an impact sound or a slam.
- **Flash**, **glitch** (slice displacement + RGB split) and **desaturate** hits.
- **Transitions** at internal cuts only: whip, flash, glitch, zoom-through, spin, light leak. Visual only, so the
  duration doesn't change.
- **Time warp**: speech ramps (1.15–1.35×, pitch kept) over a build-up, wordless speed-ups, slow-motion tails
  after a punchline, and freeze frames (0.6–1 s) at the end of a payoff with a label sticker. Captions follow the
  warp. Needs `FX_WARP=on`.
- **Sound effects**: whoosh, impact, pop, riser, record scratch, boom, cash register, ticker, slap, stamp, glitch,
  tape stop. Styles bring their own (a slam gets an impact, a counter ticks and stamps).
- **Bleeps**: a 1 kHz tone over every swear, optionally with a black bar or pixelation over the mouth (from face
  landmarks). Bleeps ignore budgets. No bleeps with approximate timing, and none with intensity Off.
- **Music bed**: one of six moods (hype, chill, dramatic, funny, inspiring, dark; Auto follows the beat model),
  looped and ducked under the voice.
- **Grades**: punchy, warm, teal & orange, noir, VHS, dream; Auto picks punchy at Bold, and teal & orange at Max
  for dramatic or dark clips. **Grain** is optional.
- **Retention**: a progress bar along the bottom, a loop ending (the last 0.4 s crossfades into the first frame,
  music seam included), and a "Wait for it…" sticker before a payoff.
- **Reactions**: an optional GIF picture-in-picture on the funniest moments, with "via GIPHY" attribution. Off
  unless the server has `REACTIONS_PROVIDER=giphy` and a key, and the user switches it on.

**Serious mode**: when the beat model marks a clip as serious (grief, illness, violence, tragedy), it gets Subtle
budgets whatever the intensity, no comedic sounds, emoji, doodles, reactions, freezes or speed, and an
"inspiring" bed. Bleeps stay.

## Intensity and budgets

Caps per 60 s of edited duration (`cap = max(1, round(rate × duration / 60))` from 6 s), with the minimum gap in
seconds. "Style only" is an animated caption style with intensity Off. The source of truth is `BUDGETS` in
`backend/src/edit_fx/resolver.py`.

| Family | Style only | Subtle | Bold | Max |
|---|---|---|---|---|
| Accents | 10 / 3.0 s | 10 / 3.0 s | 20 / 1.5 s | 40 / 0.8 s |
| Zoom punch | – | 4 / 6.0 s | 8 / 3.0 s | 14 / 1.6 s |
| Zoom push | – | 1 per clip | 2 per clip | 4 per clip |
| Shake | – | – | 3 / 8.0 s | 8 / 3.0 s |
| Sound effects (not bleeps) | style sounds, 10 / 1.0 s | 6 / 2.5 s | 12 / 1.2 s | 24 / 0.6 s |
| Transitions at cuts | – | 1 per clip, passage breaks only | 1 per 8 s | 1 per 4 s |
| Flash / glitch hits | style only | style only | 2 per clip | 6 / 4.0 s |
| Freezes | – | – | 1 (clip ≥ 15 s) | 2 |
| Speech ramps / speed-ups / slow-mo | – | – | 1 / – / – | 2 / 2 / 1 |
| Emoji bursts | – | – | 3 per clip | 5 per clip |
| Reactions | – | – | 1 | 2 |
| "Wait for it" | – | – | 1 | 1 |
| Music bed / duck | – | −24 / −10 dB | −20 / −12 dB | −17 / −13 dB |
| SFX bus gain | 0 dB | −4 dB | 0 dB | +2 dB |
| Grain (when on) | – | 0.15 | 0.20 | 0.30 |

Global rules (in order): serious mode; bleeps; quiet zones (nothing in the first 0.25 s except the hook, nothing
in the last 0.3 s unless the loop ending is on); no zoom, shake, transition or face effects inside B-roll or
reaction windows; full-frame hits at least 0.8 s apart; nothing in the top 20 % while the hook title shows; a
total freeze hold of at most min(1.6 s, 4 % of the clip) and a speed change of at most 10 % of it. Effects you add
or delete in the studio survive a re-resolve.

## Configuration

`backend/src/edit_fx/env.py` is the only reader. Unknown values fall back to the default. Set them on the API, the
video worker, the AI worker and every [remote render worker](render-worker.md#dynamic-edits-renderer-deno);
`docker-compose.yml` passes them through the `&worker-environment` anchor.

| Variable | Default | Purpose |
|---|---|---|
| `EDIT_PLAN_LLM` | `LLM` | Model for beat tagging (`provider:model`) |
| `EDIT_PLAN_TIMEOUT_SECONDS` | `45` | One beat-tagging call; past it the clip uses heuristic beats |
| `EDIT_PLAN_DAILY_LIMIT` | `100` | Studio "Plan edits" requests per user per day (refunded on failure) |
| `FX_RUNTIME` | `deno` | `off` is the kill switch: no drawing stage, clips render at level safe |
| `FX_DENO_BIN` | `deno` | The deno binary |
| `FX_WARP` | `on` | `off` disables speed ramps and freezes everywhere |
| `FX_RENDER_TIMEOUT_SECONDS` | `max(900, 6 × clip seconds)` | Wall-clock limit for one FX render |
| `FX_LIBRARY_DIR` | `/app/fx_library` | Self-hoster drop-in sounds and music |
| `FX_LOUDNORM` | `static` | `static` (measured voice gain) or `dynamic` (the old one-pass loudnorm) |
| `FX_CHAIN_MODE` | `pipe` | `file` makes the drawing stage write a file instead of piping, if pipes prove flaky |
| `REACTIONS_PROVIDER` | `off` | `giphy` enables GIF reactions (hosted stays `off` until legal review) |
| `GIPHY_API_KEY` | empty | Needed with `REACTIONS_PROVIDER=giphy` |

## Architecture

Clocks: **S** is source-clip time (words, beats, stored cues), **E** is edit time after cuts, **F** is final output
time after the time warp. Generation is S == E.

1. **Beats** (`backend/src/edit_fx/beats_llm.py`): one pydantic-ai call per clip returns word ids, never
   timestamps: hook, emphasis, number, punchline, swear, emotional turn, payoff, build, with strength, tone, an
   emoji, a sticker label, the clip's mood and sensitivity. Code validates them (`beats.validate_llm_plan`), adds
   every listed swear (`edit_fx/swears.txt`, several languages), and caches them in Redis for 14 days and on the
   clip's sidecar. Beats don't depend on intensity, so changing intensity never calls the model again. Without a
   model, on timeout or error, `beats.heuristic_beats` runs and PostHog gets `edit_beats_fallback {reason}`.
2. **Resolver** (`edit_fx/resolver.py`): pure and deterministic (seeded by task and clip). Beats + intensity +
   style → a typed `FxPlan` of cues, with the budgets above. Reads only the committed asset manifest.
3. **Renderer** (`frontend/src/lib/fx/core`): one TypeScript library draws every cue on a canvas. The studio
   preview and browser export use it directly; the worker runs the same code as an esbuild bundle
   (`backend/src/fx_runtime/fx-core.js`, regenerate with `cd frontend && pnpm run build:fx-core`; CI checks it for
   drift) under deno with `@napi-rs/canvas`.
4. **The render chain** (`backend/src/media/fx_chain.py`), three processes joined by pipes:
   - **P1** ffmpeg: the framing head and B-roll (and a reaction PiP and matte) → raw RGBA frames.
   - **P2** deno (`src/fx_runtime/main.ts`): draws each output frame through the warp.
   - **P3** ffmpeg: the ASS layer (hook title, classic captions), the audio mix (`media/fx_audio.py`: voice
     through the warp, bleeps, SFX and music bus, ducking, static loudness) and the encode.
   - Face tracking (YuNet with landmarks) and the person matte (PP-HumanSeg) run before P1 only when the plan
     needs them (`media/fx_analysis.py`), from one decode at 6 fps.
   - The 720p cap still runs last.
5. **Storage**: `tasks.edit_style` holds the choice; `generated_clips.fx_meta`
   (`{fx_level, timing, intensity, template}`) marks FX clips. The clip's `.source_map.json` sidecar holds
   `edit_style`, `edit_beats`, `edit_fx` (the plan, S time), `face_track`, `fx_level`, `edit_fx_warp` (the warp
   actually rendered) and `vertical_layout`. Re-renders reuse the plan when nothing changed, re-resolve on a
   style change, and re-anchor beats after a cleanup change without calling the model.
6. **Jobs**: generation renders FX clips in the video worker. Settings re-renders and FX clip edits run as
   `fx_clip_job` (the API answers 202 and the client polls `GET /tasks/{id}/clip-jobs/{job_id}`). The studio's
   "Plan edits" runs `edit_fx_plan_job` on the AI worker, its face/matte analysis `editor_fx_analysis` on the
   video worker, and its server export renders the document's `fx` through the same chain.

### The deno and napi canvas requirement

The worker image pins deno 2.9.8 (`ARG DENO_VERSION` in `backend/Dockerfile`) and bakes the `@napi-rs/canvas`
0.1.80 cache into `DENO_DIR` at build time; renders run `--cached-only --frozen` with narrow permissions and need
no network. A source checkout runs `cd backend/src/fx_runtime && deno install --frozen` once. Check a machine
with `cd backend/src/fx_runtime && deno task selftest` (one JSON line ending in `"ok":true`). Details:
[render-worker.md](render-worker.md#dynamic-edits-renderer-deno).

### Observability

- PostHog `fx_render {intensity, template, fx_level, cues, warp, duration_ms, p1_ms, p2_ms, p3_ms, analysis_ms,
  library_fingerprint}` for every FX render.
- PostHog `fx_render_failed {stage, level, error, template, intensity}` on each downgrade; `stage` is one of
  `p1`, `p2`, `p3`, `q`, `output`, `timeout`, `setup`, `analysis`, `av_check`.
- PostHog `edit_beats_fallback {reason}` when heuristic beats replace the model. No transcript text is sent.
- Logs (Axiom) carry `fx_render_failed level=… error=…` (generation) and `fx_render_failed surface=studio
  stage=…` (studio export), plus warnings that degrade one part of a render: `fx_bus_failed` (no SFX or music),
  `fx_analysis_failed` (no face or matte), `fx_atlas_unavailable` (no emoji). Render timings are in the
  `fx_render` event.

## Assets and self-hoster drop-ins

Built-in assets are committed in `backend/src/fx_assets/` and generated by scripts in `backend/scripts/fx/`, so
nothing is downloaded and everything is licence-clean:

- `sfx/`: synthesised sound effects (`synth_sfx.py`, CC0), up to 2.5 s each.
- `music/`: six synthesised, seamlessly looping beds, one per mood (`synth_music.py`, CC0).
- `emoji/`: an atlas of Noto Color Emoji glyphs (`build_emoji_atlas.ts`, OFL-1.1).
- `manifest.json`: hashes, durations and loudness (`write_manifest.py`).

`GET /fx/assets` serves the manifest with content-addressed URLs so the studio preview plays the same files.

To use your own sounds or music, drop files into `backend/fx_library/` (mounted at `FX_LIBRARY_DIR`), like fonts:

- `sfx/<sound>[-<name>].{wav,mp3,m4a,ogg}`, where `<sound>` is one of `whoosh impact pop riser record_scratch boom
  cash_register ticker slap stamp glitch tape_stop`.
- `music/<mood>-<name>.{m4a,mp3,wav}`, where `<mood>` is one of `hype chill dramatic funny inspiring dark`.

Drop-ins never change the plan; they replace the built-in file for that sound or mood at mix time and are
gain-normalised. **Every render machine must mount the same library**, or previews and renders differ; each
render logs a `library_fingerprint`. Only add files you have the rights to publish.

## Performance

Budget (60 s clip at 1080×1920): stage-2 wall time with effects, **including face/matte analysis**, at most
**2.0×** the classic render; over **3.0×** blocks the release. Intensity off with a classic template runs no new code
(+0). The deno compositor's target is a mean of 35 ms per frame or less (p95 80 ms or less).

`backend/scripts/fx/bench_fx.py` renders a synthetic 60 s 1080×1920 clip (152 transcript words) through
`VideoService.create_single_clip` for each mode and reads the chain's own timings (`FxResult.timings`, the same
numbers the worker logs as `fx_chain … timings=`). Beats come from the heuristic, so the beat model's time (≤6 s,
concurrent with B-roll planning) is not included. The process is pinned to 4 cores, the per-chain budget.

```bash
cd backend && .venv/bin/python scripts/fx/bench_fx.py --runs 2 --json bench.json   # ffmpeg + deno 2.9.8 on PATH
```

Measured 2026-11-14 on the development laptop (x86_64, 32 cores, pinned to 4; ffmpeg n9.0.2, deno 2.9.8), median
of 2 runs:

| Mode | Template | Cues | Stage 2 (s) | × classic | FX chain (s) | Analysis (s) | P1‖P2‖P3 (s) | P2 draw (s) | P2 ms/frame |
|---|---|---|---|---|---|---|---|---|---|
| Classic (off) | default | – | 44.0 | 1.00 | – | – | – | – | – |
| Style only | marker_circle | 9 | 62.2 | 1.41 | 45.4 | 4.6 | 40.6 | 30.2 | 16.8 |
| Subtle | slam | 17 | 60.5 | 1.37 | 44.9 | 4.6 | 40.0 | 29.1 | 16.2 |
| Bold | slam | 44 | 77.4 | 1.76 | 61.5 | 4.6 | 56.6 | 45.5 | 25.3 |
| Max | slam | 74 | 70.0 | 1.59 | 55.2 | 4.5 | 50.4 | 40.5 | 22.5 |
| Bold, behind head | behind_head | 41 | 69.3 | 1.57 | 54.1 | 4.7 | 49.1 | 40.8 | 22.7 |

P1, P2 and P3 run as one pipe, so their times overlap; "P2 draw" is the compositor's own drawing time, which
dominates the pipeline. The audio bus and voice loudness pass take 0.1–0.2 s together. Every mode is within the
2.0× budget and none is near the 3.0× blocker. The compositor averages 16–25 ms per frame, inside the 35 ms target.

Caveats:
- The laptop was busy during the run: a Docker image build, a sample-render job and a pytest run shared the
  machine (load average 6–15). The first run was 15–35 % slower than the second (bold 93.8 s against 61.1 s,
  classic 47.1 s against 40.9 s); the second, quieter run gives 1.22–1.55×.
- The source is a lavfi test pattern, so there is no real face: analysis decodes and runs detection but finds
  nothing, and behind-head text has no matte windows. With a real speaker, analysis also builds the matte (budget
  ≤3 s).
- **Still owed for the rollout perf gate, before the flag opens beyond admins:** the same bench on sora (render worker) and on the
  prod host, a 10-clip FX settings re-render job, and the arm64 napi canvas load.

## Rollout

The migration `backend/src/migrations/sql/20261114_0001_dynamic_edits.sql` adds `tasks.edit_style`,
`generated_clips.fx_meta` and seeds the `dynamic_edits` flag off on web and iOS with admins bypassing.

1. Deploy order: migration → video workers, render workers and the AI worker (new image) → API → frontend.
   Roll back the other way, flag first.
2. Dark: admins only. Watch `fx_render`, `fx_render_failed`, `edit_beats_fallback`. If more than 1 % of renders
   fall back, set `FX_CHAIN_MODE=file`.
3. Open on web in `/admin` → Feature Flags (`web_enabled`). Set the tours' `releasedAt` in
   `frontend/src/lib/feature-tours.ts` to that day.
4. iOS: `ios_enabled` once an iOS build sends `edit_style`. Server-rendered clips already carry the effects for
   every client.

With the flag off, a request that turns effects on (an active intensity, or an animated template) returns 403
`DYNAMIC_EDITS_NOT_AVAILABLE` before any charge. Clips that already have effects keep working: re-renders, font
changes and studio exports. Feature tours: `dynamic-edits` (create screen) and `dynamic-edits-studio`, both
`requiresFlag: "dynamic_edits"`; the announcement loop is rendered by
`backend/scripts/fx/render_tour_media.py` through the real chain.

## Release checklist

Tick these in order before opening the flag on web (Phase 2). Items marked *done on the branch* were checked
when the feature branch was finished; repeat them if the code changes.

**Before merging**

- [ ] Full backend suite with PostgreSQL and Redis, `FX_CHAIN_REAL_DEPS=1 pytest tests/unit/fx_media` with
      deno 2.9.8 on the path, and the drift checks `scripts/fx/gen_fixtures.py --check`,
      `gen_resolver_goldens.py --check` and `gen_scene_fixtures.py --check` (*done on the branch*).
- [ ] Frontend `pnpm run lint`, `pnpm exec tsc --noEmit`, `FX_REQUIRE_FIXTURES=1 pnpm run test`, and the
      Playwright polish config including `e2e/editor-effects.spec.ts` and `e2e/fx-parity.spec.ts`
      (*done on the branch*).
- [ ] `pnpm run build:fx-core` leaves `backend/src/fx_runtime/fx-core.js` unchanged (`git diff --exit-code`).
- [ ] `deno task test` and `deno task selftest` in `backend/src/fx_runtime`; MCP tests.
- [ ] Off stays legacy: `test_dynamic_edits_legacy_pins.py` and `test_dynamic_edits_off_is_legacy.py` green.
- [ ] The `fx-media-ffmpeg61` CI job green on GitHub (only emulated locally so far).

**Image and machines**

- [ ] The Docker image builds with deno 2.9.8 and the baked npm cache; `docker run --rm --network none <image>
      .venv/bin/python -m src.media.fx_selftest` passes on amd64 (and arm64 if a render host uses it).
- [ ] Bench on sora and on the prod host: `backend/scripts/fx/bench_fx.py` (see Performance). Stage 2 with
      effects over 3× the classic render blocks the release; over 2× needs a decision.
- [ ] One real generation at each intensity (Subtle, Bold, Max) plus a style-only clip, watched and listened to
      by a person: sound effects, music beds, ducking and bleeps.
- [ ] A real clip's colours compared with its classic render (no shift, BT.709 tags kept).

**Rollout**

- [ ] Deploy in the order under Rollout: migration, workers (video, render on sora, AI), API, frontend.
- [ ] Phase 0 (dark, admins): `fx_render_failed` and `edit_beats_fallback` under 1 % of `fx_render` for a few
      days; Axiom `fx_chain` timings within budget.
- [ ] Phase 1: studio and clip edits with effects, admins only. Clip jobs finish (no `WORKER_STOPPED` or
      `WORKER_UNAVAILABLE` in normal operation).
- [ ] Phase 2: `web_enabled` on in `/admin`, the tours' `releasedAt` set to that day, the CHANGELOG **New**
      entry. Reactions stay self-host only until the GIPHY legal review.
- [ ] iOS: `ios_enabled` only once an iOS build that sends `edit_style` is approved.
- [ ] Rollback drill known: flag off first, then the API, then workers; `FX_RUNTIME=off` is the kill switch
      (clips render at level safe).

## Fallbacks

Each FX render steps down a ladder and records `fx_level` (`create_optimized_clip`, `editor_fx_render`):

1. **full**: the three-process chain. If P1 failed with B-roll, retry once without B-roll. If the audio and video
   lengths disagree with a warp, retry without the warp.
2. **safe**: no drawing stage, no warp, no picture effects; animated captions are burned with their classic
   fallback, and sound effects, music and bleeps stay. Also what `FX_RUNTIME=off` gives.
3. **none**: the classic render, exactly as before.

A planning error renders the clip without effects rather than losing it. Transcripts without word timing get
`timing="approx"` (no bleeps, fixed highlighter speed) or `"none"` (only music, grade, grain, progress bar, loop
ending and transitions).

## Known gaps

- Safari and Firefox measure text slightly differently from Skia, so the studio preview is approximate there.
- The studio's browser export refuses documents with a time warp, reactions or behind-the-head accents; use the
  background export.
- The browser export has no loudness stage or 720p cap.
- Settings re-renders and FX clip edits are asynchronous (202 and poll), where classic ones answer directly.
- Combined studio clips need "Analyse face" again before face-anchored effects are exact.
- Bleeps for non-English swearing are best effort (the word list plus the model). Behind-the-head edges are soft.
- Drop-in libraries are not mirrored through the bucket yet.

## Troubleshooting

| Symptom | Check |
|---|---|
| No Edits tab or Animated styles | `GET /tasks/edit-styles` → `enabled`. The flag must be on for the platform (or the user an admin with bypass) |
| 403 `DYNAMIC_EDITS_NOT_AVAILABLE` | The flag is off for this platform and the request turns effects on |
| Clips render but say "simplified" | `fx_render_failed` in PostHog: its `stage` says where. `p2`/`setup` usually means deno or the napi cache: run `deno task selftest` on that machine |
| Every render falls to safe | `FX_RUNTIME=off`, deno missing, or the image was built without the baked `DENO_DIR` cache |
| Renders time out | `FX_RENDER_TIMEOUT_SECONDS`, CPU contention (each chain is three processes; `max_jobs` bounds them) |
| Effects ignore the moments you'd expect | `edit_beats_fallback`: the beat model failed or has no key, so heuristic beats ran |
| No speed ramps or freezes | `FX_WARP=off`, Subtle intensity, the clip shorter than 15 s, or the speed switch off |
| No bleeps | Approximate timing, intensity Off, or censor set to "Leave as is" |
| Preview sounds different from the render | Drop-in libraries differ between machines: compare `library_fingerprint` in `fx_render` |
| Behind-the-head text in front of the speaker | No person found in that window (the matte model found too little), or a studio clip without "Analyse face" |
