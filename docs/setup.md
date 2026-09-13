# Setup

This guide covers the recommended Docker setup, local development mode, and the checks to perform after first boot.

## Requirements

### Required software

- Docker Desktop or a Docker Engine installation with Compose support
- Git

### Required credentials

- `ASSEMBLY_AI_API_KEY`
- One LLM provider configuration:
  - `OPENAI_API_KEY` with `LLM=openai:...`
  - `GOOGLE_API_KEY` with `LLM=google-gla:...`
  - `ANTHROPIC_API_KEY` with `LLM=anthropic:...`
  - `LLM=ollama:...` with an available Ollama server, optionally `OLLAMA_BASE_URL`

### Optional credentials

- `PEXELS_API_KEY` for AI B-roll sourcing
- `NEXT_PUBLIC_DATAFAST_WEBSITE_ID` and `NEXT_PUBLIC_DATAFAST_DOMAIN` for DataFast analytics
- `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `SES_FROM_EMAIL` for hosted billing emails
- Stripe keys if you are running with monetization enabled
- Discord webhook URLs for feedback forwarding

## Recommended Setup: Docker

Docker is the intended path for running SupoClip because it starts the frontend, backend, worker, PostgreSQL, and Redis together with the expected wiring.

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd supoclip
```

### 2. Create a local environment file

There is no `.env.example` template shipped in the repo; create `.env` in the project root yourself and set at least:

```env
ASSEMBLY_AI_API_KEY=your_assemblyai_key
LLM=google-gla:gemini-3-flash-preview
GOOGLE_API_KEY=your_google_key

# Optional: DataFast analytics
NEXT_PUBLIC_DATAFAST_WEBSITE_ID=dfid_xxxxx
NEXT_PUBLIC_DATAFAST_DOMAIN=your-domain.com
NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST=false
```

By default `REQUIRE_AUTH` is unset (`false`), so the app runs local-first with no login and everything resolves to a single implicit user. Only set `BETTER_AUTH_SECRET`, `BACKEND_AUTH_SECRET`, and `REQUIRE_AUTH=true` if you're standing up a real multi-tenant deployment — see [Configuration](./configuration.md).

### 3. Start the stack

Fastest option:

```bash
./start.sh
```

Manual equivalent:

```bash
docker-compose up -d --build
```

### 4. Wait for services to become healthy

```bash
docker-compose logs -f
docker-compose ps
```

You should see these services:

- `supoclip-frontend`
- `supoclip-backend`
- `supoclip-worker`
- `supoclip-postgres`
- `supoclip-redis`

### 5. Open the application

- Frontend: `http://localhost:3001` (the container listens on `3107`; Compose maps it to host port `3001` — see `docker-compose.yml`)
- Backend API: `http://localhost:8000`
- FastAPI docs: `http://localhost:8000/docs`

## What Docker Starts

The default Compose stack contains five services:

- `frontend`
  - Next.js application on container port `3107`, exposed on the host as `3001`
  - Proxies authenticated requests to the backend
- `backend`
  - FastAPI API on port `8000`
  - Provides task, media, billing, admin, and feedback endpoints
- `worker`
  - ARQ background worker
  - Processes long-running video jobs from Redis
- `postgres`
  - Stores users, sessions, tasks, sources, clips, billing metadata, and auth rotation state
- `redis`
  - Backs the job queue and progress event flow

## First-Run Checklist

After the stack is up:

1. Load the homepage at `http://localhost:3001`. No sign-in is required by default (local-first mode).
2. Submit a YouTube URL or upload a video file.
3. Open the task page and confirm progress updates appear.
4. Wait for clip generation to finish.
5. Open the clips list and verify playback and download work.
6. If DataFast is enabled, open browser devtools and confirm `/js/script.js` and `/api/events` load from your own domain.
7. Trigger one successful action such as task creation or feedback submission and verify the goal arrives in DataFast.

## Local Development Without Docker

Use this mode if you need to iterate on a single app directly. You still need PostgreSQL and Redis running somewhere.

### Backend

```bash
cd backend
uv venv .venv
source .venv/bin/activate
uv sync
uvicorn src.main_refactored:app --reload --host 0.0.0.0 --port 8000
```

In a second terminal:

```bash
cd backend
source .venv/bin/activate
arq src.workers.tasks.WorkerSettings
```

### Frontend

```bash
cd frontend
pnpm install
pnpm run dev
```

This starts the dev server on `http://localhost:3107` (not `3001` — that host port only exists when Compose maps the containerized frontend).

### Required local dependencies

- Python 3.11+
- Node.js compatible with Next.js 15
- PostgreSQL
- Redis
- FFmpeg available to the backend environment

## Data and Volumes

With Docker, SupoClip stores persistent data in named volumes:

- `postgres_data`
- `redis_data`
- `uploads`
- `clips`

The backend also mounts these local directories:

- `backend/fonts`
- `backend/transitions`

## Hosted Mode Versus Self-Hosted Mode

SupoClip defaults to self-host mode:

```env
SELF_HOST=true
```

When `SELF_HOST=false`, monetization and hosted billing flows become active. That mode requires additional Stripe and backend auth configuration. See [Configuration](./configuration.md).

## Production Setup Notes

For anything beyond local experimentation:

- Change `BETTER_AUTH_SECRET`
- Set a strong `BACKEND_AUTH_SECRET`
- Put the app behind HTTPS
- Set `NEXT_PUBLIC_APP_URL` to your deployed frontend origin
- Use persistent storage and backups for PostgreSQL
- Keep API keys outside version control
- Decide whether you want self-host mode or monetized hosted mode before launch
- Verify all callback URLs and origins match your deployed domain
- If using DataFast, set `NEXT_PUBLIC_DATAFAST_DOMAIN` to the deployed root domain you want tracked
- For hosted billing, create and verify both Stripe monthly prices before deploy: Pro at `$10/month` and Scale at `$50/month`

## Useful Commands

### Start or rebuild

```bash
docker-compose up -d --build
```

### Stream logs

```bash
docker-compose logs -f
docker-compose logs -f backend
docker-compose logs -f worker
```

### Stop services

```bash
docker-compose down
```

### Reset containers and volumes

```bash
docker-compose down -v
docker-compose up -d --build
```

Warning: `docker-compose down -v` deletes database and Redis data.

## Next Steps

- Review [Configuration](./configuration.md) before changing defaults
- Review [App Guide](./app-guide.md) to understand the UI and workflows
- Review [Troubleshooting](./troubleshooting.md) if tasks do not process correctly
