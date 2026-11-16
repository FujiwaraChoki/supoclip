# Repository Guidelines

You are the owner and long-term maintainer of this codebase, and its design is up to you.

## Project Structure & Module Organization
This repository contains the web app, processing backend, and MCP client:
- `backend/`: FastAPI + async worker code (`src/api`, `src/services`, `src/repositories`, `src/workers`).
- `frontend/`: main Next.js app (`src/app`, `src/components`, `src/lib`, `prisma/`).
- `mcp/`: Python MCP server that connects to the REST API.
- `backend/src/media/`: transcription, captions, timeline, reframing, and ffmpeg helpers.

Infra and bootstrap files live at the root: `docker-compose.yml`, `init.sql`, `.env.example`, and `start.sh`.

A video worker may run on a different machine from the API (`docs/render-worker.md`). State that another process needs belongs in PostgreSQL, Redis, or the media bucket. Files under `TEMP_DIR` are scratch for the machine that wrote them.

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
- `cd backend && .venv/bin/python -m src.workers.run_workers`: run the video worker and the cron worker.

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
- Visual proof of the change (see below).
- Linked issue(s) and manual verification steps.
- A `CHANGELOG.md` entry for user-visible changes, or a brief explanation of why no entry is needed.

### Screenshots and video in PRs
Every PR that changes something a person can see or do must show it working in the real app:
- **Always** at least one screenshot of the feature or fix (before/after for visual changes).
- **A video too whenever possible**: any flow with more than one step, an animation, or a fix that is only visible over time (progress, playback, drag, undo). Record it with Playwright (`video: "on"` in a throwaway config or `page.video()`), or a screen recorder, against the running app. Keep it short (under ~30 s) and convert to a GIF (`ffmpeg ... -vf "fps=12,scale=960:-1"`) so it plays inline; link the `.mp4`/`.webm` as well when the GIF loses detail.
- Host media on an orphan branch named `pr-assets/<branch-name>` (never commit it to the feature branch or `main`), then embed with `https://raw.githubusercontent.com/FujiwaraChoki/supoclip/pr-assets/<branch-name>/<file>`. Images and GIFs render inline; videos show as links.
- Use demo data only: no real customer names, emails, IDs, or private videos in any capture.
- Backend-only or internal changes with nothing visible: say so in the PR instead (and show test output or logs where useful).

## Changelog Requirements
- Update the root `CHANGELOG.md` in the same PR as every user-visible feature, improvement, or bug fix. This includes UI wording changes and backend fixes that affect the user experience.
- Follow the file's format: newest date first, plain-language descriptions, a **New**, **Improved**, **Fixed**, or **Under the hood** category, and a link to the relevant PR. Never include customer names, email addresses, IDs, or other private details.
- Internal-only changes, including repository instructions and tests with no product behavior change, do not need a release note.
- Before merging, explicitly check that the required entry is present and run `cd frontend && pnpm exec vitest run src/lib/changelog.test.ts` when the changelog changes.
- When deploying a user-visible change, verify its entry appears on the live `/changelog` page before reporting the release complete. Do not defer the entry to a later follow-up.

## Security & Configuration Tips
- Never commit real secrets; use `.env.example` as the template.
- Preserve the committed dependency lockfiles and patched base-image versions when deploying. Run the `Image security` checks against freshly built final images; do not bypass a failing critical-vulnerability check with a server-only tag or an unpinned package install.
- For hosted frontend/API releases, follow [the serving rollout](deploy/serving/README.md) to stage an isolated candidate, switch nginx after readiness, and drain the previous release.
- Before a production restart, check that no SupoClip tasks are queued or processing and the worker has no ongoing jobs; let active clips finish first.
- Required runtime keys include `ASSEMBLY_AI_API_KEY` and either one hosted LLM provider key (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`) or an Ollama model configuration (`LLM=ollama:*`, optional `OLLAMA_BASE_URL`).
