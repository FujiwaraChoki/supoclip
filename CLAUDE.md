# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SupoClip is an open-source alternative to OpusClip — an AI-powered video clipping tool that transforms long-form content into viral short clips. AGPL-3.0 licensed. Hosted at supoclip.com; self-hostable via Docker Compose.

A `docs/` directory is the canonical deep-dive documentation (start at [docs/README.md](docs/README.md)): [architecture.md](docs/architecture.md), [configuration.md](docs/configuration.md), [api-reference.md](docs/api-reference.md), [app-guide.md](docs/app-guide.md), [development.md](docs/development.md), [troubleshooting.md](docs/troubleshooting.md). Prefer those for detail beyond what's below.

## Development Commands

### Docker (recommended)

```bash
docker-compose up -d --build      # Start/rebuild all services
docker-compose logs -f backend    # Debug backend
docker-compose logs -f worker     # Debug video processing
docker-compose down               # Stop all services
```

Services: Frontend (:3107 locally / :3000 in Docker), Backend API (:8000, docs at `/docs`), Worker (ARQ), PostgreSQL (:5432), Redis (:6379).

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

## Conventions

- **Runtime settings must always show their current effective value.** `/admin/runtime-settings` (`src/api/routes/admin.py::_setting_status`) returns a `current_value` field for every non-`password` setting (decrypted admin value or the env fallback); the frontend (`RuntimeSettingsForm`) renders it next to the label and in the input's placeholder/default option. Password-type settings intentionally never expose their value. When adding a new runtime setting, keep this contract — never hide a non-secret value behind a generic "configured"/"unset" placeholder.
- **Caption/hook font sizing scales off the shorter frame dimension**, not just width (`get_scaled_font_size(base, width, height)` in `video_utils.py`). Scaling by width alone made captions balloon on wide outputs (16:9, 1:1) relative to their shorter height and pushed them past the safe area. `get_safe_vertical_position` treats its return value as the vertical **center** of the text block (matching the ASS `Alignment 5` + `\pos` renderer), not a top-left corner — keep that anchor convention consistent if you touch subtitle positioning.
- **`max_clips`/`target_duration_seconds` are per-request overrides**, threaded end-to-end: `api/routes/tasks.py::create_task` → `enqueue_processing_job` → `workers/tasks.py::process_video_task` → `TaskService.process_task` → `VideoService.process_video_complete` → `VideoService.analyze_transcript`, falling back to the global `MAX_CLIPS`/`CLIP_DURATION` config when unset. `POST /tasks/{id}/resume` must forward every one of these (plus `hook_style`/`social_overlay`) from the saved `task_source:{id}` metadata — it silently dropped them before; don't reintroduce that gap when touching resume.
- **Progress SSE payloads carry a `stage` field** (`download`/`transcribe`/`analyze`/`render`/`complete`, set in `VideoService.process_video_complete`'s and `TaskService.process_task`'s progress-callback calls) alongside the existing `progress`/`message`/`status`. There's also a `clip_progress` event (`ProgressTracker.clip_started`, distinct from `clip_ready`) fired before each clip starts rendering, so the frontend can show "rendering clip i/N" before it's done. The frontend falls back to guessing a stage from the percentage when `stage` is absent (older cached events) — keep both in sync if you add a new stage.
- **Frontend auto-save pattern**: `useDebouncedEffect` (`frontend/src/lib/use-debounced-effect.ts`) debounces a save call after state settles, skipping the first render. When the watched state is also refreshed from the server (e.g. `fetchTaskStatus` reloading project settings), guard against re-saving unchanged data with a "last saved snapshot" ref comparison — see `tasks/[id]/page.tsx`'s `lastSavedProjectSettingsRef` for the pattern. On that page, auto-save persists settings cheaply (`apply_to_existing: false`) while the expensive "regenerate every clip" action stays an explicit button click.
- **Clip cleanup (pause/filler removal) has a 0-100 `sensitivity` slider** (`clip_cleanup.py::normalize_clip_cleanup_settings`) that's the primary control when present: 0 disables cleanup, higher values lower the pause threshold and widen the filler-word list. Omitting it preserves the legacy explicit `cut_long_pauses`/`pause_threshold_ms`/`remove_filler_words` behavior for backward compatibility. Filler-word removal in `video_utils.py::build_clip_keep_ranges` never cuts a match within 0.75s of the clip end, a sentence-final word, or one adjacent to `!`/`?` — don't reintroduce context-free literal matching there. Crossfade blending between stitched cuts uses a *per-junction* fade (`crossfade_fades_for_ranges`), not a single global one — every junction should dissolve smoothly regardless of segment count or a short neighboring fragment; anything consuming `crossfade_fade_for_ranges` for per-word/per-junction timing should use the plural per-junction variant instead.

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
