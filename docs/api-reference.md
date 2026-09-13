# API Reference

This is a practical reference for the API surface used by SupoClip. It combines the frontend proxy routes and the backend routes they rely on.

For exact schemas and interactive testing, also use FastAPI docs at `http://localhost:8000/docs`.

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
  - Read and update default subtitle styling preferences
- `POST /api/feedback`
  - Submit product feedback

### Billing

- `POST /api/billing/checkout`
  - Open Stripe checkout
- `POST /api/billing/portal`
  - Open Stripe customer portal
- `POST /api/billing/webhook`
  - Stripe webhook receiver

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
  - Create task. Accepts per-request `max_clips` (1-20, overrides the global `MAX_CLIPS`) and `target_duration_seconds` (15/30/60, overrides `CLIP_DURATION`) that steer AI segment selection for this video specifically, in addition to the existing styling/cleanup fields.
- `GET /billing/summary`
  - Billing summary for current user
- `GET /{task_id}`
  - Task detail
- `GET /{task_id}/progress`
  - Server-Sent Events progress stream
- `PATCH /{task_id}`
  - Update task metadata
- `DELETE /{task_id}`
  - Soft-delete a task (moves it to Trash; the source video and clip files are left on disk)
- `POST /{task_id}/cancel`
  - Cancel an active task
- `POST /{task_id}/resume`
  - Resume a cancelled or errored task
- `GET /trash`
  - List the current user's soft-deleted tasks
- `POST /{task_id}/restore`
  - Restore a soft-deleted task out of Trash
- `DELETE /{task_id}/purge`
  - Permanently delete a soft-deleted task (irreversible) — best-effort removes its on-disk clip files, never touches the source video

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
- `POST /{task_id}/clips/{clip_id}/hook-variants`
  - Generate 1-6 AI-written alternative hook titles for a clip (for A/B comparison), appended to any previously generated variants
- `PATCH /{task_id}/clips/{clip_id}/hook-variants/select`
  - Apply a generated variant (`variant_id`) or custom text (`hook_title`) as the clip's active hook, optionally overriding `hook_type`; re-renders the clip from source since the hook is burned into the frame
- `GET /{task_id}/clips/{clip_id}/export`
  - Export using a platform preset
- `PATCH /{task_id}/clips/{clip_id}/reactions`
  - Replace a clip's emoji reactions (body: `{"reactions": [{id, emoji, timestamp_seconds, animation_style, duration_seconds, position: {x_pct, y_pct}}]}`); re-renders the clip from source since reactions are burned into the frame

### Task-wide settings and diagnostics

- `GET /hook-options`
  - List selectable hook types and animation styles (for the hook editor/comparison UI)
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

- `GET /export-presets`
  - List all export presets with full metadata (dimensions, bitrates, `max_duration_seconds`, safe-area margins, `target_lufs`), in display order
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

## Feedback Routes

Source file:

- `backend/src/api/routes/feedback.py`

Routes:

- `POST /feedback`
  - Submit a feedback item, optionally routing it to configured webhook destinations

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
- `status`/`progress` events carry a `stage` field (`download`/`transcribe`/`analyze`/`render`/`complete`) for a stage-by-stage UI, alongside the numeric `progress` percentage and free-text `message`
- A `clip_progress` event fires when a clip starts rendering (before `clip_ready`), carrying `clip_index`/`total_clips`, so the UI can show "rendering clip i/N" ahead of the clip actually being ready

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
