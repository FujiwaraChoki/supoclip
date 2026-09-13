# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SupoClip is an open-source alternative to OpusClip — an AI-powered video clipping tool that transforms long-form content into viral short clips. AGPL-3.0 licensed. Hosted at supoclip.com; self-hostable via Docker Compose.

A `docs/` directory is the canonical deep-dive documentation (start at [docs/README.md](docs/README.md)): [architecture.md](docs/architecture.md), [configuration.md](docs/configuration.md), [api-reference.md](docs/api-reference.md), [app-guide.md](docs/app-guide.md), [development.md](docs/development.md), [troubleshooting.md](docs/troubleshooting.md). Prefer those for detail beyond what's below.

**[DESIGN.md](DESIGN.md) is required reading before touching any UI.** It defines the Swiss/International-Typographic-Style design system (locked 4-color palette, type scale, spacing/grid, and per-component rules) that every core-product screen (Home, Clipping tool, Settings) must follow. Frontend marketing/legal pages (`blog`, `terms`, `privacy`, `share`, the landing `[slug]` page) are exempt — they're slated for removal and intentionally out of scope for the design system.

## Development Commands

### Docker (recommended)

```bash
docker-compose up -d --build      # Start/rebuild all services
docker-compose logs -f backend    # Debug backend
docker-compose logs -f worker     # Debug video processing
docker-compose down               # Stop all services
```

Services: Frontend (:3107 locally / :3001 on the host in Docker, container port 3107), Backend API (:8000, docs at `/docs`), Worker (ARQ), PostgreSQL (:5432), Redis (:6379).

### Backend (local)

Uses `uv` (not pip/poetry). Requires Python 3.11+, ffmpeg, running PostgreSQL and Redis.

```bash
cd backend
uv venv .venv && source .venv/bin/activate
uv sync

uvicorn src.main_refactored:app --reload --host 0.0.0.0 --port 8000  # API
arq src.workers.tasks.WorkerSettings                                  # Worker (required for video processing)
```

### Frontend (local)

Package manager is **pnpm** (pinned via `packageManager` in `package.json`), not npm.

```bash
cd frontend
pnpm install
pnpm run dev          # Dev server with Turbopack, port 3107
pnpm run build        # Prisma generate + Next.js build
pnpm run lint
```

### Tests

There is a real three-layer test suite (backend pytest, frontend Vitest, Playwright e2e), run via `make` from the repo root or directly per app. Postgres and Redis must be running for backend/e2e tests (`docker-compose up -d postgres redis` is enough).

```bash
make test          # backend + frontend
make test-backend  # cd backend && uv sync --all-groups && .venv/bin/pytest
make test-frontend # cd frontend && npm install && npm run test:coverage (Makefile uses npm, not pnpm)
make test-e2e      # Playwright smoke tests against real frontend+backend
make test-ci       # everything, as CI runs it
```

Run a single backend test: `cd backend && .venv/bin/pytest tests/unit/test_ai_prompt.py -k some_test`.
Run a single frontend test: `cd frontend && pnpm exec vitest run path/to/file.test.ts`.

CI (`.github/workflows/tests.yml`) runs `backend`, `frontend`, and `e2e` as separate jobs against Postgres/Redis service containers.

## Architecture

### System Overview

```
User → Frontend (Next.js 15) → Backend API (FastAPI) → Redis Queue → ARQ Worker
                                      ↓                                  ↓
                               PostgreSQL ←───────────────────────────────┘
```

Task creation returns immediately (<100ms). Video processing happens asynchronously in the worker. Frontend connects via SSE for real-time progress updates.

### Local-first auth model

By default the app runs with **no login**: both frontend and backend resolve every request to a single implicit user (`LOCAL_USER_ID = "local"`), controlled by the `REQUIRE_AUTH` env var (must be set identically on both sides — see `backend/src/auth_headers.py` and `frontend/src/lib/local-user.ts` / `frontend/src/server/session.ts`). Set `REQUIRE_AUTH=true` to restore real multi-tenant Better Auth session checks (used for the hosted deployment). There are currently no `/sign-in`, `/sign-up`, or admin dashboard pages in the frontend — those are hosted-mode/legacy concerns; check `git log`/`docs/` before assuming they exist.

Frontend-to-backend requests (when auth is required) are authenticated via HMAC-signed headers (`x-supoclip-user-id`, `x-supoclip-ts`, `x-supoclip-signature`), not raw session cookies. Programmatic clients (MCP server, API consumers) instead use a per-user API key (`Authorization: Bearer sk_...` or `x-api-key`), resolved by `auth_headers.resolve_authenticated_user_id` (API key → DB lookup, else falls back to signed session headers). Only the SHA-256 hash of an API key is stored (`api_keys` table); the frontend manages keys at `/settings/api-keys`.

### Backend: Layered Architecture

The backend was refactored from monolithic (`main.py`, legacy — do not use for new work) to layered (`main_refactored.py`, active):

```
api/routes/          → HTTP handlers (tasks.py, media.py)
services/            → Business logic (task_service.py, video_service.py)
repositories/        → Raw SQL via asyncpg (task_repository.py, clip_repository.py, source_repository.py)
workers/             → ARQ job queue (tasks.py, job_queue.py, progress.py)
utils/               → Thread pool helpers for blocking operations (async_helpers.py)
```

**Key patterns:**
- All DB access goes through repository classes using raw SQL (`text()` queries), not SQLAlchemy ORM
- Blocking operations (video processing, downloads, transcription) wrapped in `run_in_thread()` to avoid blocking the async event loop
- Progress tracking uses Redis pub/sub → SSE to frontend
- Task status flow: `queued → processing → completed/error/cancelled`

### Video Processing Pipeline

1. **Input** → YouTube URL (yt-dlp, or Apify as an alternate download/metadata provider) or uploaded file
2. **Transcription** → AssemblyAI word-level timestamps (cached as `.transcript_cache.json`); alternate providers configurable
3. **AI Analysis** → Pydantic AI selects 3-7 viral segments (10-45s each) with virality scoring
4. **Clip Generation** → MoviePy creates clips (9:16 vertical, or original aspect ratio) with:
   - Face-centered cropping: MediaPipe → OpenCV DNN → Haar cascade (fallback chain)
   - Word-synced subtitles from AssemblyAI
   - Custom fonts (TTF files in `backend/fonts/`)
   - Optional transition effects (`backend/transitions/`)
   - Optional B-roll overlays (Pexels API)
   - Caption templates with animation styles
5. **Storage** → Clips to `{TEMP_DIR}/clips/`, metadata to PostgreSQL

### Frontend Architecture

- **Next.js 15** with App Router, React 19, TailwindCSS v4
- **ShadCN UI** (New York style, stone base color, Radix primitives)
- **Better Auth** with Prisma adapter (only exercised when `REQUIRE_AUTH=true`)
- **No global state library** — React hooks only (`useState`, `useEffect`, `useSession`)
- Mostly client-side (`"use client"`) product pages — SSR is minimal
- Prisma client generated to `frontend/src/generated/prisma/` (custom output path)
- Build: `prisma generate && next build` (Prisma generate runs on both build and postinstall)

### Multi-Tool Platform Shell

SupoClip is architecturally a single tool (clipping) today, but the frontend has a light shell preparing for more tools later (a ranking/compilation tool, a voiceover/animation tool, etc., per product planning) without a rewrite when they arrive:

- **`frontend/src/tools/types.ts`** defines the `Tool` shape every tool is described by: `id`, `name`, `icon`, `description`, plus either `mount(container): (() => void) | void` (a self-contained tool simple enough to render straight into a DOM node — see `tools/placeholder/`, which really implements this via a React root and a real unmount cleanup) or `href` + optional `matchPaths` (a **route-owning** tool, one big/stateful enough to be its own multi-page Next.js route subtree — Clipping is this shape, since create/list/tasks/trash are separate pages, not one screen). There's no plugin registry or dynamic loading — `frontend/src/tools/registry.ts` is a literal `Tool[]` array.
- **`frontend/src/tools/clipping/index.ts`** — the descriptor for the existing clipping tool (`href: "/list"`, `matchPaths: ["/create", "/tasks", "/trash"]`). Its actual implementation is unchanged: the route files under `frontend/src/app/(clipping)/` (a Next.js **route group** — `(clipping)` doesn't appear in the URL, so `/create`, `/list`, `/tasks/[id]`, `/tasks/[id]/edit`, `/trash` are all identical to before this existed). `frontend/src/app/(clipping)/layout.tsx` is the one shared layout for that whole group (consolidates what used to be two near-identical per-route `layout.tsx` files) and renders `<ToolTabs />` above every clipping page.
- **`frontend/src/tools/placeholder/index.tsx`** — the one generic "coming soon" stand-in for every future tool (deliberately not named after any specific one), reachable at `/tools/placeholder`. Real proof the `mount()` contract works, not a mock.
- **`frontend/src/components/tool-tabs.tsx`** (the top tab bar, maps over `TOOLS` from the registry) and **`tool-mount.tsx`** (hosts a `mount()`-based tool inside a route, used by `/tools/placeholder`'s page) are the only two shell components.
- **Shared vs. tool-specific** (what a new tool should and shouldn't need to touch): **shared** — `/settings` (admin runtime settings, templates system), the `tasks`/`generated_clips` Postgres schema's lifecycle columns (status/progress/started_at/share_token), the job queue (`workers/job_queue.py`, generic arq/Redis wrapper), font registry, export-preset dataclass shape, GPU config. **Clipping-specific** — `workers/tasks.py::process_video_task`, `caption_templates.py`, `clip_editor.py`'s `EXPORT_PRESETS` values, and most of `video_utils.py` (ffmpeg/font helpers are reusable; face-crop/hook/overlay ASS-builders are not). A future tool with a substantially different pipeline (e.g. voiceover generation has no "clip" concept at all) would likely want its own settings/output columns rather than overloading the `tasks` table — that reorganization hasn't been done since no second tool exists yet to design it against.

**To add a new tool**: create `frontend/src/tools/<tool>/index.ts(x)` exporting a `Tool`; if it's route-based, add its pages under their own route group in `frontend/src/app/` (e.g. `(ranking)/`) the same way `(clipping)/` works; add the tool to `frontend/src/tools/registry.ts`. That's the whole integration surface for the shell — no other file needs to change. Backend-side, a substantially different pipeline (non-clipping input/output) should get its own worker task function and settings rather than extending `process_video_task`/the `tasks` table; the generic pieces above are ready to reuse.

### Home Screen

`/` (`frontend/src/components/home-app.tsx`, rendered via `home-router.tsx`) is an operations-dashboard launchpad for the multi-tool platform, not a marketing page and not a bare project list — that content lives at `/list` (the Clipping tool's full history, linked from Home's "View all"). Sections top to bottom, each its own component under `frontend/src/components/home/`:

- **`home-top-bar.tsx`** — thin persistent header: wordmark left, icon-only Notifications/System Status/Settings/theme-toggle right. No search input by design. Notifications is stubbed with a "coming soon" toast; System Status scrolls to the status strip.
- **`hero-actions.tsx`** — "Start something": New Clip (`/create`), Import Video (file picker → same pending-file handoff as drag-and-drop, below), and a "Continue" card for the last-opened project (only rendered when one exists).
- **`recent-projects.tsx`** — up to 6 most-recent tasks as thumbnail cards (YouTube thumbnail or a Film-icon fallback, via `lib/youtube-thumbnail.ts`), "View all" links to `/list`. Empty state reuses `EmptyState`.
- **`tools-grid.tsx`** + **`tool-card.tsx`** — one card per entry in `frontend/src/tools/registry.ts`; this is the primary discovery surface for future tools. A tool with `href` (route-owning, e.g. Clipping) is clickable with an "Open" button; a `mount`-only tool (e.g. Placeholder) renders disabled with "Coming soon" — this falls out of the existing `Tool` shape, no new fields needed for that part.
- **`activity-feed.tsx`** — compact last-5-actions list derived from the same task fetch (no new backend aggregation — per-week clip/hour totals aren't tracked anywhere yet, so that's deferred rather than faked).
- **`status-strip.tsx`** — bottom operator strip (queue depth, active jobs, GPU on/off/unavailable, disk free), polling `GET /tasks/system-status` (backend: `api/routes/tasks.py::get_system_status`, counts from `TaskRepository.get_status_counts`, GPU via the existing `detect_gpu_encoder()` cache, disk via `shutil.disk_usage(TEMP_DIR)`) every 10s. Routed to the backend automatically by the existing generic `frontend/src/app/api/tasks/[...path]/route.ts` proxy — no dedicated Next.js route file needed for a new `/tasks/*` backend endpoint.

**Tool card thumbnails**: a `Tool` may set `thumbnail: "/assets/tools/<tool-id>.svg"` (see `tools/types.ts`) pointing at a static SVG under `frontend/public/assets/tools/` — plain files, never inlined/base64'd, so art can be swapped without a code change. `ToolCard` renders it in an `<img>` with an `onError` fallback to a plain CSS icon tile (`tool.icon` + name) so a missing/broken thumbnail never breaks the grid. **To add a thumbnail for a new tool**: drop `frontend/public/assets/tools/<tool-id>.svg` and set `thumbnail` on that tool's descriptor — nothing else changes.

**Drag-and-drop and Import Video** hand a `File` to `/create` across a full route navigation via `frontend/src/lib/pending-file-transfer.ts` (a module-level variable — survives a Next.js client-side navigation, but not a hard reload) — `create/page.tsx` consumes it once on mount via `takePendingFile()`. **"Continue"** is backed by `frontend/src/lib/last-project.ts` (localStorage), written by `tasks/[id]/page.tsx` every time a project loads.

### Database

PostgreSQL 15. Schema in `init.sql`. Mixed naming conventions:
- `tasks`, `sources`, `generated_clips` → snake_case
- `session`, `account`, `verification`, `users` → camelCase (Better Auth)
- UUIDs stored as VARCHAR(36)
- Auto-update triggers on `updated_at`/`updatedAt` columns

## Key Backend Files

| File | Purpose |
|------|---------|
| `src/main_refactored.py` | Active FastAPI entry point |
| `src/main.py` | Legacy monolithic entry point (do not use for new work) |
| `src/api/routes/tasks.py` | Task CRUD, SSE progress, clip editing endpoints |
| `src/api/routes/media.py` | Fonts, transitions, uploads, templates |
| `src/auth_headers.py` | Local-first bypass, HMAC session verification, API key auth |
| `src/services/task_service.py` | Task orchestration, clip editing logic |
| `src/services/video_service.py` | Video download, transcription, AI analysis, clip generation |
| `src/workers/tasks.py` | ARQ worker task definitions |
| `src/workers/job_queue.py` | Job queue management |
| `src/workers/progress.py` | Real-time progress via Redis |
| `src/ai.py` | Pydantic AI agents, system prompt, segment validation |
| `src/video_utils.py` | Video processing, cropping, subtitles |
| `src/clip_editor.py` | Clip trim, split, merge, export presets |
| `src/broll.py` | Pexels API B-roll integration |
| `src/caption_templates.py` | Caption template system |
| `src/config.py` | Environment variable configuration |

## API Endpoints (routes in `api/routes/`)

**Task lifecycle:**
- `POST /start-with-progress` — Create task, enqueue to worker (returns task_id)
- `GET /tasks/` — List user tasks
- `GET /tasks/system-status` — Queue depth, active/processing job counts, GPU state, disk space (home screen's status strip)
- `GET /tasks/{id}` — Get task with clips
- `GET /tasks/{id}/progress` — SSE real-time progress stream
- `POST /tasks/{id}/cancel` — Cancel processing
- `POST /tasks/{id}/resume` — Resume cancelled/errored task
- `DELETE /tasks/{id}` — Delete task

**Clip editing:**
- `PATCH /tasks/{id}/clips/{clip_id}` — Trim clip
- `POST /tasks/{id}/clips/{clip_id}/split` — Split at timestamp
- `POST /tasks/{id}/clips/merge` — Merge selected clips
- `PATCH /tasks/{id}/clips/{clip_id}/captions` — Update captions
- `GET /tasks/{id}/clips/{clip_id}/export?preset=tiktok` — Export with platform preset

**Media:**
- `GET /fonts`, `GET /transitions`, `GET /caption-templates`, `GET /broll/status`
- `POST /upload` — Upload video file
- `GET /clips/{filename}` — Serve generated clips

**API keys (programmatic access):**
- `GET /api-keys/` — List the user's API keys (metadata only)
- `POST /api-keys/` — Create a key (plaintext `sk_...` returned exactly once)
- `DELETE /api-keys/{key_id}` — Revoke a key

Full endpoint list including billing/admin/feedback routes: [docs/api-reference.md](docs/api-reference.md).

## Environment Variables

See [docs/configuration.md](docs/configuration.md) for the complete list. Core ones:

```bash
ASSEMBLY_AI_API_KEY=...              # Required: video transcription
LLM=google-gla:gemini-3-flash-preview # Format: provider:model-name
GOOGLE_API_KEY=...                   # Or OPENAI_API_KEY / ANTHROPIC_API_KEY
OLLAMA_BASE_URL=http://localhost:11434/v1  # Optional for ollama:* models
OLLAMA_API_KEY=...                   # Optional; required for Ollama Cloud

REQUIRE_AUTH=false                   # Default: local-first, no login. Set true on both frontend and backend for hosted/multi-tenant mode
PEXELS_API_KEY=...                   # Optional: B-roll stock footage
REDIS_HOST=localhost                 # Default: localhost
REDIS_PORT=6379                      # Default: 6379
QUEUED_TASK_TIMEOUT_SECONDS=180      # Fail-safe for stuck tasks
TEMP_DIR=/tmp                        # Temp file storage
DATABASE_URL=postgresql+asyncpg://...
BETTER_AUTH_SECRET=...               # Frontend auth secret (only used when REQUIRE_AUTH=true)
```

## Local LLM (Ollama)

SupoClip's content-policy detection and metadata generation features (see [Conventions](#conventions)) default to a **local Ollama model as the primary LLM**, with Gemini Flash-Lite as an explicit opt-in fallback for users without a GPU — never the other way around. This keeps those features free, private, and unlimited by default.

- **Install**: `curl -fsSL https://ollama.com/install.sh | sh` (Linux, installs+starts a systemd service), `brew install ollama` (macOS), `winget install --id Ollama.Ollama -e` (Windows). See `backend/src/ollama_status.py::check_ollama_status()` for the live reachability/model-list probe used by Settings' "Test connection" action and the `OLLAMA_MODEL` dropdown — it always makes a real request rather than trusting config, mirroring `video_utils.detect_gpu_encoder()`'s probe-don't-assume approach.
- **Recommended models**: `llama3.2:3b` (balanced default), `gemma2:2b` (fastest, lower VRAM), `qwen2.5:3b` (best JSON-mode reliability for structured output). Pull with `ollama pull <name>`.
- **`OLLAMA_KEEP_ALIVE=30s`**: set this in the environment the `ollama serve`/service process runs in (systemd drop-in on Linux: `systemctl edit ollama`; `launchctl setenv`/shell profile on macOS; `setx` + service restart on Windows). This auto-unloads the model after 30s idle so its VRAM is freed for video rendering between LLM calls.
- **VRAM serialization rule**: Ollama inference and ffmpeg rendering (GPU-accelerated or not) can compete for the same GPU's VRAM. `backend/src/workers/resource_locks.py` provides a Redis-backed distributed semaphore (`resource_slot(redis, name, max_concurrent)`) reused across worker processes; both LLM call sites (`ai.py`) and render call sites (`video_service.py`/`video_utils.py`) acquire the shared `"gpu"` slot (max_concurrent=1) so a local LLM call is never in flight at the same time as a render job. Remote Gemini calls intentionally skip this slot — they don't touch local VRAM.
- **Gemini fallback**: reuses the existing `GOOGLE_API_KEY` setting (no separate Gemini-specific key) plus a `GEMINI_MODEL` setting (default `gemini-3.5-flash-lite` — confirmed live via the Settings "Test Gemini connection" button that `gemini-2.0-flash-lite` is now retired by Google). Only used when Ollama is unreachable (or its output fails validation twice) *and* a Google API key is configured *and* the user has opted into the fallback (`LLM_PROVIDER_MODE=hybrid` or `gemini`). See `ai.py::run_with_llm_fallback()`.
- **Provider selection**: Settings → LLM Provider (`Ollama (local)` / `Gemini` / `Hybrid`), with a live status indicator and "Test connection" actions for both providers.

## Conventions

- **Runtime settings must always show their current effective value.** `/admin/runtime-settings` (`src/api/routes/admin.py::_setting_status`) returns a `current_value` field for every non-`password` setting (decrypted admin value or the env fallback); the frontend (`RuntimeSettingsForm`) renders it next to the label and in the input's placeholder/default option. Password-type settings intentionally never expose their value. When adding a new runtime setting, keep this contract — never hide a non-secret value behind a generic "configured"/"unset" placeholder.
- **Caption/hook font sizing scales off the shorter frame dimension**, not just width (`get_scaled_font_size(base, width, height)` in `video_utils.py`). Scaling by width alone made captions balloon on wide outputs (16:9, 1:1) relative to their shorter height and pushed them past the safe area. `get_safe_vertical_position` treats its return value as the vertical **center** of the text block (matching the ASS `Alignment 5` + `\pos` renderer), not a top-left corner — keep that anchor convention consistent if you touch subtitle positioning.
- **`max_clips`/`target_duration_seconds` are per-request overrides**, threaded end-to-end: `api/routes/tasks.py::create_task` → `enqueue_processing_job` → `workers/tasks.py::process_video_task` → `TaskService.process_task` → `VideoService.process_video_complete` → `VideoService.analyze_transcript`, falling back to the global `MAX_CLIPS`/`CLIP_DURATION` config when unset. `POST /tasks/{id}/resume` must forward every one of these (plus `hook_style`/`social_overlay`) from the saved `task_source:{id}` metadata — it silently dropped them before; don't reintroduce that gap when touching resume.
- **Progress SSE payloads carry a `stage` field** (`download`/`transcribe`/`analyze`/`render`/`complete`, set in `VideoService.process_video_complete`'s and `TaskService.process_task`'s progress-callback calls) alongside the existing `progress`/`message`/`status`. There's also a `clip_progress` event (`ProgressTracker.clip_started`, distinct from `clip_ready`) fired before each clip starts rendering, so the frontend can show "rendering clip i/N" before it's done. The frontend falls back to guessing a stage from the percentage when `stage` is absent (older cached events) — keep both in sync if you add a new stage.
- **Frontend auto-save pattern**: `useDebouncedEffect` (`frontend/src/lib/use-debounced-effect.ts`) debounces a save call after state settles, skipping the first render. When the watched state is also refreshed from the server (e.g. `fetchTaskStatus` reloading project settings), guard against re-saving unchanged data with a "last saved snapshot" ref comparison — see `tasks/[id]/page.tsx`'s `lastSavedProjectSettingsRef` for the pattern. On that page, auto-save persists settings cheaply (`apply_to_existing: false`) while the expensive "regenerate every clip" action stays an explicit button click.
- **Clip cleanup (pause/filler removal) has a 0-100 `sensitivity` slider** (`clip_cleanup.py::normalize_clip_cleanup_settings`) that's the primary control when present: 0 disables cleanup, higher values lower the pause threshold and widen the filler-word list. Omitting it preserves the legacy explicit `cut_long_pauses`/`pause_threshold_ms`/`remove_filler_words` behavior for backward compatibility. Filler-word removal in `video_utils.py::build_clip_keep_ranges` never cuts a match within 0.75s of the clip end, a sentence-final word, or one adjacent to `!`/`?` — don't reintroduce context-free literal matching there. Crossfade blending between stitched cuts uses a *per-junction* fade (`crossfade_fades_for_ranges`), not a single global one — every junction should dissolve smoothly regardless of segment count or a short neighboring fragment; anything consuming `crossfade_fade_for_ranges` for per-word/per-junction timing should use the plural per-junction variant instead.
- **Task deletion is soft-delete, not a hard `DELETE`.** `DELETE /tasks/{id}` sets `tasks.deleted_at` (`TaskRepository.delete_task`); every existing list/get query filters `deleted_at IS NULL`. The row (and its `generated_clips`) only actually disappears via `DELETE /tasks/{id}/purge` (`TaskRepository.purge_task`), which best-effort removes the on-disk clip files first — source videos are never touched by either path. `GET /tasks/trash` / `POST /tasks/{id}/restore` round out the lifecycle; keep new task-scoped queries filtering `deleted_at` the same way, or soft-deleted tasks will leak back into normal listings.
- **A shared `enforce_size_cap()` (`video_utils.py`) caps every finalized clip at 300MB**, called after each of the three independent final-encode sites (main render pass, subtitle-burn pass, `clip_editor.export_with_preset`): it only re-encodes (two-pass, bitrate computed from target size ÷ duration) when the CRF-quality output actually exceeds the cap, so quality is never sacrificed unless necessary. Loudness normalization is similarly centralized — `build_audio_output_args(has_audio, target_lufs=...)` is the only place that builds the `loudnorm` filter string; any new ffmpeg call site that finalizes a clip should route audio args through it (with the export preset's `target_lufs`) rather than hand-rolling `aac`/bitrate args, which is what caused normalization to silently not apply on two render paths before this was centralized.
- **Export presets carry duration/safe-area/loudness metadata, not just resolution.** `ExportPreset` (`clip_editor.py`) has `max_duration_seconds`, `safe_area_top_pct`/`safe_area_bottom_pct`, and `target_lufs` alongside bitrate/dimensions; `GET /export-presets` returns all of them so the frontend can render preset options dynamically instead of hardcoding names. New presets are appended to `EXPORT_PRESETS` (dict insertion order = display order) — never reorder or replace the existing entries, since `preset=` values are persisted/referenced externally.
- **Emoji reactions reuse the hook-title ASS/animation infrastructure**, not a new rendering path. `generated_clips.reactions` is a JSON-encoded `TEXT` column (same pattern as `hook_title_variants`); `emoji_reactions.build_emoji_reactions_ass()` emits ASS dialogue events using the same animation vocabulary as `caption_templates.HOOK_ANIMATIONS` and the same `\pos`/Alignment-5-center convention as `build_hook_title_ass`, appended into the same subtitle file already burned via libass. Saving reactions (`PATCH /tasks/{id}/clips/{clip_id}/reactions`, body `{"reactions": [...]}`) always triggers a real re-render from source — there's no cheap non-rendering update, since the reaction is burned into the frame.
- **Most page content still uses literal Tailwind colors (`stone-*`, and previously some raw `bg-white`/`text-black`), not the semantic CSS-variable tokens** (`bg-background`/`text-foreground`/etc. in `globals.css`) that `next-themes`' `.dark` class toggling actually affects. The theme toggle and `ThemeProvider` are wired up and work correctly, but only shadcn primitives and the outer page shells fully adapt to dark mode today — a full pass replacing `stone-*`/hardcoded colors with theme tokens across every page is still open work.
- **Hook title generation rules live in one place**: `backend/src/ai.py::HOOK_GENERATION_RULES`, an audience-first/curiosity-driven spec (topic clarity, emoji only at the end, banned generic phrases) shared verbatim by both hook-generation call sites — the per-segment `hook_title` produced as part of the cached transcript analysis (`transcript_analysis_system_prompt`), and the on-demand `generate_hook_title_variants()` used by the editor's "Regenerate Hook" button and the "Compare Hooks" A/B dialog. Changing hook-writing rules means editing this one constant, not both prompts separately. Analysis results (hook titles included) are cached by source URL + processing mode (`processing_cache` table, keyed off `TRANSCRIPT_ANALYSIS_CACHE_VERSION`) — bump that version string to invalidate old hooks after a rules change; `generate_hook_title_variants()` itself is never cached, since it's only called on an explicit user action.
- **Hook title font size** (`build_hook_title_ass` in `video_utils.py`) clamps to 40-160px (scaled off the shorter frame dimension via `caption_font_px`, same convention as captions), with the frontend's Small/Default/Large/XL preset mapping to a `hook_font_size_scale` multiplier (0.65/null/1.0/1.3) rather than a raw pixel value.
- **Captions wrap and auto-shrink to stay inside the frame.** `build_assemblyai_ass_subtitles` chunks words by estimated on-screen width as well as `max_words_per_line` (`_split_caption_chunks`), and shrinks the font (down to 18px) if even the single longest word in the clip wouldn't fit the horizontal safe area (`get_subtitle_max_width`, now actually wired in). Caption font size is also editable inline in the clip editor (not just the create/settings flow) via a debounced auto-save that persists to the task's `font_size` column through a partial update (`TaskRepository.update_task_font_size`) — it never touches `font_family`/`font_color`/`caption_template`, unlike the full-replace `POST /tasks/{id}/settings`.
- **Reusable settings templates** (`project_templates` table, `api/routes/templates.py`) let a user save a project's current settings (font/caption/hook/social-overlay/B-roll/cleanup/export/duration/clip-count) as a named, versioned bundle and apply it onto any other project — REPLACE (full overwrite) or MERGE (template values win, unset template fields keep the project's current value). `TEMPLATE_SCHEMA_VERSION` + `migrate_template_settings()` is the upgrade path for future shape changes; version 1 is the only version that has ever existed, so it's currently a passthrough (re-normalized). Managed at `/settings/templates` (rename/duplicate/delete/export/import as JSON) plus "Save as Template"/"Load Template" controls in the per-project settings sheet.
- **Toasts (`sonner`) go through `frontend/src/lib/toast.ts`, not `"sonner"` directly.** Success/info/warning auto-dismiss after 4s; errors stay until manually closed (`duration: Infinity`) since they usually need to be read or acted on. Import `toast` from `@/lib/toast` in any new call site instead of `"sonner"` so this stays the single place that decides dismiss behavior.
- **"Export All Clips"** (`tasks/[id]/page.tsx::handleExportAllClips`) exports every clip at the project's export preset in sequence (not parallel — the backend renders one export at a time anyway), retrying a failed clip once automatically before marking it failed; one clip failing never stops the batch. A progress dialog tracks per-clip status with an inline retry for anything still failed, and finishes with one aggregate toast. It reuses the single-clip export path's "original" preset special case (a frontend-only sentinel meaning "download the rendered file as-is", not a real backend `EXPORT_PRESETS` entry).
- **Hook highlighting is backend-correct; the frontend preview used to lie about it.** `build_hook_title_ass` (`video_utils.py`) has always colored power words/digits/user-requested `highlight_words` correctly once burned in — the bug was that `HookTitlePreview`, its use in `HookVariantCompare`, and a third duplicate inline preview in `create/page.tsx` never applied any per-word highlight logic (one showed a hardcoded sample, the others showed real hook text as one plain unstyled string). Fixed by `frontend/src/lib/hook-highlight.ts`, which mirrors the backend's exact `POWER_WORDS`/digit/`normalize_token` rules — keep it in sync if those change on the backend.
- **`GET /tasks/` and `GET /tasks/trash` clamp `limit` to `[1, 500]`** (was an unbounded `int = 50` default with nothing stopping a caller from requesting more, but nothing asking for more either). `/list` and `/trash` now explicitly request `?limit=500` so "select all" actually sees every task — the previous bug wasn't the delete logic (already correct: per-id `Promise.allSettled`, immune to partial failures) but the fact that only the first 50 tasks were ever loaded to select from. `frontend/src/app/api/tasks/route.ts` (the base `/api/tasks/` proxy) forwards query params now; it silently dropped them before.
- **This project's ffmpeg/libass build cannot render colour text glyphs at all** (verified directly: neither a system-installed Noto Color Emoji (CBDT/bitmap) nor a bundled Twemoji Mozilla (COLR/CPAL) font produces any pixels through the `subtitles`/`ass` filter, regardless of font format) — this isn't a missing-font problem, don't try bundling a "better" emoji font to fix it. Emoji reactions are burned as true-colour **image overlays** instead (`emoji_reactions.py::overlay_emoji_reactions_ffmpeg`, ffmpeg `overlay` filter + `-loop 1` PNG inputs — needs an explicit `-t <duration>` cap, since `-shortest` alone doesn't reliably terminate a filter graph built on infinite-duration looped image inputs), using 32 bundled Twemoji PNGs at `backend/assets/emoji/` (CC-BY 4.0, see `NOTICE.txt` there) matching `emoji-picker.tsx`'s `REACTION_EMOJIS`. Animation is simplified to fade in/out for this path — the full `_entrance_tags` scale-animation vocabulary stays ASS-text-only. Caption keyword-emoji (word-position-dependent, much harder to overlay correctly without real text-layout metrics) stays honestly disabled via `emoji_rendering_supported()`'s probe rather than claiming a fix that doesn't render.
- **GPU-accelerated rendering** is opt-in via the `GPU_ACCELERATION_ENABLED` runtime setting (Settings → Export), wired into the single shared `build_final_video_encode_args()` (all 5 encode call sites in `render_reframed_clip_ffmpeg`) as an NVENC/libx264 switch. `detect_gpu_encoder()` always re-verifies with a real trivial NVENC encode attempt rather than trusting ffmpeg's compiled-encoder list or the saved setting — a render silently and correctly falls back to CPU if the hardware isn't actually there, and the admin settings UI shows the toggle disabled with the specific reason (`_setting_status`'s `disabled_reason` field) rather than leaving a user to wonder why it's not doing anything. Only NVENC is implemented; VAAPI/QSV each need their own hwupload/format-negotiation filter chain and are follow-up work, as are the 4 other standalone `libx264` call sites elsewhere in `video_utils.py` (two-pass/size-cap re-encodes) that don't route through the shared function.
- **Progress UI shows real elapsed time and an honest ETA.** `tasks/[id]/page.tsx` ticks `elapsedSeconds` from the task's own `started_at` (survives a page refresh mid-render). The ETA is only ever computed from this run's own observed per-clip render speed once inside the `render` stage (real clips-done ÷ real elapsed-since-render-started) — every earlier stage shows "estimating…" rather than a fabricated number, and there's no historical-duration backend endpoint feeding this (the existing `/tasks/metrics/performance` is admin-only and aggregate, not per-task).

## Common Workflows

### Adding fonts/transitions

Drop `.ttf` files into `backend/fonts/` or `.mp4` files into `backend/transitions/`. They auto-appear via their respective `GET` endpoints.

### Modifying AI clip selection

Edit `backend/src/ai.py`: `simplified_system_prompt` controls selection criteria, `TranscriptSegment` defines the output model, `get_most_relevant_parts_by_transcript()` runs analysis with validation.

### Video processing constraints

- Output: 9:16 vertical (default) or original aspect ratio, H.264, even pixel dimensions (`round_to_even()`)
- Subtitles positioned at 75% down the frame
- Virality scoring: `hook_score`, `engagement_score`, `value_score`, `shareability_score` (0-25 each, summed to `virality_score` 0-100)
- Each segment gets an AI-written `hook_title` (3-9 words) burned into the top safe area for the first ~4s (`build_hook_title_ass` in `video_utils.py`), persisted on `generated_clips.hook_title`
  - Hook animation styles (`caption_templates.HOOK_ANIMATIONS`): `fade_pop`, `fade`, `slide_down`, `zoom_punch`, `bounce`, `pulse`, `none`
  - Hook type labels (`caption_templates.HOOK_TYPES`, AI-classified but user-overridable): `question`, `statement`, `statistic`, `story`, `contrast`, `callout`, `warning`, `none`
  - A/B hook comparison: `ai.generate_hook_title_variants()` generates alternative hook titles for an existing clip (stored as JSON in `generated_clips.hook_title_variants`); `TaskService.select_hook_variant()` applies a chosen variant/custom text and re-renders the clip from source (the hook is burned into the same frame as the crop/captions, so it can't be swapped without a re-render) — see `POST/PATCH /tasks/{id}/clips/{clip_id}/hook-variants[/select]`
- Static talking-head crops get a slow ~5% Ken Burns punch-in (`kenburns_zoom_fragment`); tracked pans and split screens keep their own motion

## iOS App

A native iOS app ships on the App Store
(https://apps.apple.com/us/app/supoclip/id6784760040, app id `6784760040`). Its
source lives outside this repo. It talks to the hosted API and bills through
RevenueCat, which is what `frontend/src/app/api/billing/revenuecat-webhook/`
serves; App Store subscribers are blocked from Stripe checkout/portal by the
"managed through the App Store" guard in the billing routes. The www references
the app via `APP_STORE_ID`/`APP_STORE_URL` in `frontend/src/lib/site.ts`
(Smart App Banner meta, JSON-LD `MobileApplication`, hero badge, footer link).

## MCP Server

`mcp/` is a standalone [MCP](https://modelcontextprotocol.io) server
(`supoclip-mcp`, Python/FastMCP, stdio) that exposes SupoClip to MCP clients
(Claude Desktop/Code, Cursor, …). It is a thin client over the REST API.

- **Default target:** the hosted API `https://api.supoclip.com`. Override with
  `SUPOCLIP_API_URL` for self-hosting (e.g. `http://localhost:8000`).
- **Auth:** a per-user API key in `SUPOCLIP_API_KEY` (see API keys above).
  Self-hosters may instead use `SUPOCLIP_USER_ID` (+ `SUPOCLIP_AUTH_SECRET` when
  signing is enforced).
- **Tools:** create/list/get/wait/cancel/resume/delete tasks, list/download/
  export clips, and public discovery (templates, transitions, fonts, B-roll).
- Run with `cd mcp && uv run supoclip-mcp`. Details in `mcp/README.md`.
