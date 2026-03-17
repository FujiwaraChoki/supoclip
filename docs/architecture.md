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
- `.env.example`
- `start.sh`

This repository snapshot does not currently include the separate `waitlist/` app referenced in older project guidance.

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
- `/sign-in`
- `/sign-up`
- `/admin`

### Frontend API routes

The frontend includes server routes under `frontend/src/app/api`. They serve several purposes:

- Attach session-based auth context
- Proxy requests to the backend
- Handle Stripe callbacks and webhooks
- Expose internal user preference and feedback endpoints

This separation lets the browser talk to the frontend domain while the frontend securely talks to the backend.

### Authentication

SupoClip uses Better Auth with Prisma and PostgreSQL.

Important details:

- Email and password login is enabled
- Additional user field `is_admin` is persisted
- Trusted origins are derived from app configuration
- Session cookies are used to identify the current user

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

## Related Reading

- [App Guide](./app-guide.md)
- [API Reference](./api-reference.md)
- [Development](./development.md)
- [Troubleshooting](./troubleshooting.md)
