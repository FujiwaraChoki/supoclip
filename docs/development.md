# Development

This guide is for contributors working on SupoClip locally.

## Repository Layout

Current repository structure:

- `backend/`
  - FastAPI app
  - ARQ worker
  - services, repositories, route modules, and media-processing code
- `frontend/`
  - Next.js app
  - App Router pages, API routes, auth, Prisma schema, UI components
- Root files
  - `docker-compose.yml`
  - `init.sql`
  - `start.sh`

## Main Commands

## Full stack with Docker

```bash
docker-compose up -d --build
docker-compose logs -f
docker-compose down
```

## Frontend

The frontend's package manager is pnpm (pinned via `packageManager` in `frontend/package.json`), not npm.

```bash
cd frontend
pnpm install
pnpm run dev      # http://localhost:3107
pnpm run build    # prisma generate && next build
pnpm run start
pnpm run lint
```

## Backend

```bash
cd backend
uv venv .venv
source .venv/bin/activate
uv sync
uvicorn src.main_refactored:app --reload --host 0.0.0.0 --port 8000
```

Run the worker separately:

```bash
cd backend
source .venv/bin/activate
arq src.workers.tasks.WorkerSettings
```

## Frontend Development Notes

Important locations:

- `frontend/src/app`
  - App pages and API routes
- `frontend/src/components`
  - Reusable UI and product components
- `frontend/src/lib`
  - Auth, API helpers, backend proxy helpers, Stripe wiring
- `frontend/prisma`
  - Prisma schema and migrations, if present in your branch
- `frontend/src/generated/prisma`
  - Generated Prisma client output

### Build behavior

The frontend build runs:

```bash
prisma generate && next build
```

`postinstall` also runs Prisma generation.

### Frontend patterns

- App Router
- Mostly client-side product pages
- Better Auth sessions
- No dedicated global state library

## Backend Development Notes

Important locations:

- `backend/src/main_refactored.py`
  - Active entry point
- `backend/src/api/routes`
  - Route modules
- `backend/src/services`
  - Business logic
- `backend/src/repositories`
  - Data access
- `backend/src/workers`
  - Queue processing
- `backend/src/video_utils.py`
  - Clip rendering pipeline
- `backend/src/ai.py`
  - LLM prompt and validation logic

### Layering guideline

When possible:

- keep HTTP concerns in route modules
- keep orchestration in services
- keep SQL and persistence in repositories

## Database Notes

The primary database bootstrap file is:

- `init.sql`

It defines:

- users
- sessions
- auth support tables
- tasks
- sources
- generated clips
- processing cache
- Stripe webhook tracking

The frontend also uses Prisma for auth and admin-related access patterns.

## Common Development Workflows

### Modify clip selection behavior

Primary files:

- `backend/src/ai.py`
- `backend/src/services/video_service.py`

Use this area when changing:

- segment selection rules
- LLM prompts
- output validation
- clip count heuristics

### Modify rendering or subtitle behavior

Primary files:

- `backend/src/video_utils.py`
- `backend/src/caption_templates.py`
- `backend/src/clip_editor.py`

Use this area when changing:

- subtitle layout
- font rendering
- cropping
- export presets
- clip edits after generation

### Modify task orchestration

Primary files:

- `backend/src/api/routes/tasks.py`
- `backend/src/services/task_service.py`
- `backend/src/workers/tasks.py`
- `backend/src/workers/job_queue.py`

Use this area when changing:

- status transitions
- background job behavior
- cancellation and resume logic
- progress reporting

### Modify uploads, fonts, transitions, or media listings

Primary files:

- `backend/src/api/routes/media.py`
- `backend/src/font_registry.py`
- `backend/fonts/`
- `backend/transitions/`

### Modify auth or user roles

Primary files:

- `frontend/src/lib/auth.ts`
- `frontend/src/app/api/auth/[...all]/route.ts`
- `init.sql`

### Modify billing behavior

Primary files:

- `frontend/src/app/api/billing/*`
- `frontend/src/lib/stripe.ts`
- `backend/src/api/routes/billing.py`
- `backend/src/services/billing_service.py`
- `backend/src/services/subscription_email_service.py`

### Modify the admin dashboard

Primary files:

- `frontend/src/app/admin/page.tsx`
- `backend/src/api/routes/admin.py`

## Testing and Verification

The repository now uses a three-layer automated test setup:

- backend `pytest` for unit and integration coverage
- frontend `Vitest` plus Testing Library for route handlers and client UI
- frontend `Playwright` for seeded browser smoke tests against real frontend and backend processes

Primary repo-level commands:

```bash
make test
make test-backend
make test-frontend
make test-e2e
make test-ci
```

Direct app-level commands:

```bash
cd backend && uv sync --all-groups && .venv/bin/pytest
cd frontend && npm install && npm run test:coverage
cd frontend && npm run test:e2e
```

### Local Test Environment

- Start PostgreSQL and Redis locally before running integration or e2e flows.
- `docker-compose up -d postgres redis` is enough for backend and frontend test runs.
- `docker-compose up -d` is the simplest full-stack option when you also want manual smoke testing.

Useful backend test env vars:

```bash
DATABASE_URL=postgresql+asyncpg://localhost:5432/supoclip
TEST_DATABASE_URL=postgresql+asyncpg://localhost:5432/supoclip
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
BACKEND_AUTH_SECRET=supoclip_test_secret
BETTER_AUTH_SECRET=supoclip_better_auth_test_secret
```

### Coverage and CI

- Backend coverage thresholds are enforced during `pytest`.
- Frontend coverage thresholds are enforced during `npm run test:coverage`.
- GitHub Actions runs separate `backend`, `frontend`, and `e2e` jobs with Postgres and Redis service containers.
- Playwright failures retain traces, screenshots, and videos for debugging.

### Recommended Manual Smoke Test

Automated tests cover the main seams, but manual smoke testing is still useful for high-risk media flows:

1. Start the stack.
2. Sign in.
3. Create a task from a YouTube URL.
4. Confirm progress updates arrive.
5. Confirm clips are generated.
6. Confirm clip editing and export actions still work.

## Helpful Logs

```bash
docker-compose logs -f backend
docker-compose logs -f worker
docker-compose logs -f frontend
docker-compose logs -f postgres
docker-compose logs -f redis
```

## Codebase Conventions

### Backend

- Python 3.11+
- 4-space indentation
- Prefer type hints where practical
- `snake_case` naming

### Frontend

- TypeScript and React
- 2-space indentation
- `PascalCase` components
- `camelCase` variables and functions
- Use `@/*` imports where practical

## Safe Defaults for New Work

- Prefer `backend/src/main_refactored.py` over `main.py`
- Keep auth-sensitive browser requests behind frontend API routes
- Preserve async behavior by keeping blocking work out of FastAPI request handlers
- Use the worker for long-running media processing

## Feature Implementation Notes (Gotchas)

Non-obvious behavior per feature, moved here from CLAUDE.md to keep that file
scannable. CLAUDE.md's "Common pitfalls" has a one-line summary of each;
read the matching entry below before touching that code.

- **Runtime settings must always show their current effective value.** `/admin/runtime-settings` (`src/api/routes/admin.py::_setting_status`) returns a `current_value` field for every non-`password` setting (decrypted admin value or the env fallback); the frontend (`RuntimeSettingsForm`) renders it next to the label and in the input's placeholder/default option. Password-type settings intentionally never expose their value. When adding a new runtime setting, keep this contract — never hide a non-secret value behind a generic "configured"/"unset" placeholder.
- **Caption/hook font sizing scales off the shorter frame dimension**, not just width (`get_scaled_font_size(base, width, height)` in `video_utils.py`). Scaling by width alone made captions balloon on wide outputs (16:9, 1:1) relative to their shorter height and pushed them past the safe area. `get_safe_vertical_position` treats its return value as the vertical **center** of the text block (matching the ASS `Alignment 5` + `\pos` renderer), not a top-left corner — keep that anchor convention consistent if you touch subtitle positioning.
- **`max_clips`/`target_duration_seconds` are per-request overrides**, threaded end-to-end: `api/routes/tasks.py::create_task` → `enqueue_processing_job` → `workers/tasks.py::process_video_task` → `TaskService.process_task` → `VideoService.process_video_complete` → `VideoService.analyze_transcript`, falling back to the global `MAX_CLIPS`/`CLIP_DURATION` config when unset. `POST /tasks/{id}/resume` must forward every one of these (plus `hook_style`/`social_overlay`) from the saved `task_source:{id}` metadata — it silently dropped them before; don't reintroduce that gap when touching resume.
- **Progress SSE payloads carry a `stage` field** (`download`/`transcribe`/`analyze`/`render`/`policy_check`/`metadata`/`complete`, set in `VideoService.process_video_complete`'s and `TaskService.process_task`'s progress-callback calls) alongside the existing `progress`/`message`/`status`. There's also a `clip_progress` event (`ProgressTracker.clip_started`, distinct from `clip_ready`) fired before each clip starts rendering, so the frontend can show "rendering clip i/N" before it's done. The frontend falls back to guessing a stage from the percentage when `stage` is absent (older cached events) — keep both in sync if you add a new stage. The SSE endpoint's initial `status` event (`api/routes/tasks.py::get_task_progress_sse`) reads the last cached Redis progress snapshot (`ProgressTracker.get()`) rather than the `tasks` DB row, since the DB never persists `stage` — a page reopened mid-processing needs the real stage immediately, not a percentage-based guess. The frontend SSE client (`tasks/[id]/page.tsx`) reconnects with exponential backoff (1s→16s, 5 attempts) on a native connection error instead of freezing the bar; only a real server-sent error payload is treated as fatal. On task error/cancel, the last real progress percentage is frozen rather than reset to 0 (`TaskService.process_task`'s `last_progress` closure variable) — 0 reads as "nothing happened" even after a late-pipeline failure.
- **Frontend auto-save pattern**: `useDebouncedEffect` (`frontend/src/lib/use-debounced-effect.ts`) debounces a save call after state settles, skipping the first render. When the watched state is also refreshed from the server (e.g. `fetchTaskStatus` reloading project settings), guard against re-saving unchanged data with a "last saved snapshot" ref comparison — see `tasks/[id]/page.tsx`'s `lastSavedProjectSettingsRef` for the pattern. On that page, auto-save persists settings cheaply (`apply_to_existing: false`) while the expensive "regenerate every clip" action stays an explicit button click.
- **Clip cleanup (pause/filler removal) has a 0-100 `sensitivity` slider** (`clip_cleanup.py::normalize_clip_cleanup_settings`) that's the primary control when present: 0 disables cleanup, higher values lower the pause threshold and widen the filler-word list. Omitting it preserves the legacy explicit `cut_long_pauses`/`pause_threshold_ms`/`remove_filler_words` behavior for backward compatibility. `pause_threshold_ms` is clamped to **[600ms, 3000ms]** (`normalize_pause_threshold_ms`); the sensitivity slider maps to that same range, topping out at **600ms** at max sensitivity (`_SENSITIVITY_PAUSE_THRESHOLD_MS_AT_MAX`) — raised from an earlier 300ms floor that fell inside the range of ordinary inter-word gaps in natural speech and was misclassifying continuous speech as pauses. Pause-gap removal in `video_utils.py::build_clip_keep_ranges` also requires a sentence/phrase boundary before cutting a gap below 1.2s (`_pause_gap_is_safe_to_cut`: the word before the gap must end in `.`/`!`/`?`/`…`/`,`, or the gap must be ≥1.2s of obvious dead air) — a raw gap-length threshold alone (the old behavior) cut mid-sentence on ordinary breathing gaps at high sensitivity. Filler-word removal never cuts a match within 0.75s of the clip end, a sentence-final word, or one adjacent to `!`/`?` — don't reintroduce context-free literal matching there. Crossfade blending between stitched cuts uses a *per-junction* fade (`crossfade_fades_for_ranges`), not a single global one — every junction should dissolve smoothly regardless of segment count or a short neighboring fragment; anything consuming `crossfade_fade_for_ranges` for per-word/per-junction timing should use the plural per-junction variant instead.
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
- **Content policy detection is regex-first, LLM-optional.** `content_policy.py`'s word-boundary regex engine (`scan_text`/`DEFAULT_WORD_LISTS`, per-category `severe`/`borderline` tiers) is the only detector that runs by default; the optional Ollama borderline-phrase check (`ollama_borderline_check`, off by default, one call per video) only supplements it. Flags are cached per-clip on `generated_clips.content_policy_flags` (TEXT-JSON, same convention as `reactions`/`hook_title_variants`) so re-opening the editor never re-scans — a manual "Rescan" (`POST /tasks/{id}/clips/{id}/content-policy/rescan`) exists for after a word-list edit. Matched words are asterisked preserving **both** the first and last letter (`rewrite_flagged` — the spec's own worked example, `"cocaine" → "c*****e"`, does more than its prose "preserve first letter" alone implies); audio is never touched.
- **Metadata (title/description/tags) is generated once per video, not per clip, and auto-generates by default.** `metadata_generation.py::generate_metadata_for_video` sends every clip's transcript excerpt (plus 3 few-shot examples in `METADATA_SYSTEM_PROMPT`) in a single prompt and maps the response back by `clip_index`; a separate, lighter `generate_metadata_for_single_clip` exists only for the per-clip "Regenerate" button, so that action doesn't re-run the whole video's batch. `TaskService.process_task` calls the batch generator automatically at 98% progress (stage `metadata`), right after the content-policy scan, gated by the `AUTO_GENERATE_METADATA_ENABLED` runtime setting (Settings → LLM Provider; default **on** — off falls back to manual-only via the "Regenerate Metadata" button). Results cache on `generated_clips.metadata_*` columns (never regenerated on render/preview/re-open) alongside `metadata_provider`/`metadata_generated_at`/`metadata_generation_ms` for the UI's provider badge and timing display, plus `metadata_source_text` — a snapshot of the transcript text metadata was generated from, used to compute a `metadata_stale` flag (`ClipRepository._is_metadata_stale`) by comparing it against the clip's current `text`. Deliberately **not** based on the `updated_at` column: a table-wide trigger (`update_generated_clips_updated_at`) bumps `updated_at` on every write to the row (reactions, content-policy scans, metadata edits included), so it can't distinguish a real re-cut from an unrelated write.
- **Per-clip metadata is displayed and edited on `/tasks/[id]`**, reusing `components/editor/clip-metadata-panel.tsx::ClipMetadataPanel` (previously wired only into the `/tasks/[id]/edit` route) inside each clip's detail card. It shows a stale badge, per-field copy buttons (title/description/tags) plus "Copy all" (formatted `Title:\nDescription:\nTags:` block for YouTube paste), a quality dropdown (fast=`qwen2.5:3b-instruct`, balanced=`llama3.2:3b`, high=`qwen2.5:7b-instruct`, gemini=force Gemini — `ai.py::_QUALITY_OLLAMA_MODEL_OVERRIDES`, passed as `?quality=` on the per-clip regenerate route) before the Regenerate button, and a Save button for manual edits. The project page also shows an "X/Y clips have metadata" summary (plus a stale count) and an "Export All Metadata" button that downloads a `.txt` with every clip's title/description/tags in clip order — a pure client-side formatting of already-fetched clip data, no new endpoint.
- **User edits to generated metadata are never silently overwritten** — a pattern new to this codebase (the closest prior precedent, `TaskService.select_hook_variant()`, actually does the opposite: it unconditionally overwrites `hook_title`). Three booleans (`metadata_title_user_edited`/`metadata_description_user_edited`/`metadata_tags_user_edited`) are set by the manual-edit path (`PATCH /tasks/{id}/clips/{id}/metadata` → `ClipRepository.update_clip_metadata_fields`) and checked by the passive-regeneration path (`MetadataService.maybe_regenerate_on_transcript_change`), which skips any field whose flag is set. The explicit "Regenerate" button is a deliberate user action and always overwrites everything, resetting all three flags — don't confuse the two paths if you touch either.
- **Local LLM calls and video renders never overlap on the same GPU.** `workers/resource_locks.py` is this codebase's first lock/semaphore of any kind (confirmed via a full grep before adding it) — a Redis-backed counting semaphore (sorted-set, holder+expiry scored) rather than an in-process `asyncio.Semaphore`, since ARQ workers may run as more than one process. `ai.py::run_with_llm_fallback` acquires the shared `"llm"` and `"gpu"` slots (max 1 each) around every Ollama call; render call sites should acquire `"gpu"` too if you add new ones. Gemini calls skip the `"gpu"` slot (remote API, no local VRAM contention). Ollama's model_settings use `httpx.Timeout(300.0, connect=5.0)` — a flat single timeout would let a genuinely-unreachable host stall for as long as a real slow-but-connected generation, defeating the point of failing fast into the Gemini fallback.
- **Batch processing state lives in Postgres, not browser memory** (`batch_queues`/`batch_queue_items`) — this is what makes "resume after an app/backend restart" possible at all; `ResumeBatchPrompt` (mounted on the home screen) just checks `GET /batch-queue/incomplete` on load, no client-side persistence needed. `BatchQueueService.run_batch` (the ARQ job body, `process_batch_queue_task`) walks items with a plain sequential loop — real concurrency-1 comes from awaiting each item fully before starting the next, not a separate lock — and never re-raises a single item's failure, so the batch always reaches `completed` regardless of individual item outcomes. Presets reuse `project_templates` directly (`batch_queues.template_id`); there is no second "preset" concept.
- **Safe Zone Overlay is frontend-only and never touches the render.** Per-platform UI-coverage percentages (top/bottom/left/right, portrait 9:16 base) live in `frontend/src/lib/safe-zones.ts` (`PLATFORM_SAFE_ZONES`) — edit that one table to add a platform or tweak a value, nothing else needs to change. `SafeZoneOverlay` (`frontend/src/components/safe-zone-overlay.tsx`) draws it as a `pointer-events-none` SVG (viewBox `0 0 100 100`, so it scales with the preview) passed as the `overlay` prop into `DynamicVideoPlayer`; it never reaches ffmpeg or gets burned into an export. The toggle + platform `Select` live in the clipping project page's toolbar (`frontend/src/app/(clipping)/tasks/[id]/page.tsx`, next to "Export All Clips") — defaulting to whichever platform the current export preset targets (`platformForExportPreset`), or the user's global default (`getDefaultSafeZonePlatform`) — and persist per-project to `localStorage` via `frontend/src/lib/safe-zone-settings.ts` (no DB column; this is view state, not project state, and the app has no per-user backend to put it in anyway). "All" mode overlays every platform's dashed boundary plus their intersection (the actual common-safe area) as a solid line. DESIGN.md's locked 4-color palette has no amber/warning hue and bans opacity tricks outside one sanctioned scrim, so the overlay uses ink dashed hairlines for each platform's unsafe boundary and a teal solid hairline for the safe intersection — not the filled teal/amber zones a generic spec might suggest. `warnIfTextInUnsafeZone` (same page) does a rough band-overlap check (captions ~70-80% down, hook title ~0-15%) against the export preset's platform before a download/export and shows a non-blocking `toast.warning` — it never moves anything, the user decides.

## Related Reading

- [Architecture](./architecture.md)
- [API Reference](./api-reference.md)
- [Troubleshooting](./troubleshooting.md)
