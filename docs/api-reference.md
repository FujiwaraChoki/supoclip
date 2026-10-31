# API Reference

This is a practical reference for the API surface used by SupoClip. It combines the frontend proxy routes and the backend routes they rely on.

For exact schemas and interactive testing, also use FastAPI docs at `http://localhost:8000/docs`.

## Authenticating with API keys

Agents and scripts authenticate with a personal API key. Create one in the app under **Agents & API** (`/settings/api-keys`); the plaintext `sk_...` key is shown exactly once, and only its SHA-256 hash is stored.

Creating a key requires a verified email address. Use **Send verification email**
on that page, follow the one-hour link, then refresh your verification status.
The backend returns `403` before minting a key or sending its notification if
the database user is unverified. Existing keys, listing, revocation and ordinary
sign-in continue to work. Self-hosted installations also need SES configured to
deliver verification links; existing users are not automatically marked verified.

Send it to the backend (hosted: `https://api.supoclip.com`, self-hosted: your backend URL) with either header:

```bash
curl https://api.supoclip.com/tasks/ \
  -H "Authorization: Bearer sk_..."

curl https://api.supoclip.com/tasks/ \
  -H "x-api-key: sk_..."
```

API keys authenticate `/tasks/*`, `/fonts` and `/upload`. Revoking a key in the app takes effect immediately. For AI agents (Claude, Cursor and other MCP clients), use the [SupoClip MCP server](../mcp/README.md), which wraps this API.

## API Layers

SupoClip has two relevant layers:

- Frontend API routes in `frontend/src/app/api`
  - These are what the browser usually calls
- Backend FastAPI routes in `backend/src/api/routes`
  - These perform the real work

## Frontend API Routes

These routes generally attach session context and then proxy or orchestrate backend access.

### Authentication

- `GET/POST /api/auth/[...all]`
  - Better Auth handler

### Tasks

- `GET /api/tasks`
  - Fetch current user tasks
- `GET /api/tasks/billing-summary`
  - Fetch billing and usage summary
- `POST /api/tasks/create`
  - Create a task
- `GET|POST|PATCH|DELETE /api/tasks/[...path]`
  - Catch-all proxy for task operations such as clips, exports, resume, cancel, and settings

### Uploads and media

- `POST /api/upload`
  - Upload a source video
- `GET /api/fonts`
  - Fetch available fonts
- `GET /api/fonts/[fontName]`
  - Fetch an individual font file
- `POST /api/fonts/upload`
  - Upload a custom font

### User preferences and feedback

- `GET/PATCH /api/preferences`
  - Read and update default subtitle styling and clip length preferences (`clipLength`: `auto`, `short`, `medium`, `long`)
- `POST /api/feedback`
  - Submit product feedback

### Billing

- `POST /api/billing/checkout`
  - Open Stripe checkout
- `POST /api/billing/portal`
  - Open Stripe customer portal
- `POST /api/billing/webhook`
  - Stripe webhook receiver

### Social publishing

- `GET /api/social/connect/[provider]`
  - Starts the OAuth handshake and redirects the browser to the platform
- `GET /api/social/callback/[provider]`
  - OAuth redirect target; completes the connection and returns to `/settings/social`
- `GET /api/social/media/[token]`
  - Public, token-gated clip stream used by Instagram to fetch a video
- `GET|POST|DELETE /api/social/[...path]`
  - Authenticated proxy for the backend `/social/*` routes

### Admin

- `GET /admin`
  - Admin dashboard page

## Backend Route Groups

## Task Routes

Source file:

- `backend/src/api/routes/tasks.py`

### Task lifecycle

- `GET /`
  - List tasks
- `POST /`
  - Create task
- `GET /billing/summary`
  - Billing summary for current user
- `GET /workflows`
  - What the current user can make (see [Workflows](#workflows))
- `GET /{task_id}`
  - Task detail
- `GET /{task_id}/progress`
  - Server-Sent Events progress stream
- `PATCH /{task_id}`
  - Update task metadata
- `DELETE /{task_id}`
  - Delete a task
- `POST /{task_id}/cancel`
  - Cancel an active task
- `POST /{task_id}/resume`
  - Resume a cancelled or errored task

Task creation accepts an optional `clip_length`: `auto` (default, 15–60 seconds),
`short` (15–30), `medium` (30–60), or `long` (60–120). Invalid values return 422
before a task is created or minutes are charged. Example body:

```json
{"source": {"url": "https://www.youtube.com/watch?v=VIDEO_ID"}, "clip_length": "long"}
```

The preset controls AI selection and the render cap, and is kept for resume and
styling regeneration. Pause and filler removal may shorten the result; a source
shorter than the requested duration can only produce a shorter clip. Existing
clients that omit the field keep Auto behavior. The web app passes the account's
saved default explicitly; API and MCP callers should set their preferred preset.

Deploy the `default_clip_length` user migration (through either migration runner)
before the frontend. Ship the API and worker together so the worker accepts the
new optional job argument. No new environment variables are required.

### Workflows

A task makes one of five things. Omit `workflow` (or send `"highlights"`) for
the original behavior: without instructions, highlights are selected and
rendered exactly as before workflows existed.

| `workflow` | Makes | `clip_length` means |
|---|---|---|
| `highlights` | The best standalone clips | Each clip (15–60 s on auto) |
| `series` | Part 1..N of one story, in order, cut on shared boundaries | Each part: auto 45–150 s, short 30–60, medium 45–90, long 90–175 |
| `topic` | Every strong standalone moment about `workflow_options.topic` | Each clip, as for highlights |
| `recap` | One clip covering the main points in order | Total: auto scales with the video (45–120 s), short ~45, medium ~75, long ~120 |
| `trailer` | One short teaser that never reveals the payoff | Total: auto ~30, short ~20, medium ~40, long ~60 |

New optional fields on `POST /tasks/`:

- `workflow`: one of the five ids. Any other value is rejected, never coerced.
- `workflow_options`: an object. `topic` (topic only): what to look for, 2–120
  characters with at least one letter or digit; a single Han, kana or Hangul
  character is a whole word and is accepted. `part_count` (series only):
  `"auto"`, `null` or 2–10. Options that do not apply to the workflow are
  dropped. `workflow_options.instructions` is also accepted.
- `instructions`: free-text editorial guidance for any workflow, highlights
  included, up to 500 characters. It wins over `workflow_options.instructions`
  when both are set.

Topic and instructions are normalized before they are measured or stored: line
breaks unified, control characters, lone surrogates and invisible characters
(soft hyphen, zero-width space, word joiner, BOM, bidi controls) removed,
spaces collapsed. Topics are one line; instructions keep at most one blank line.

```jsonc
{"source": {"url": "https://youtu.be/VIDEO_ID"}, "workflow": "series", "workflow_options": {"part_count": "auto"}, "clip_length": "medium"}
{"source": {"url": "https://youtu.be/VIDEO_ID"}, "workflow": "topic", "workflow_options": {"topic": "pricing"}}
{"source": {"url": "https://youtu.be/VIDEO_ID"}, "workflow": "recap", "clip_length": "short", "instructions": "Focus on the money advice"}
{"source": {"url": "https://youtu.be/VIDEO_ID"}, "workflow": "trailer"}
{"source": {"url": "https://youtu.be/VIDEO_ID"}, "instructions": "Skip the sponsor read"}
```

Rejections happen before a task exists or minutes are charged. `detail` is
`{"code", "message"}` plus the fields listed:

| Status | `code` | When |
|---|---|---|
| 422 | `INVALID_WORKFLOW` | `workflow` is not one of the five ids |
| 422 | `INVALID_WORKFLOW_OPTIONS` | `workflow_options` is not an object |
| 422 | `TOPIC_REQUIRED` | topic workflow without a usable topic (empty, punctuation only, or one non-CJK character) |
| 422 | `TOPIC_TOO_LONG` | topic over 120 characters |
| 422 | `INVALID_PART_COUNT` | series `part_count` other than `"auto"`, `null` or 2–10 |
| 422 | `INSTRUCTIONS_TOO_LONG` | instructions over 500 characters after normalization |
| 403 | `WORKFLOW_NOT_AVAILABLE` | a workflow other than highlights, or instructions, outside the `WORKFLOWS_ACCESS` gate |
| 422 | `SOURCE_TOO_SHORT_FOR_SERIES` | source under 2 minutes; `min_source_seconds`. With an explicit part count that fits fewer than two parts of the chosen length, `min_source_seconds` is what two parts need (225 for long parts) |
| 422 | `TOO_MANY_PARTS` | the explicit part count does not fit; `max_parts` is the most that does |
| 422 | `SOURCE_TOO_SHORT_FOR_RECAP` | source under 3 minutes; `min_source_seconds` |
| 422 | `SOURCE_TOO_SHORT_FOR_TRAILER` | source under 3 minutes; `min_source_seconds` |
| 422 | `SOURCE_TOO_SHORT_FOR_TOPIC` | source under 1 minute; `min_source_seconds` |

`GET /tasks/workflows` reports the gate and limits for the caller:

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

`enabled: false` means only highlights without instructions.

Responses:

- `GET /tasks/{id}` adds `workflow`, `workflow_options` (normalized, including
  `instructions`), `workflow_plan` (the stored plan: title, parts, points,
  beats, topic coverage, notes, and `error` after a workflow failure),
  `source_duration_seconds` and `share_enabled`. `error_code` holds the
  worker code below when a workflow fails.
- Clips (`GET /tasks/{id}`, `GET /tasks/{id}/clips` and the SSE `clip_ready`
  event) add `workflow_role` (`part`, `topic`, `recap`, `trailer`, or null for
  highlights), `part_number` (series) and `workflow_meta` (`{}` for
  highlights; planned ranges plus the part's title, ending and post caption,
  the topic relevance and why, or a recap's points and a trailer's beats with
  their offsets into the clip).
- `GET /tasks/` adds `workflow`, `workflow_title` and `part_count`. Series,
  recap and trailer covers are their first clip.
- Shared results (`GET /tasks/shared/{share_token}`) add `workflow`, a public
  `workflow_plan` (`version`, `workflow`, `title`, `parts[].{part_number,title}`,
  `points[].label`, `beats[].role`, `topic.{query,canonical,coverage,found}`,
  `notes[].{code,params}`) and, per clip, `part_number`, `workflow_role` and
  `workflow_meta.{title, ends_on, points, beats}`. Options, instructions,
  ranges, captions and errors are never shared.

A workflow that cannot produce what it promised fails the task loudly instead
of returning filler. The task ends `error` with no clips (so the minutes are
refunded automatically), `progress_message` explains it in English, and
`error_code` is one of:

| `error_code` | Meaning | Suggested next step |
|---|---|---|
| `topic_not_found` | The topic is only mentioned in passing. `workflow_plan.error.details` has `suggested_topics` and `mentions` (MM:SS) | Retry with a suggested topic, or highlights |
| `source_too_short` | Not enough story for two parts | Highlights |
| `no_story` | No storyline runs long enough for a series | Recap |
| `recap_unsupported` | Not enough distinct points | Highlights |
| `trailer_unbuildable` | Every trailer would give away the ending | Recap |
| `analysis_incomplete` | Part of the long-video analysis failed | Resume |
| `plan_failed` | Planning failed or exceeded `WORKFLOW_PLAN_TIMEOUT_SECONDS` | Resume |
| `render_incomplete` | A required part, recap or trailer failed to render | Resume |
| `transcript_untimed` | The transcript has no timings to cut on | Upload the file instead |

A cancelled series keeps the parts already rendered; resuming re-renders from
the cached plan. Resume needs no extra fields for any workflow: the worker reads
`workflow` and `workflow_options` from the task.

### Clip operations

- `GET /{task_id}/clips`
  - List generated clips
- `DELETE /{task_id}/clips/{clip_id}`
  - Delete a clip
- `PATCH /{task_id}/clips/{clip_id}`
  - Edit a clip
- `POST /{task_id}/clips/{clip_id}/split`
  - Split a clip
- `POST /{task_id}/clips/merge`
  - Merge clips
- `PATCH /{task_id}/clips/{clip_id}/captions`
  - Update caption text or related settings
- `POST /{task_id}/clips/{clip_id}/regenerate`
  - Re-render a clip
- `GET /{task_id}/clips/{clip_id}/export`
  - Export using a platform preset. The response waits until ffmpeg finishes. Older clients, including current iOS builds, keep using this route.
- `POST /{task_id}/clips/{clip_id}/exports`
  - Queue the same preset export and return immediately. Body `{"preset": "tiktok"|"reels"|"shorts"}` or query `preset` (default `tiktok`). Same clip, preset, and source file reuses a queued, running, or succeeded job. A queued job stays queued while it waits behind other work. It is failed if its worker job disappears, or after 60 minutes. A running job is failed when the worker stops heartbeating for 3 minutes.
- `GET /{task_id}/clips/{clip_id}/exports/{export_id}`
  - Job state: `queued`, `running`, `succeeded`, or `failed`, plus `progress`, `error`, and `download_url` when the file is ready.
- `GET /{task_id}/clips/{clip_id}/exports/{export_id}/file`
  - Download a finished export. Redirects to a presigned S3 URL when object storage is on.

The queued export routes need the `clip_exports` table (created when the API process runs its startup migrations) and a worker restarted afterwards so it registers `export_clip_preset`. Ship the worker with the API, then the frontend. The synchronous GET route does not need the new table.

### Sharing

- `POST /{task_id}/share` / `DELETE /{task_id}/share`
  - Create (or re-enable) and revoke the read-only share link of a completed task
- `GET /shared/{share_token}`
  - Public task and clip subset (see [Workflows](#workflows) for the workflow fields)
- `GET /shared/{share_token}/clips/{clip_id}/file`
  - Public clip file

### Task-wide settings and diagnostics

- `POST /{task_id}/settings`
  - Apply project-wide task settings such as fonts or caption template
- `GET /metrics/performance`
  - View aggregate performance metrics
- `GET /dead-letter/list`
  - Inspect dead-letter items or failed job artifacts

## Media Routes

Source file:

- `backend/src/api/routes/media.py`

### Fonts

- `GET /fonts`
  - List available fonts
- `GET /fonts/{font_name}`
  - Download or stream a font file
- `POST /fonts/upload`
  - Upload a font

### Other media assets

- `GET /transitions`
  - List available transitions
- `GET /caption-templates`
  - List available subtitle template definitions
- `GET /broll/status`
  - Whether B-roll integration is configured
- `POST /upload`
  - Upload a source video

## Admin Routes

Source file:

- `backend/src/api/routes/admin.py`

Routes:

- `GET /health`
  - Verify admin access and backend reachability

## Billing Routes

Source file:

- `backend/src/api/routes/billing.py`

Routes:

- `POST /subscription-email`
  - Send or trigger subscription-related email behavior
  - Events: `subscribed`, `unsubscribed`, `cancellation_scheduled`. Scheduled
    cancellation mail reads the user's current Stripe state and access end date
    from the database, and skips if the cancellation is no longer scheduled.

## Feedback Routes

Source file:

- `backend/src/api/routes/feedback.py`

Routes:

- `POST /feedback`
  - Submit a feedback item, optionally routing it to configured webhook destinations

## Social Publishing Routes

Source files:

- `backend/src/api/routes/social.py`
- `backend/src/services/social_service.py`
- `backend/src/social/` (YouTube, TikTok and Instagram providers)

Connecting and disconnecting accounts requires signed session headers. Posting
and performance routes also accept API keys.

Connections:

- `GET /social/providers`
  - Which platforms are configured on this server, their supported privacy levels and
    `default_privacy_level` (TikTok: `public`/`private`, default `private`)
- `GET /social/connections`
  - The user's connected accounts (never includes tokens)
- `POST /social/connections/{provider}/authorize`
  - Create a CSRF state and return the platform's `authorize_url`
- `POST /social/connections/{provider}/callback`
  - Body `{code, state}`; exchanges the code and stores encrypted tokens
- `DELETE /social/connections/{account_id}`
  - Disconnect an account, cancel its scheduled posts and revoke the grant at the platform
    (best effort; Instagram has no revocation endpoint)
- `DELETE /social/connections`
  - Disconnect every account (called when a user deletes their SupoClip account)

Posts:

- `GET /social/posts?task_id=&clip_id=&status=`
  - List posts with their latest metrics
- `POST /social/posts`
  - Body `{task_id, clip_id, social_account_id, title?, caption?, hashtags?, privacy_level?, scheduled_for?}`.
    Without `scheduled_for` the post is queued immediately; with an ISO timestamp it is scheduled.
- `GET /social/posts/{post_id}`
  - One post including its metrics history
- `POST /social/posts/{post_id}/cancel`
  - Cancel a scheduled or queued post
- `POST /social/posts/{post_id}/retry`
  - Re-queue a failed or cancelled post. An upload the platform already accepted is only
    checked again, never uploaded twice
- `POST /social/posts/{post_id}/refresh-metrics`
  - Pull fresh metrics from the platform now
- `DELETE /social/posts/{post_id}`
  - Remove the post record (the platform post is untouched); not while queued or publishing

Performance loop:

- `GET /social/performance`
  - Views, likes, comments and shares aggregated by hook type, platform and clip length, plus top clips.
    The same aggregation is injected into the AI selection prompt once
    `SOCIAL_PERFORMANCE_MIN_POSTS` posts have metrics.

Public:

- `GET /social/media/{media_token}`
  - Streams a clip for a platform fetch while the token is valid (a few hours around publishing).
    With `MEDIA_STORAGE=s3` it redirects to a presigned URL instead

Post status flow: `scheduled → queued → publishing → published | failed | cancelled`.
A worker claims a queued post atomically before uploading, and always publishes
the clip's current render. Deleting a clip or task cancels its scheduled and
queued posts. The worker runs a cron every minute to promote due scheduled
posts, poll asynchronous publishes and fail posts stuck in `publishing` for two
hours, and hourly to refresh metrics. When the platform may already have the
post (a timeout after the upload was sent, or a lost worker) the post fails with
"Upload outcome unknown" and is never uploaded again automatically.

## Auth and Identity Model

### Browser to frontend

The browser talks to Next.js route handlers on the frontend domain.

### Frontend to backend

Frontend server routes:

- read the Better Auth session
- determine the current user
- attach auth headers or internal credentials
- proxy requests to the FastAPI backend

### Admin access

Admin-only pages and routes rely on the `is_admin` field stored on the user record.

## Streaming Behavior

The progress endpoint uses Server-Sent Events rather than WebSockets.

Important implications:

- The frontend subscribes with `EventSource`
- The response stays open while the task is active
- Redis-backed progress updates can appear live without repeated polling

## Billing and Hosted Mode Notes

Billing-specific endpoints only matter if you are running with:

```env
SELF_HOST=false
```

In self-host mode, you may still see some route files, but the effective product behavior is much simpler.

## Practical Debugging Tips

If a route seems broken:

1. Confirm the frontend proxy route exists.
2. Confirm the backend route exists.
3. Confirm the user session is valid.
4. Confirm the backend auth secret and origin configuration match your environment.
5. Check browser network logs and backend container logs together.

## Related Reading

- [Architecture](./architecture.md)
- [Development](./development.md)
- [Troubleshooting](./troubleshooting.md)
