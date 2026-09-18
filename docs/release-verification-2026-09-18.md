# SupoClip release verification — 2026-09-18

This release consolidates existing behavior and fixes reliability and editing bugs. It adds no new product features. Changes are split across PRs #103–#140; each code PR received an independent agent review, with exact reviewed commits recorded in GitHub review comments. Review findings were fixed before merge.

## Local verification

The final assembled release was tested with real PostgreSQL, Redis, the API, the asynchronous worker, and the Next.js application. YouTube metadata and downloads used the local `yt_dlp` adapters, with local Whisper `base` transcription and Ollama `qwen3.8:27b`. Apify and hosted AI credentials were empty. The locked yt-dlp runtime was updated to 2026.8.19 after a real download exposed HTTP 403 failures in the previous version.

| Check | Result |
| --- | --- |
| Backend unit/integration tests, including real PostgreSQL edit and worker races | 190 passed |
| Frontend unit tests | 67 passed |
| ESLint and TypeScript | Passed |
| Next.js production build | Passed |
| Real-service browser workflows | 8 passed |
| Isolated browser regressions with mocked API failures | 7 passed |
| Browser smoke tests against the local production build | 2 passed |
| API/worker workflow groups | 6 passed |
| MCP over real stdio and HTTP | 17 tools discovered; 14 tool calls passed |

Real-service browser coverage includes YouTube generation and playback on desktop/mobile, caption saving and reload, browser export dimensions and audio volume, trimming and repeated splits, merging, sharing and revocation, range requests, upload processing, authentication, preferences, API key revocation, and custom font upload/deletion. The browser suite passed before the final worker cancellation changes; the complete backend suite and real API/MCP processing were then rerun with those changes included.

The API runner additionally verifies malformed media reaches a terminal error, cancellation followed by worker exit and successful resume, rename, task-setting regeneration, and original/pan/split output modes without captions. Outputs were checked for playability, cleanup, validation, and clip deletion. MCP coverage includes generation, waiting, download, export, and deletion through the actual server.

Independent review found and verified fixes for numeric overflow returning 500, caption drafts being lost when resetting preview adjustments, controls remaining active while saving, concurrent clip edits losing updates, hook title styling changing during caption edits, cancellation publishing late render results, and rapid resume allowing an old worker to overwrite the new run. Resume now returns 409 while the old worker is still draining; PostgreSQL advisory locks prevent overlapping task runs. Exception cleanup was also checked against pooled connections.

Coverage percentages reported by the test configurations apply only to selected modules. They are not whole-application coverage measurements.

## Scope and limitations

Live processing used a short 19-second, single-speaker YouTube fixture and its uploaded equivalent. This verifies the complete local pipeline but is not a long-video, 4K, or sustained-load benchmark. The installed MediaPipe package lacked the older solutions API, so the existing Haar fallback was exercised. Whisper transcription quality on the small fixture was imperfect; successful processing does not imply perfect speech recognition.

Live payments, transactional email, Discord delivery, hosted AI providers, and external B-roll services were not invoked. Their applicable automated tests use controlled inputs or mocks. Production configuration is preserved during deployment.

This report records pre-deployment verification. Deployment is performed only after all remaining reviews, merges, and the final main-branch CI run succeed. The deployment procedure builds staged images, preserves rollback images, backs up PostgreSQL, applies pending migrations, waits for active jobs to drain, and then checks the deployed services and public endpoints.

## Reproduction and evidence

See [local testing instructions](testing-local.md) for the committed browser and API runners. Use the committed pnpm and uv lockfiles. Backend integration tests require a dedicated PostgreSQL database; do not point them at production.

Raw logs and media evidence from this run are retained on the testing workstation under `/home/loki/.cache/supoclip-qa/`, including `backend-release-final.log`, `frontend-final.log`, `browser-final.log`, `production-smoke.log`, `api-workflows-final.log`, `mcp-final.log`, and `release-build.log`. Exact reviewed frontend commits and review URLs are recorded in `frontend-review-final-heads.json`; GitHub retains all PR review comments.
