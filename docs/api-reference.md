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

### Waitlist

- `POST /api/waitlist`
  - Waitlist submission endpoint

The route exists in the frontend, even though the separate `waitlist/` application mentioned in older docs is not present in this repository snapshot.

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
  - Export using a platform preset

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
