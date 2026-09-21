# Architecture

This guide explains how SupoClip is structured and how a task moves through the system.

## High-Level System

SupoClip is a multi-service application built around asynchronous video processing.

```text
Browser
  -> Frontend (Next.js)
  -> Frontend API routes
  -> Backend (FastAPI)
  -> Redis queue
  -> Worker (ARQ)
  -> PostgreSQL and file storage
```

The key design choice is that task creation is fast, while clip generation runs out of band in a worker.

## Runtime Components

### Frontend

Location:

- `frontend/src/app`
- `frontend/src/components`
- `frontend/src/lib`

Responsibilities:

- Authentication UI
- Task creation UI
- Task list and clip editing UI
- Admin dashboard
- Billing UI and webhooks
- Server-side API proxies to the backend

Technology:

- Next.js 15
- React 19
- Tailwind CSS
- Better Auth
- Prisma

### Backend API

Location:

- `backend/src/main_refactored.py`
- `backend/src/api/routes`
- `backend/src/services`
- `backend/src/repositories`

Responsibilities:

- Accept and validate requests
- Create and update tasks
- Manage clip editing actions
- Serve fonts, transitions, upload endpoints, and clip files
- Expose progress streams
- Handle feedback, billing support, and admin flows

Technology:

- FastAPI
- Async request handling
- Repository and service layering

### Worker

Location:

- `backend/src/workers`

Responsibilities:

- Poll jobs from Redis
- Execute long-running video processing
- Publish progress updates
- Write final clip records back to PostgreSQL

Technology:

- ARQ
- Redis

### PostgreSQL

Primary responsibilities:

- Users and sessions
- Task records
- Source records
- Generated clip metadata
- Billing metadata
- Processing cache

Schema bootstrap lives in `init.sql`.

### Redis

Primary responsibilities:

- Background queue transport
- Real-time progress plumbing
- Operational coordination for task state

## Repository Structure

Current top-level layout:

- `backend/`
- `frontend/`
- `docker-compose.yml`
- `init.sql`
- `start.sh`

## Backend Architecture

The backend follows a layered pattern.

### Routes

Directory:

- `backend/src/api/routes`

Responsibilities:

- HTTP request parsing
- Route-level authorization
- Response formatting

Main route groups:

- `tasks.py`
- `media.py`
- `billing.py`
- `feedback.py`
- `admin.py`

### Services

Directory:

- `backend/src/services`

Responsibilities:

- Orchestration
- Business logic
- Coordinating repositories and processing modules

Important services:

- `task_service.py`
- `video_service.py`
- `billing_service.py`
- `subscription_email_service.py`

### Repositories

Directory:

- `backend/src/repositories`

Responsibilities:

- Direct database access
- Raw query execution
- Encapsulated persistence logic

Important repositories:

- `task_repository.py`
- `clip_repository.py`
- `source_repository.py`
- `cache_repository.py`

### Utility and domain modules

Important backend modules:

- `ai.py`
  - Prompting and structured LLM output
- `video_utils.py`
  - Rendering, cropping, and subtitle logic
- `clip_editor.py`
  - Post-generation clip edits and exports
- `caption_templates.py`
  - Available subtitle template definitions
- `broll.py`
  - Optional Pexels integration
- `font_registry.py`
  - Font discovery and registration
- `observability.py`
  - Metrics and timing helpers

## Frontend Architecture

The frontend uses the App Router and keeps most product pages client-driven.

### App pages

Key pages:

- `/`
- `/list`
- `/tasks/[id]`
- `/settings`

There are currently no `/sign-in`, `/sign-up`, or `/admin` pages in the frontend — those were hosted-mode/legacy concerns and have been removed. The app runs local-first by default (`REQUIRE_AUTH=false`), with no login screen at all.

### Frontend API routes

The frontend includes server routes under `frontend/src/app/api`. They serve several purposes:

- Attach session-based auth context
- Proxy requests to the backend
- Handle Stripe callbacks and webhooks
- Expose internal user preference and feedback endpoints

This separation lets the browser talk to the frontend domain while the frontend securely talks to the backend.

### Authentication

By default (`REQUIRE_AUTH=false`), there is no login at all: both frontend and backend resolve every request to a single implicit user (`LOCAL_USER_ID = "local"`). This is the local-first, self-hosted default.

Setting `REQUIRE_AUTH=true` on both frontend and backend restores real multi-tenant auth for hosted deployments:

- Better Auth with Prisma and PostgreSQL handles email/password login
- Additional user field `is_admin` is persisted
- Trusted origins are derived from app configuration
- Session cookies identify the current user, and frontend-to-backend requests are authenticated via HMAC-signed headers (`x-supoclip-user-id`, `x-supoclip-ts`, `x-supoclip-signature`) rather than raw cookies
- Programmatic clients (MCP server, API consumers) instead use a per-user API key (`Authorization: Bearer sk_...` or `x-api-key`)

## End-to-End Task Lifecycle

### 1. Task creation

The user submits a YouTube URL or upload from the frontend.

The backend:

- Validates the request
- Creates or links a source record
- Creates a task row
- Enqueues background work
- Returns quickly to the frontend

### 2. Queueing

The task enters a queue-backed state such as `queued`.

Redis carries the job definition to the worker.

### 3. Processing

The worker:

- Pulls the job
- Downloads or reads the source media
- Creates a transcript
- Runs AI analysis
- Generates clips
- Publishes progress updates

The task status becomes `processing`.

### 4. Completion

Once clip generation succeeds:

- Files are written to storage
- clip metadata is persisted in `generated_clips`
- the task status becomes `completed`
- the frontend refetches task and clip data

If anything fails:

- the task status becomes `error`
- resumable and diagnostic information is preserved where possible

## Video Processing Pipeline

The rough pipeline is:

1. Input acquisition
   - YouTube via Apify actor, with `yt-dlp` fallback
   - Uploaded file from the frontend
2. Transcription
   - AssemblyAI for word-level timestamps
3. Segment selection
   - LLM chooses promising short moments
4. Rendering
   - Video trimming and formatting
   - Subtitle placement and styling
   - Face-aware cropping
   - Optional transitions
   - Optional B-roll
5. Persistence
   - Clip metadata in PostgreSQL
   - media files in mounted storage

### Cropping and subtitles

The rendering path includes support for:

- Vertical output
- Face-centered cropping
- Subtitle overlays
- Caption templates
- Font customization

## Progress and Realtime Updates

The task detail page subscribes to progress using Server-Sent Events.

Backend route:

- `GET /tasks/{task_id}/progress`

The worker publishes progress updates during processing, and the frontend updates its UI without polling on every step.

## Data Model Overview

Important tables from `init.sql`:

### `users`

Stores:

- Auth identity
- Admin flag
- Default font preferences
- Billing plan and subscription fields

### `sources`

Stores:

- Source type
- Title
- Original URL when applicable

### `tasks`

Stores:

- User and source relationships
- Task status
- Progress percentage and message
- Font and caption settings
- B-roll setting
- Processing mode
- Timing and cache metadata

### `generated_clips`

Stores:

- File name and path
- Clip timing
- Selected text
- AI reasoning
- Virality and scoring breakdown

### `processing_cache`

Stores reusable processing artifacts to avoid repeating expensive work when possible.

### Better Auth tables

- `session`
- `account`
- `verification`

### Billing support

- `stripe_webhook_events`

## Multi-Tool Platform & Ranking Tool

### The tool shell

`frontend/src/tools/types.ts` defines the `Tool` shape every tool on the
platform matches: `id`, `name`, `icon`, `description`, plus either `href` +
`matchPaths` (a route-owning tool — one big/stateful enough to be its own
multi-page Next.js route subtree) or `mount(container)` (a self-contained
tool simple enough to render straight into a DOM node — see the reference
implementation still available, unregistered, at `frontend/src/tools/
placeholder/`). `frontend/src/tools/registry.ts` is a literal `Tool[]`
array — no plugin registry, no dynamic loading. `frontend/src/components/
tool-tabs.tsx` renders the tab bar from that array; a route-owning tool's
pages live under their own Next.js **route group** (`(clipping)/`,
`(rank)/`) so the shared layout in that group can render `<ToolTabs />`
above every page without repeating it per-page. Route groups don't
contribute a URL segment, so a second tool's pages need their own path
segment inside the group (`(rank)/rank/create/page.tsx` → `/rank/create`) —
see the Common Pitfalls entry in CLAUDE.md for what happens if you skip it.

### Ranking: N inputs → one compilation

Clipping is 1 source video → N output clips. Ranking inverts that: N
independent input videos → 1 output compilation video with a burned-in rank
number on each segment. This is different enough that it needed its own
data-model and pipeline pieces rather than reusing Clipping's as-is:

- **`tasks.task_type`** (`'clipping'` | `'ranking'`, migration
  `20260915_0001_ranking_tool_schema.sql`) is the discriminator that lets
  `/list`, trash, and soft-delete keep working unchanged for both project
  types — a ranking task simply has `source_id = NULL` (already nullable)
  and no row in `sources`.
- **`ranking_inputs`** (new table) holds the N input videos: `file_path`
  (an `upload://` reference, same convention as Clipping's uploaded
  sources, resolved via `VideoService.resolve_local_video_path`),
  `original_filename`, `duration_seconds`, `order_index` (drag order, 0-based),
  a nullable `rank_position` (a manual 1-based override — see
  "auto-number vs. manual rank" below), a nullable `folder_clip_id` (which
  folder-library clip this came from, if any — see "Folder library..."
  below), `rank_text` (this project's own copy of that rank's display
  text), and `framing` (`blur_fill`/`crop_fill`/`letterbox`, default
  `blur_fill`).
- **`tasks.ranking_settings`** (TEXT, JSON-encoded — same convention as
  `generated_clips.reactions`/`hook_title_variants`) holds
  `{template_id, number_overlay, export_preset, target_lufs}`.
- **The rendered output reuses `generated_clips`** as a single row
  (`clip_order = 0`) rather than a parallel output table — structurally
  it's the same thing Clipping already has (a rendered file tied to a
  task, with a duration), so `GET /tasks/{id}` returns a ranking task's
  compilation the same way it returns Clipping's clips, and the existing
  per-clip export endpoint (`GET /tasks/{id}/clips/{clip_id}/export?preset=`)
  works unchanged.

**Auto-number vs. manual rank**: `ranking_repository.py::list_inputs`
orders every input with an explicit `rank_position` first (sorted by that
value), then the remaining un-ranked inputs by `order_index` — a manual
rank always wins outright rather than competing numerically against
`order_index` (which is 0-based, while `rank_position` is a 1-based
user-facing number; naively `COALESCE`-ing the two sorts an unranked
item ahead of a manually-ranked one whenever their numbers happen to
collide). `RankingService._resolve_ranks` then computes the *displayed*
digit for each segment: the manual override if set, else its sequential
position in that already-resolved order.

**Render pipeline** (`backend/src/services/ranking_service.py::
process_ranking_complete`, stages `load → order → render → export →
complete` over the existing `ProgressTracker`/SSE channel, worker entry
point `process_ranking_task` in `workers/tasks.py`):

1. Load ordered inputs; a wide duration spread across inputs surfaces a
   progress-message notice, not an error.
2. Resolve the template (`ranking_templates.py` — folder-based, mirrors
   `caption_templates.py`'s registry *pattern* but as data files under
   `backend/templates/ranking/<name>/config.json`; only `rapid_fire` ships
   today) and the number-overlay config.
3. Concatenate all N inputs via a single ffmpeg `filter_complex` graph:
   each input is scaled/padded to the export preset's target dimensions
   independently (arbitrary resolutions/aspect ratios in, one consistent
   output), silent inputs get a generated `anullsrc` audio stream so the
   `concat` filter's audio output stays continuous, then a single ASS
   subtitle burn-in renders every segment's rank-number tile
   (`ranking_overlay.py::build_rank_number_ass` — one shared ASS style,
   timed dialogue events per segment). This concatenation-of-independent-
   files step is genuinely new: nothing in `video_utils.py`/`clip_editor.py`
   previously combined more than clip+broll or clip+transition pairs.
4. Encode via the existing `build_final_video_encode_args` (GPU/CPU) and
   `enforce_size_cap` — reused as-is. Loudness normalization is added as a
   filter-graph step (`[araw]loudnorm=...[afinal]`), **not** a `build_audio_output_args`
   `-af` flag, since `-af` and `-filter_complex` can't target the same
   output stream (ffmpeg errors "Simple and complex filtering cannot be
   used together") — see CLAUDE.md's Common Pitfalls.
5. Acquires the shared `"gpu"` resource slot (`workers/resource_locks.py`)
   around the render, same convention as the LLM call sites in `ai.py`.

**Templates beyond Rapid Fire** (`countdown`, `ranking_list` — both ship
today): `ranking_templates.py`'s `TEMPLATE_DEFAULTS` carries three fields
`RankingService.process_ranking_complete` actually dispatches on, not just
decorative metadata — `layout` (only `"full_screen"` is implemented;
`"head_to_head"`/`"tier_grid"` are reserved for the still-deferred
templates), `render_order` (`"ascending"` plays rank #1 first, e.g. Rapid
Fire; `"descending"` reverses *playback* order to worst-first while
`_resolve_ranks`'s displayed digits stay computed off the ascending order —
Countdown, Ranking List), and `list_overlay` (bool — an accumulating
corner list of every rank revealed so far, rendered by
`ranking_overlay.py::build_rank_list_ass` as one dialogue event per segment
re-listing everything revealed up to that point, positioned opposite the
main rank tile so the two never collide — Ranking List's differentiator
from Countdown, which uses the same reversed order but no list). Template
previews are served (not read directly from disk by the frontend) via
`GET /ranking/templates/{id}/preview`, unauthenticated like the MCP
server's "public discovery" endpoints — the raw filesystem `preview_path`
never leaves the backend.

**Rank-number tile rendering** (`ranking_overlay.py::build_rank_number_ass`,
used by the three tile-based templates): a plain ASS `BorderStyle=3`
"opaque box" per segment — the DESIGN.md-sanctioned "centered text in a
fixed square tile" exception — using only the locked 4-color palette
(ink/paper/teal tile, paper/ink digit for contrast). On this project's
libass build, `BorderStyle=3`'s box fill color comes from
**`OutlineColour`**, not the `BackColour` field the ASS spec describes —
verified directly, not assumed; see CLAUDE.md's Common Pitfalls before
changing this. `build_ranking_overlay_ass` (`ranking_classic`, see below)
reuses the same `OutlineColour`-as-fill technique but is not palette-locked
— gold for #1 is a deliberate, video-only exception.

**API** (`backend/src/api/routes/ranking.py`, proxied by the frontend's
generic `app/api/ranking/[...path]/route.ts` the same way `app/api/tasks/
[...path]/route.ts` proxies Clipping): `POST /ranking/tasks` (create),
`POST/GET/DELETE /ranking/tasks/{id}/inputs[/...]` (attach/list/remove —
upload itself reuses the existing generic `POST /upload`, unchanged),
`PATCH .../inputs/order` (drag reorder), `PATCH .../inputs/{id}/rank`
(manual override), `PATCH .../settings`, `POST .../render` (enqueues
`process_ranking_task` via the already-generic `JobQueue.
enqueue_processing_job` — no job-queue changes needed for a second job
type), `GET /ranking/templates`, `GET /ranking/export-presets` (the same
`EXPORT_PRESETS` Clipping uses — reused as-is, no ranking-specific
restriction). Folder library: `GET /ranking/folders` (list), `POST
/ranking/folders/scan` (multipart upload of a batch into a named folder),
`GET /ranking/folders/{id}/clips`, `POST /ranking/folders/{id}/select`
(random + prefer-unused pick), `GET /ranking/folders/clips/{clip_id}/file`
(stream one library clip for preview — a real endpoint, not a raw path
query param, so ownership is checked). Per-input text/framing:
`PATCH /ranking/tasks/{id}/inputs/{input_id}/text`
(also mirrors into that clip's library `saved_text`),
`PATCH .../inputs/{input_id}/framing`. Settings: `POST
/ranking/settings/sfx` (multipart SFX upload). The folder-scan and
SFX-upload endpoints are multipart, so the frontend gives them their own
dedicated Next.js routes (`app/api/ranking/folders/scan/route.ts`,
`app/api/ranking/settings/sfx/route.ts`) instead of going through the
generic `[...path]` proxy — that proxy reads the request body with
`.text()`, which corrupts binary multipart bodies.

**Create flow** (`(rank)/rank/create/page.tsx`): a three-step client-side
wizard — pick/build a folder, auto-select + swap 5 clips, then per-rank
text — rather than separate routes, since none of the intermediate state is
persisted server-side until the final submit (create task → attach 5
inputs with `folder_clip_id`/`rank_text`/`framing` → set `ranking_classic`
settings → render). See "Folder library..." below for the folder step.

**Project duplication** (`POST /ranking/tasks/{id}/duplicate`,
`ranking_repository.py::duplicate_inputs`): clones a ranking project's
inputs (a cheap row copy — `file_path` is already an `upload://`
reference, nothing re-uploads) and `ranking_settings` into a new draft
task; doesn't render on its own — the frontend follows up with the normal
`POST .../render` call, same two-step shape project creation already uses.
Surfaced on the task page both generally ("Duplicate", next to Export) and
specifically as "Duplicate & try again" on a failed render, since re-running
the same lineup is the main reason to duplicate rather than re-upload.

**Transition SFX** (a template's `transition_sfx` field — Countdown ships
with `whoosh-sfx-1.mp3`, the one asset in `backend/sfx/`): mixed into the
existing single ffmpeg command, not as a `mix_sfx_into_clip` post-pass
(`video_utils.py`'s convention for hook-title SFX) — a post-pass would mean
one full re-encode per cut boundary instead of one for the whole
compilation. Instead, the sfx file is added as one extra `-i` per boundary
(same short file, decoded independently each time — cheap), each copy
delayed to its own cut point with `adelay`, then folded into `araw` via
`amix` before the loudnorm step. `find_sfx_path` resolving to `None` (bad
name, missing file) degrades silently to no sfx rather than failing the
render. The *visual* half of "configurable transitions" — an actual
crossfade/wipe between segments (`xfade`/`acrossfade` instead of `concat`)
— stays deferred below; it changes the segment-timing math the rank-tile
and list-overlay dialogue events are computed from (`cumulative` in
`_render_compilation`), so it isn't purely additive the way the audio SFX
was.

**Background music + auto-ducking** (a template's `background_music`
field, `backend/music/` — ships empty by design, same licensing rationale
as `backend/sfx/README.md`; no shipped template sets it, since there's no
bundled track to point at): looped with `-stream_loop -1` so any
compilation length works, trimmed to the render's total duration, then
ducked under the dialogue/sfx track with ffmpeg's `sidechaincompress`
rather than hand-authored per-clip volume keyframes. The one non-obvious
bit, found by actually running the filter graph rather than assuming it:
a filtergraph label produced by another filter (unlike a raw `[N:a]` input
pad) can only feed *one* downstream filter, so the dialogue/sfx track has
to `asplit` into two copies — one as the sidechain's key input, one for
the final `amix` — or ffmpeg errors "Invalid stream specifier" trying to
consume the same internal label twice.

**Folder library, text memory, and the `ranking_classic` template**
(`ranking_folder_repository.py`, `ranking_overlay.py::build_ranking_overlay_ass`):
a "folder" is a user-named batch of clips picked via the browser (a
directory picker or a multi-file drop — there's no server filesystem path
to scan, `POST /ranking/folders/scan` uploads are the only way a clip
reaches the backend), stored as `ranking_folders`/`ranking_folder_clips`
(migration `20260915_0002_ranking_clip_library.sql`), separate from
`ranking_inputs` — the folder tables are the persistent *pool* a project's 5
attached inputs get selected/swapped from. Clips are keyed by
`(folder_id, content_hash)` so re-adding the same file resolves to the
existing row instead of duplicating it and losing its `use_count`/
`saved_text`. **Prefer-unused selection** (`select_clips`) groups the
folder's clips by `use_count` ascending and fills the 5-clip pick from the
lowest-use group first (shuffled within each group), only reaching into
more-used clips once the unused ones run out. **Text memory** is
`ranking_folder_clips.saved_text`; a ranking project's own
`ranking_inputs.rank_text` is a separate, independently-editable copy
pre-filled from it — `RankingService.process_ranking_complete` writes
`rank_text` back into `saved_text` (and bumps `use_count`/`last_used_at`)
when a ranking using that clip actually renders, so text and usage only
count once a project is real, not just drafted.

The `ranking_classic` template is the folder-workflow's default and is
where the rest of the original spec's rendering requirements live, gated
behind two config fields the three tile-based templates leave at their
defaults so nothing about them changes: `number_overlay.style: "stacked"`
dispatches `_render_compilation` to `build_ranking_overlay_ass` instead of
`build_rank_number_ass` — every rank's number tile is one ASS dialogue
event spanning the *entire* video (not one event per segment), stacked
5-at-top to 1-at-bottom on the left, while each rank's text is a second,
separate dialogue event that only starts at that rank's segment start and
runs to the end — "always visible" numbers and "reveals then persists"
text are consequently two different event lifetimes, not one. Rank #1
renders in gold (`#FFD700`) — the one deliberate, documented exception to
the site's locked 4-color palette, which only ever applied to site UI, not
rendered video (see DECISIONS.md). Both the tile and its text get a ~500ms
overshoot-then-settle `\t` scale transform timed to that rank's reveal.
`use_global_sfx: true` swaps the SFX source from a template-named file to
`Config.ranking_sfx_filename` (Settings → Ranking), applies
`Config.ranking_sfx_offset_pct` (SFX starts that % of its own length before
each cut, instead of exactly on it), and appends one further SFX instance
timed so its tail lands exactly at `total_duration` — a clean loop point
with no black frame or trailing silence, per the original spec's "final
SFX must end exactly when the video ends."

**Per-clip framing** (`ranking_inputs.framing`, `RankingService._framing_filter`)
is resolved for every template, not just `ranking_classic`, since it's a
tool-wide default (`Config.ranking_default_framing`) rather than a
template concern: `blur_fill` (the default) keeps the source clip's full
frame via `scale=increase,crop,setsar` and overlays it on a blurred/scaled
copy of itself filling the rest of the 9:16 frame, `crop_fill` is a plain
center-crop, `letterbox` is the original scale+pad-to-fit behavior (the
only option that existed before per-clip framing). `blur_fill` over
`crop_fill` as the default matters here specifically because ranking
source clips are often chaotically-framed (fails/highlights compilations)
where a center-crop would cut the actual subject out of frame.

**Deferred** (see the original spec if picking this back up): the two
templates needing a structurally different filter graph and new
`ranking_inputs` data rather than just a new config.json — Head-to-Head
(split-screen per pair, needs pairing logic + likely a mixed/winner-only
audio decision) and Tier List (needs a new `tier` column + tier-assignment
UI, and a grouped-by-tier render rather than one clip per segment) — plus
a real visual crossfade/wipe transition (see the Transition SFX note
above). Everything else from the original deferred list now ships:
`rapid_fire`/`countdown`/`ranking_list`/`ranking_classic`, project
duplication, transition SFX (including the offset + end-aligned variant),
background music with auto-ducking, the folder library with prefer-unused
selection and text memory, and per-clip framing (all above). The
template-loader/render-pipeline/data-model are built to make Head-to-Head/
Tier List additive rather than requiring rework.

## Storage Model

In Docker, the system uses named volumes for:

- uploads
- clips
- Redis data
- PostgreSQL data
- YouTube auth state

Fonts and transitions are file-based assets mounted from the repository.

## Operational Characteristics

### Why the worker matters

Without the worker, tasks may be created successfully but never progress beyond `queued`.

### Why Redis matters

Redis is required for:

- ARQ queue delivery
- progress messaging
- coordination around task processing

### Why FastAPI docs matter

The backend exposes interactive docs at `/docs`, which is helpful for inspecting available endpoints outside the frontend.

## Legacy and Active Entry Points

The active backend entry point is:

- `backend/src/main_refactored.py`

The legacy monolithic file still exists:

- `backend/src/main.py`

For new work, use the refactored entry point and layered route structure.

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

## Related Reading

- [App Guide](./app-guide.md)
- [API Reference](./api-reference.md)
- [Development](./development.md)
- [Troubleshooting](./troubleshooting.md)
