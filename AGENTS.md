# Repository Guidelines

## Project Structure
Monorepo with three apps:
- `backend/`: FastAPI API + ARQ worker (`src/api/routes`, `src/services`, `src/repositories`, `src/workers`). Python 3.11+, managed with `uv` (not pip/poetry).
- `frontend/`: main Next.js 15 app (App Router, React 19, Tailwind v4, Prisma + Better Auth). Package manager: pnpm.
- `mcp/`: standalone MCP server (`supoclip-mcp`) that wraps the REST API for MCP clients.

There is no `waitlist/` app, although older docs still reference one.

Root infra: `docker-compose.yml`, `init.sql`, `.env.example`, `start.sh`, `Makefile`. Deeper guides live in `CLAUDE.md` and `docs/`.

## Commands

### Docker (full stack)
- `docker-compose up -d --build` — starts frontend, backend, worker, mcp, Postgres, Redis. Rebuild after changing `.env`.
- Debug with `docker-compose logs -f backend|worker|frontend`.
- Ports: backend http://localhost:8000 (docs at `/docs`); frontend via Docker is http://localhost:3001 (host 3001 → container 3107). Local dev serves on 3107 directly — don't confuse the two.

### Backend (local)
Requires ffmpeg and running Postgres + Redis.
```bash
cd backend
uv venv .venv && source .venv/bin/activate
uv sync
uvicorn src.main_refactored:app --reload --port 8000   # NOT src.main:app (legacy monolith, do not extend)
arq src.workers.tasks.WorkerSettings                   # worker; video processing never runs without it
```

### Frontend (local)
```bash
cd frontend
pnpm install      # postinstall runs prisma generate
pnpm run dev      # Turbopack, port 3107
pnpm run build    # prisma generate && next build
pnpm run lint
```

### Tests
Three layers: backend pytest, frontend Vitest, Playwright e2e. All expect Postgres + Redis (`docker-compose up -d postgres redis` is enough):
- `make test-backend` / `make test-frontend` / `make test-e2e` / `make test`
- Single test: `cd backend && .venv/bin/pytest tests/unit/<file> -k name`; `cd frontend && npx vitest run <path>`
- Backend pytest fails under 65% coverage of `src/auth_headers.py` and `src/services/billing_service.py`.
- The Makefile uses POSIX venv paths (`.venv/bin/`); on Windows run the underlying commands with `.venv\Scripts\` instead.
- CI (`.github/workflows/tests.yml`) runs the same three jobs with Postgres/Redis service containers.

## Architecture Notes
- Flow: Next.js → FastAPI → Redis queue → ARQ worker → Postgres. Task creation returns immediately; progress reaches the frontend via SSE backed by Redis pub/sub.
- Backend layering is deliberate: routes (HTTP) → services (orchestration) → repositories (raw SQL via asyncpg/SQLAlchemy `text()` — no ORM models for domain tables). Keep new code in this shape.
- Blocking work (downloads, transcription, rendering) must be wrapped in `run_in_thread()` (`src/utils/async_helpers.py`) so it doesn't stall the event loop.
- Two migration systems coexist — keep them consistent when changing schema:
  - Backend: base schema in root `init.sql`; incremental SQL in `backend/src/migrations/sql/` auto-applied at startup (tracked in `schema_migrations` table).
  - Frontend: Prisma migrations in `frontend/prisma/migrations/`, applied with `prisma migrate deploy`.
- DB naming is mixed: app tables snake_case, Better Auth tables camelCase; UUIDs stored as VARCHAR(36).
- The LLM clip-selection prompt lives in `backend/prompts/rells_engine.md`, loaded via `PromptManager` (hot-reloads on mtime change). Edit the markdown, not strings in `ai.py`. Spec: `RELLS_ENGINE_v2.0_Documento_Oficial.md`.
- Fonts/transitions are drop-in: adding files to `backend/fonts/` / `backend/transitions/` makes them appear via their GET endpoints.

## Environment
- Copy `.env.example` → `.env` at repo root. Required: `ASSEMBLY_AI_API_KEY` plus one LLM provider — `LLM=provider:model` with matching `OPENAI_API_KEY` / `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY`, or `LLM=ollama:*` (optional `OLLAMA_BASE_URL`).
- Ollama inside Docker needs `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`; local runs use `http://localhost:11434/v1`.
- `SELF_HOST=true` (default) disables monetization; `SELF_HOST=false` requires Stripe/SES/AWS config. `ALLOW_UNSIGNED_BACKEND_AUTH=true` skips HMAC session-header verification (self-host convenience).

## Style & Workflow
- Python: 4-space indent, type hints where practical, snake_case. TypeScript/React: 2-space indent, PascalCase components, `@/*` alias imports.
- Commits: `type(scope): concise summary` (e.g., `fix(clips): delete previous clips before reprocessing`), one logical change per commit.
- PRs: what changed and why, any env/migration impact, screenshots for UI changes, manual verification steps.
- Never commit real secrets; treat `.env.example` as the template only.
