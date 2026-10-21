# Repository Guidelines

## Project Structure & Module Organization
This repository contains the web app, processing backend, and MCP client:
- `backend/`: FastAPI + async worker code (`src/api`, `src/services`, `src/repositories`, `src/workers`).
- `frontend/`: main Next.js app (`src/app`, `src/components`, `src/lib`, `prisma/`).
- `mcp/`: Python MCP server that connects to the REST API.
- `backend/src/media/`: transcription, captions, timeline, reframing, and ffmpeg helpers.

Infra and bootstrap files live at the root: `docker-compose.yml`, `init.sql`, `.env.example`, and `start.sh`.

## Build, Test, and Development Commands
Use Docker for full-stack development:
- `docker-compose up -d --build`: start frontend, backend, worker, Postgres, and Redis.
- `docker-compose logs -f`: stream service logs.
- `docker-compose down`: stop everything.

Local app commands:
- `cd frontend && pnpm run dev`: run Next.js in dev mode.
- `cd frontend && pnpm run build && pnpm run start`: production build + serve.
- `cd frontend && pnpm run lint`: run ESLint.
- `cd backend && uv sync && uvicorn src.main:app --reload --host 0.0.0.0 --port 8000`: run API locally.
- `cd backend && .venv/bin/arq src.workers.tasks.WorkerSettings`: run the worker.

## Coding Style & Naming Conventions
- Python: 4-space indentation, type hints where practical, `snake_case` for functions/modules.
- TypeScript/React: 2-space indentation, `PascalCase` for component names, `camelCase` for variables/functions, route files in Next.js App Router conventions (`app/.../page.tsx`, `route.ts`).
- Linting: Next.js ESLint configs in `frontend/eslint.config.mjs`.
- Imports: use the `@/*` alias in Next.js apps when possible.

## Testing Guidelines
Automated coverage uses pytest, Vitest, and Playwright:
- Run `make check` for frontend lint and TypeScript checks.
- Run `make test` for backend and frontend unit/integration tests. Backend integration tests need a dedicated Postgres database.
- Run `make test-e2e` for the database-backed browser smoke tests.
- Run `cd frontend && pnpm run test:polish` for isolated browser workflow tests with mocked APIs.
- Install frontend dependencies with `pnpm install --frozen-lockfile`; use the committed pnpm lockfile.
- Smoke test core flows with `docker-compose` (create task, process clips, view task page).

When adding tests, place them near code or under `tests/` with clear names (`test_*.py`, `*.test.ts[x]`).

## Commit & Pull Request Guidelines
Recent history favors short imperative commit subjects (`Add list endpoint`, `Fix typo`, `improve UX`). Prefer:
- `type(scope): concise summary` (example: `feat(backend): add task list pagination`).
- One logical change per commit.

PRs should include:
- What changed and why.
- Any env/config or migration impact.
- Screenshots/GIFs for UI changes.
- Linked issue(s) and manual verification steps.

## Security & Configuration Tips
- Never commit real secrets; use `.env.example` as the template.
- Required runtime keys include `ASSEMBLY_AI_API_KEY` and either one hosted LLM provider key (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, or `ANTHROPIC_API_KEY`) or an Ollama model configuration (`LLM=ollama:*`, optional `OLLAMA_BASE_URL`).

## Cursor Cloud specific instructions

Cloud Agents do not use Docker for this repo. `.cursor/cloud-install.sh` installs apt packages (PostgreSQL 16, Redis 7, ffmpeg, fonts), uv, Python 3.11, pnpm 10.27.0, and Deno, then runs `uv sync --frozen --all-groups --extra cpu` in `backend/`, `pnpm install --frozen-lockfile` in `frontend/`, and `uv sync` in `mcp/`. `.cursor/cloud-start.sh` is the boot script. systemd is not running here, so Postgres is started with `pg_ctlcluster` and Redis with `redis-server --daemonize yes`.

- Database: `postgresql://supoclip:supoclip_password@127.0.0.1:5432/supoclip`. The API and worker need the `postgresql+asyncpg://` form. Prisma migrate runs before the API starts.
- API: http://127.0.0.1:8000 (docs at `/docs`). Worker: tmux session `supoclip-worker`. Frontend: http://127.0.0.1:3107.
- `SELF_HOST=true`. Signup works without email verification. Dev secrets are the placeholders in `.env.example`.
- `ASSEMBLY_AI_API_KEY` and an LLM key are unset. Uploads, task creation, and the worker run; transcription stops until a key is set, or Whisper plus Ollama is configured (`docs/testing-local.md`). YouTube metadata requests from this network are blocked by YouTube's bot check, so use a file upload unless `YOUTUBE_DATA_API_KEY` is set.
- Checks: `make check`, `make test-backend`, and `cd frontend && pnpm run test:coverage`. Social publishing integration tests call authorize while `SOCIAL_PUBLISHING_ACCESS` defaults to `off`, so those ten tests return 403 unless the variable is `all`.
