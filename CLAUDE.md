# CLAUDE.md

Read this first. Full depth lives in `docs/` — this file is a map and a
scan-list, not the source of truth. If this file and the code disagree, the
code wins; fix this file in the same commit.

## Project Overview

SupoClip is an open-source (AGPL-3.0) OpusClip alternative: turns long-form
video into AI-selected, captioned, vertical short clips. Local-first (no
login/cloud required by default), self-hostable via Docker Compose, also
hosted at supoclip.com. A multi-tool platform: Clipping (one source video in,
many clips out) and Ranking (N input videos in, one ranked compilation video
out) are the two tools live today, sharing one tab-bar shell; a voiceover/
animation tool is the next planned addition. See
[docs/architecture.md](docs/architecture.md#multi-tool-platform--ranking-tool)
for how the shell and the ranking pipeline work.

## Tech Stack

- Backend: Python 3.11+, FastAPI 0.121+, `pydantic-ai` 1.89+, `asyncpg`, ARQ (Redis-backed worker queue), `uv` for deps
- Frontend: Next.js 15.4 (App Router, Turbopack), React 19.1, TypeScript 5.9, TailwindCSS v4, ShadCN/Radix, Prisma (Better Auth only), `pnpm`
- Data: PostgreSQL 15, Redis
- Media: ffmpeg + libass (rendering/subtitles), MediaPipe/OpenCV/Haar (face crop), AssemblyAI (transcription), Ollama (local LLM, primary) / Gemini (fallback) / OpenAI / Anthropic
- Tests: pytest (backend), Vitest (frontend unit), Playwright (e2e)

## Directory Structure

```
backend/src/api/routes/     FastAPI route handlers (tasks, media, admin, templates, metadata, billing)
backend/src/services/       Business logic (task orchestration, video pipeline, metadata, batch queue)
backend/src/repositories/   Raw-SQL (asyncpg) DB access, one class per table family
backend/src/workers/        ARQ job queue, task definitions, Redis progress pub/sub, GPU/LLM resource locks
backend/src/migrations/sql/ Hand-written SQL migrations, timestamp-prefixed, applied in filename order
backend/src/*.py            Domain modules — ai.py, video_utils.py, clip_editor.py, clip_cleanup.py, etc. (see Key Files)
backend/fonts/              .ttf files, auto-served via GET /fonts
backend/transitions/        .mp4 files, auto-served via GET /transitions
backend/templates/          Folder-based templates for non-clipping tools (e.g. templates/ranking/<name>/config.json)
backend/tests/              pytest suite
frontend/src/app/           Next.js routes; (clipping)/ is a route group for /create /list /tasks/[id] /trash
frontend/src/components/    React components; editor/, home/, ui/ (ShadCN primitives) subfolders
frontend/src/lib/           Client-side helpers — one file per concern, no barrel exports
frontend/src/tools/         Multi-tool shell: Tool type, registry.ts, per-tool descriptor folders
frontend/prisma/            Better Auth schema (only exercised when REQUIRE_AUTH=true)
frontend/e2e/               Playwright specs
docs/                       Deep-dive docs — architecture, api-reference, configuration, development, troubleshooting, setup, app-guide
mcp/                        Standalone MCP server (thin REST client), separate uv project
init.sql                    Full Postgres schema (source of truth for table shapes)
docker-compose.yml          Frontend/backend/worker/postgres/redis service definitions
```

## Key Files

| File | Why you'll touch it |
|---|---|
| `backend/src/main_refactored.py` | Active FastAPI entry point (never `main.py`, legacy) |
| `backend/src/api/routes/tasks.py` | Task CRUD, SSE progress, clip trim/split/merge/export endpoints |
| `backend/src/services/task_service.py` | Per-task orchestration: calls video/metadata/content-policy in sequence |
| `backend/src/services/video_service.py` | Download → transcribe → AI analysis → clip generation pipeline |
| `backend/src/ai.py` | LLM prompts (segment selection, hook generation), Ollama/Gemini fallback logic |
| `backend/src/video_utils.py` | ffmpeg cropping, subtitle/hook ASS generation, encode args, size cap |
| `backend/src/clip_cleanup.py` | Pause/filler-word cut-range logic (sensitivity slider) |
| `backend/src/clip_editor.py` | Trim/split/merge, `EXPORT_PRESETS` |
| `backend/src/config.py` | `Config` class — all env vars + runtime-setting overrides |
| `backend/src/runtime_settings.py` | Admin-configurable settings storage (DB-backed, env fallback) |
| `backend/src/repositories/task_repository.py` | Raw SQL for `tasks` table |
| `backend/src/repositories/clip_repository.py` | Raw SQL for `generated_clips`, metadata staleness check |
| `backend/src/workers/tasks.py` | ARQ job entry point (`process_video_task`) |
| `frontend/src/app/(clipping)/tasks/[id]/page.tsx` | Main project page: clip list, export, safe zones, metadata panel |
| `frontend/src/app/(clipping)/tasks/[id]/edit/page.tsx` | Fine-controls editor: trim/FX/captions live preview |
| `frontend/src/app/(clipping)/create/page.tsx` | New-task form |
| `frontend/src/app/settings/page.tsx` | User-facing settings (runtime settings form + local-only prefs) |
| `frontend/src/tools/registry.ts` | The list of tools shown in the tab bar |
| `init.sql` | Postgres schema — check here before assuming a column exists |

## How to Run Locally

```bash
docker-compose up -d --build      # everything (recommended)
docker-compose logs -f backend    # or worker / frontend
```

Backend only: `cd backend && uv sync && uvicorn src.main_refactored:app --reload --port 8000` + `arq src.workers.tasks.WorkerSettings` (worker required for processing).
Frontend only: `cd frontend && pnpm install && pnpm run dev` (port 3107).
Tests: `make test` (backend+frontend), `make test-backend`, `make test-frontend`, `make test-e2e` (needs `docker-compose up -d postgres redis`), `make test-ci` (all, as CI runs it).
Single backend test: `cd backend && .venv/bin/pytest tests/unit/test_x.py -k name`. Single frontend test: `cd frontend && pnpm exec vitest run path/to/file.test.ts`.

## Where Things Live

- Design system → [DESIGN.md](DESIGN.md)
- Roadmap → [future-plan.md](future-plan.md)
- Decisions log (don't re-litigate) → [DECISIONS.md](DECISIONS.md)
- Session history → [SESSION_LOG.md](SESSION_LOG.md)
- Dev-session bug cache → [TROUBLESHOOTING.md](TROUBLESHOOTING.md) (root) — distinct from [docs/troubleshooting.md](docs/troubleshooting.md), which is the user/operator runbook (services won't start, docker/env issues)
- Feature implementation gotchas (full detail) → [docs/development.md](docs/development.md#feature-implementation-notes-gotchas)
- Architecture / data flow / DB schema → [docs/architecture.md](docs/architecture.md)
- API endpoint list → [docs/api-reference.md](docs/api-reference.md)
- Config schema → `backend/src/config.py` (`Config` class); admin-editable subset → `backend/src/runtime_settings.py`; full env var list → [docs/configuration.md](docs/configuration.md)
- Ollama config → `backend/src/config.py` (`ollama_*` fields) + `backend/src/ollama_status.py` (`check_ollama_status`, live probe)
- Metadata cache → `generated_clips.metadata_*` columns (`init.sql`), read/written via `backend/src/repositories/clip_repository.py`
- Safe zones config → `frontend/src/lib/safe-zones.ts` (`PLATFORM_SAFE_ZONES`)
- Settings templates (save/apply a project's settings) → `backend/src/api/routes/templates.py`, `project_templates` table
- Caption style templates (font/animation presets) → `backend/src/caption_templates.py` (hardcoded dict, not folder-based)
- Ranking-tool templates → `backend/templates/ranking/<name>/config.json`, loaded by `backend/src/ranking_templates.py` (four ship today — `rapid_fire`, `countdown`, `ranking_list`, `ranking_classic` — see `docs/architecture.md`). `ranking_classic` is the folder-workflow default: `number_overlay.style: "stacked"` (always-visible 5..1 column, #1 gold, per-rank text reveal/persist, bounce) and `use_global_sfx: true` (Settings-configured transition SFX + offset instead of a template-named file) dispatch to different code paths in `ranking_overlay.py`/`ranking_service.py` than the three tile-based templates — see Common Pitfalls below.
- Ranking tool routes → `backend/src/api/routes/ranking.py`; render pipeline → `backend/src/services/ranking_service.py`; one project's N attached clips → `ranking_inputs` table (`backend/src/repositories/ranking_repository.py`)
- Ranking folder library (random/prefer-unused selection + cross-ranking text memory) → `ranking_folders`/`ranking_folder_clips` tables, `backend/src/repositories/ranking_folder_repository.py`. A "folder" is a user-named batch of clips picked via the browser (directory picker or multi-file drop) under `POST /ranking/folders/scan`, keyed by `(folder, content_hash)` so re-adding the same file resolves to the same row. **Text memory** lives in `ranking_folder_clips.saved_text` (mirrored into that ranking's own `ranking_inputs.rank_text` on save/render — the input's copy is what actually renders, the library's copy is what pre-fills next time). **Usage tracking** is `ranking_folder_clips.use_count`/`last_used_at`, incremented in `RankingService.process_ranking_complete` when a ranking using that clip renders. Frontend at `frontend/src/app/(rank)/rank/`.
- Ranking SFX/offset/default-framing settings → `RANKING_SFX_FILENAME`/`RANKING_SFX_OFFSET_PCT`/`RANKING_DEFAULT_FRAMING` in `backend/src/config.py` + `runtime_settings.py` (same admin-settings mechanism as everything else in Settings). The SFX file itself uploads via `POST /ranking/settings/sfx` into `backend/sfx/` (video_utils.py's `SFX_DIR`, same directory clipping's curated SFX ship from) under the reserved name `ranking_default.<ext>` — only the filename is a setting, not the bytes.
- Cut logic (pause/filler removal) → `backend/src/clip_cleanup.py` (settings/thresholds) + `video_utils.py::build_clip_keep_ranges` (applies cuts)
- Caption rendering → `video_utils.py::build_assemblyai_ass_subtitles` (captions), `build_hook_title_ass` (hooks)
- Hook generation prompt → `backend/src/ai.py::HOOK_GENERATION_RULES`

## Common Pitfalls

One line each — full rationale in [docs/development.md](docs/development.md#feature-implementation-notes-gotchas).

- Font/hook sizing scales off the *shorter* frame dimension, not width — width-only scaling balloons captions on 16:9/1:1 outputs.
- `max_clips`/`target_duration_seconds` are per-request overrides threaded through 5 layers — `/resume` must forward them from saved metadata or silently drops them.
- SSE initial status reads the cached Redis snapshot, not the DB row — the DB never persists `stage`. On error/cancel, progress freezes at last real % (never resets to 0).
- Clip-cleanup pause threshold floor is 600ms, not 300ms — 300ms fell inside normal speech gaps and cut continuous speech. Cuts below 1.2s also require a sentence-boundary word before them.
- Task delete is soft (`deleted_at`) — every task query must filter `deleted_at IS NULL`, or trashed tasks leak back into lists.
- `enforce_size_cap()` and `build_audio_output_args()` are the *only* places that should re-encode-for-size or build `loudnorm` args — hand-rolling either at a new call site breaks the 300MB cap or loudness normalization silently.
- `EXPORT_PRESETS` dict order is display order and `preset=` values are persisted externally — never reorder or rename existing entries.
- ffmpeg/libass here cannot render color emoji glyphs at all (verified, not a font problem) — emoji reactions are PNG image overlays, not ASS text.
- `HOOK_GENERATION_RULES` in `ai.py` is the single source for hook-writing rules, shared by both hook call sites — don't duplicate it.
- `GET /tasks/` and `/trash` clamp `limit` to [1,500]; frontend requests `?limit=500` explicitly for "select all" to see everything.
- `detect_gpu_encoder()` always re-verifies with a real NVENC encode attempt — never trust the saved setting or ffmpeg's compiled-encoder list.
- Metadata generates once per video (not per clip) in one LLM call, auto-triggered at 98% progress, gated by `AUTO_GENERATE_METADATA_ENABLED`. User-edited metadata fields are never silently overwritten by passive regeneration (tracked via `*_user_edited` booleans) — only the explicit "Regenerate" button overwrites.
- Ollama calls and video renders share one Redis-backed `"gpu"` semaphore (`workers/resource_locks.py`) — a new render or LLM call site must acquire it too, or VRAM contention returns.
- Batch queue state lives in Postgres (`batch_queues`/`batch_queue_items`), not browser memory — that's what makes resume-after-restart possible.
- Safe Zone Overlay is frontend-only (SVG, `pointer-events-none`) — it never reaches ffmpeg or the export. Not appearing in an exported file is correct behavior, not a bug.
- Most pages still use literal Tailwind colors (`stone-*`), not the semantic theme tokens — dark mode only fully works on ShadCN primitives and page shells.
- ASS `BorderStyle=3` (opaque box) fills using **OutlineColour**, not `BackColour`, on this project's libass build (verified directly, same "probe don't assume" caveat as the color-emoji pitfall above) — `ranking_overlay.py`'s rank-number tile sets `OutlineColour` to the tile fill color for this reason; don't "fix" it back to `BackColour`.
- An ffmpeg filtergraph label produced by another filter (e.g. `[araw]` from `concat`) can only feed *one* downstream filter, unlike a raw `[N:a]`/`[N:v]` input pad — reusing it twice (ranking's background-music ducking needs the dialogue track as both the sidechain key and the final mix input) errors "Invalid stream specifier"/"matches no streams"; `asplit`/`split` it into separate labels first. Verified by actually running the graph, not assumed.
- Ranking-tool `transition_sfx` mixes into the *main* render's `filter_complex` (extra `-i` per boundary + `adelay`/`amix`), not via `video_utils.py`'s `mix_sfx_into_clip` post-pass — that helper re-encodes the whole file per call, which would mean N-1 re-encodes for N-1 cut boundaries instead of one combined pass.
- A ranking template's `render_order: "descending"` (Countdown, Ranking List) only flips *playback* order — `RankingService._resolve_ranks` computes each item's displayed digit off the ascending (#1-first) order before that reversal, so #1 always displays as "1" regardless of when it plays. Reversing before `_resolve_ranks` instead would make the digits themselves count backwards.
- `loudnorm` (or any `-af`) can't be combined with `-filter_complex` on the same output stream — ffmpeg errors "Simple and complex filtering cannot be used together". Any ffmpeg command that already uses `-filter_complex` (e.g. `ranking_service.py`'s multi-input concat) must add loudnorm as a filter-graph step (`[in]loudnorm=...[out]`), not as a `-af` flag.
- Ranking videos are explicitly exempt from the site's locked 4-color palette (DESIGN.md) — `ranking_overlay.py::build_ranking_overlay_ass`'s gold `#FFD700` for the #1 rank is the one deliberate departure, used only in rendered video output, never in site UI.
- `ranking_service.py::_render_compilation`'s `use_global_sfx` flag (only `ranking_classic` sets it) changes SFX behavior in two ways at once: the offset-before-cut comes from `Config.ranking_sfx_offset_pct` instead of firing exactly on the boundary, and one *extra* SFX instance is appended whose start is timed so its tail lands exactly at video end (`total_duration - sfx_duration`) — the three tile-based templates' own `transition_sfx` keeps the original boundary-exact, no-extra-instance behavior unchanged so their existing tests/behavior don't shift.
- Per-clip framing (`blur_fill`/`crop_fill`/`letterbox`, `ranking_inputs.framing`) is resolved and applied uniformly for every ranking template via `RankingService._framing_filter`, not just `ranking_classic` — `blur_fill` (the tool-wide default) preserves the whole source frame with a blurred, scaled-to-fill copy behind it, since ranking inputs are often chaotically-framed footage where a center-crop would cut the subject out.
- A multi-file Next.js dev server edit across several new route files can leave Turbopack's HMR in a broken state (every route 500s, no logged stack trace) — a plain `restart` isn't enough since `.next`'s build cache survives it; use `docker compose up -d --force-recreate frontend` to get a clean container.
- Route groups (`(clipping)/`, `(rank)/`) don't contribute a URL segment — two groups both containing e.g. `create/page.tsx` collide on `/create`. A second tool's pages need their own segment inside its group (e.g. `(rank)/rank/create/page.tsx` for `/rank/create`), not just a same-named file one level down.

## Coding Conventions

- Backend: Python 3.11+, 4-space indent, type hints, `snake_case`, all DB access via repository classes with raw SQL (no ORM), blocking work wrapped in `run_in_thread()`.
- Frontend: TypeScript/React, 2-space indent, `PascalCase` components, `camelCase` vars, `@/*` imports, no global state library (hooks only), `toast` from `@/lib/toast` (never `"sonner"` directly).
- No comments explaining *what* code does — only non-obvious *why*.
- Don't add abstractions, fallbacks, or config flags for hypothetical future needs.

## Constraints

- Local-first: no login/cloud required by default (`REQUIRE_AUTH=false`); don't add features that assume a hosted backend.
- Minimal deps: prefer stdlib/already-installed packages; justify any new dependency.
- Backwards compat: DB migrations are additive; old task/clip rows without a new column must still work.
- 4-color palette locked (ink/paper/teal/blue) for all core-product screens — see DESIGN.md. No new colors, gradients, shadows, or opacity tricks (one sanctioned scrim exception).
- Ollama-first for LLM features, Gemini/cloud is opt-in fallback only — never the reverse default.

## How to Add X

- **New tool (tab):** create `frontend/src/tools/<tool>/index.ts(x)` exporting a `Tool`; add it to `frontend/src/tools/registry.ts`; if route-based, add pages under a new route group in `frontend/src/app/`. See `docs/architecture.md`.
- **New ranking template:** if it fits `layout: "full_screen"` (the only layout `RankingService` renders) and reuses an existing `number_overlay.style` (`"tile"` or `"stacked"`), add a folder under `backend/templates/ranking/<name>/` with `config.json` (+ optional `preview.svg`) — no code change, `render_order`/`list_overlay`/`number_overlay`/`use_global_sfx` are already dispatched on. A new `layout` value (e.g. a future Head-to-Head/Tier List) or a genuinely new `number_overlay.style` needs a `RankingService`/`ranking_overlay.py` code change, it isn't data-driven.
- **New setting:** add the field to `Config` in `backend/src/config.py`, add its metadata to `SETTING_METADATA` in `backend/src/api/routes/admin.py`, always expose `current_value` (never hide a non-secret behind "configured"/"unset").
- **New export preset:** append (don't reorder) to `EXPORT_PRESETS` in `backend/src/clip_editor.py`, including `max_duration_seconds`/`safe_area_*_pct`/`target_lufs`.
- **Update safe zones:** edit `PLATFORM_SAFE_ZONES` in `frontend/src/lib/safe-zones.ts` — nothing else needs to change.

## Environment Variables

Full list: [docs/configuration.md](docs/configuration.md). Core: `ASSEMBLY_AI_API_KEY`, `LLM` (`provider:model`), `GOOGLE_API_KEY`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, `REQUIRE_AUTH`, `PEXELS_API_KEY`, `REDIS_HOST`/`PORT`, `DATABASE_URL`, `TEMP_DIR`.

## Other Subsystems

- iOS app → [docs/architecture.md#ios-app](docs/architecture.md#ios-app)
- MCP server (`mcp/`) → [docs/architecture.md#mcp-server](docs/architecture.md#mcp-server)
