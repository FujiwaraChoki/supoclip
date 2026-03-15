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
  - `.env.example`
  - `start.sh`

Note: older repo guidance references a `waitlist/` app, but it is not present in this checkout.

## Main Commands

## Full stack with Docker

```bash
docker-compose up -d --build
docker-compose logs -f
docker-compose down
```

## Frontend

```bash
cd frontend
npm install
npm run dev
npm run build
npm run start
npm run lint
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

### Modify admin YouTube auth rotation

Primary files:

- `frontend/src/app/admin/page.tsx`
- `frontend/src/components/admin/youtube-auth-manager.tsx`
- `backend/src/api/routes/admin.py`
- `backend/src/services/youtube_cookie_manager.py`
- `backend/src/repositories/youtube_auth_repository.py`

## Testing and Verification

There is not yet a mature automated test suite. Current baseline verification is:

- `npm run lint` in `frontend/`
- manual smoke testing
- Docker-based end-to-end validation

Recommended smoke test:

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

## Related Reading

- [Architecture](./architecture.md)
- [API Reference](./api-reference.md)
- [Troubleshooting](./troubleshooting.md)

