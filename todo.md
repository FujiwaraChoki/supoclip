# Workflows: where we stopped (2026-10-31)

Branch `feat/workflows`, worktree `/home/loki/Work/supoclip-workflows` (from `origin/main` at `08ec76db`).
Feature: "What do you want to make?" → `workflow` = highlights | series | topic | recap | trailer, plus an
instructions field. Background: Codex chat on fuji (2026-10-27) + the founder's bar "almost black-magic".

## Files to read first
- `docs/workflows-spec.md`: the final implementation spec (decisions, invariants I1–I6, contracts,
  prompts, work packages WP1–WP6, test list §19, eval and ship bar §20).
- `workflows-build-reports.md`: every agent's report: open issues, cross-package requests, spec deviations.
  **Delete this file and this todo before opening the PR.**

## Status
- [x] WP1 contracts, migration, API, `WORKFLOWS_ACCESS` gate (implemented + reviewed)
- [x] WP2 deterministic engine: units, boundaries, series partition, recap/trailer fit, titles (implemented + reviewed)
- [x] WP4 pipeline + render integration, planned-segment locking, output offsets (implemented + reviewed)
- [x] WP5 frontend creation UI (implemented + reviewed)
- [x] WP6 results UI, MCP, docs (implemented + reviewed)
- [~] WP3 LLM planners: implemented, **review was stopped partway** (it may have left partial edits;
      re-review all of `backend/src/workflows/{llm,prompts,schemas,outline,series,topic,recap,trailer,pipeline}.py` + `ai.py` diff)
- [ ] Integration: make sure `task_service`/`video_service` call the REAL `plan_workflow` (no fake), apply
      the still-valid cross-package requests and open issues from the reports file, check that contracts match end to end
      (API JSON ↔ `frontend/src/lib/workflows.ts` ↔ task page ↔ MCP)
- [ ] Full suites green: backend unit + integration, frontend tsc/vitest/lint, mcp tests
- [ ] Smoke: one real fixture per workflow through `plan_workflow` / `backend/scripts/workflow_eval.py`
- [ ] Independent adversarial bug review of the whole diff (separate workflow, fresh eyes)
- [ ] Quality eval per spec §20 (rendered outputs, boundary excerpts, judge rubric), tune prompts until the ship bar is met
- [ ] Live run of each workflow in the local app (upload + YouTube)
- [ ] Clean up WIP files, split into sensible commits if wanted, open the PR (deploy behind `WORKFLOWS_ACCESS=admins`)

## State at stop
- Backend unit: 4338 passed (baseline main: 1525). Frontend `tsc` clean. Integration, vitest, lint and mcp
  were not re-run after the last edits.

## Environment notes
- Test DB/Redis: throwaway containers `supoclip-wf-pg` (127.0.0.1:55432) and `supoclip-wf-redis` (127.0.0.1:56379).
  They may be gone after a reboot: `docker start supoclip-wf-pg supoclip-wf-redis`. If they no longer exist, run
  `docker run -d --name supoclip-wf-pg -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=supoclip_test -p 127.0.0.1:55432:5432 postgres:15`.
  Env: `TEST_DATABASE_URL=DATABASE_URL=postgresql+asyncpg://postgres:pg@127.0.0.1:55432/supoclip_test REDIS_PORT=56379`
  (the 3 `test_video_service` baseline tests also need `DATABASE_URL`).
- Run: `cd backend && .venv/bin/pytest -p no:cacheprovider --no-cov -q tests/unit`; `cd frontend && pnpm exec tsc --noEmit -p . && pnpm exec vitest run && pnpm run lint`.
- LLM for real runs: **Anthropic API, as in prod** (`LLM=anthropic:claude-sonnet-5-5`), not OpenRouter and not local ollama.
  The key lives encrypted in prod `app_settings` (the env var there is empty). Decrypt it into a private file and never commit it.
  See the memory note "Prod debugging on hetzner".
