# Testing with local YouTube downloads

Use a disposable PostgreSQL database and Redis instance. The browser seed step replaces
its `e2e-user@supoclip.test` and `e2e-admin@supoclip.test` accounts. Do not point these
checks at a database containing real work.

## Services

Before starting either backend service, apply migrations to an empty test database:

```sh
cd frontend
DATABASE_URL=postgresql://supoclip@127.0.0.1:55432/supoclip_test pnpm exec prisma migrate deploy
cd ..
```

Start the API and ARQ worker with the **same** database, Redis, media directory and
provider environment:

```sh
export DATABASE_URL=postgresql+asyncpg://supoclip@127.0.0.1:55432/supoclip_test
export REDIS_HOST=127.0.0.1 REDIS_PORT=56379
export TEMP_DIR=/tmp/supoclip-local-media
export BACKEND_AUTH_SECRET=local-test-backend-secret
export SELF_HOST=true
export YOUTUBE_DOWNLOAD_PROVIDER=yt_dlp YOUTUBE_METADATA_PROVIDER=yt_dlp
export APIFY_API_TOKEN='' YOUTUBE_DATA_API_KEY='' GOOGLE_API_KEY=''
export TRANSCRIPTION_PROVIDER=whisper WHISPER_MODEL=base
export LLM=ollama:YOUR_INSTALLED_MODEL
export OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
export NEXT_PUBLIC_APP_URL=http://127.0.0.1:3100
export CORS_ORIGINS=http://127.0.0.1:3100
```

Use a fresh database with no admin runtime overrides, so the empty Apify token also
disables fallback. Leave email, Discord and billing credentials unset. The test run
uses yt-dlp, Whisper and Ollama; no hosted AI keys are required.

In separate terminals, from `backend/`:

```sh
.venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8100
.venv/bin/arq src.workers.tasks.WorkerSettings
```

From `frontend/`, use the equivalent plain `postgresql://` database URL, then run:

```sh
# Point this at a short video with speech to also exercise uploads.
export LOCAL_TEST_VIDEO_PATH=/absolute/path/to/fixture.mp4
# Optional when using the system Chromium rather than Playwright's download:
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
pnpm run test:local
```

Playwright starts the frontend on port 3100 and reuses the API on 8100. The default
YouTube fixture is the 19-second “Me at the zoo” video. Override it with
`LOCAL_TEST_YOUTUBE_URL`. `ffmpeg` and `ffprobe` must be on `PATH`.

The eight browser scenarios cover creation and live completion, video playback,
mobile layout, caption persistence, browser export with measured audio gain, repeated
splits without losing neighboring clips, trimming, merging, sharing/revocation,
server export, direct upload, signup/signout, preferences, API keys and fonts.
Screenshots, real exports and failure traces are written to `frontend/test-results/`.

## Additional real worker checks

After the browser suite has seeded accounts, set `LOCAL_TEST_USER_ID` to the test
user's database ID and use the same backend secret and Redis port:

```sh
export LOCAL_TEST_USER_ID=TEST_USER_DATABASE_ID
export LOCAL_TEST_BACKEND_URL=http://127.0.0.1:8100
export LOCAL_TEST_ARTIFACTS=/tmp/supoclip-local-evidence
backend/.venv/bin/python backend/tests/live/run_local_workflows.py
```

This runner checks invalid-media error states, cancellation and resumption,
renaming, regeneration, original/pan/split framing, subtitle-free output, cleanup,
export validation and deletion. It uses real API requests, worker jobs and media.
Run it after Playwright finishes; browser reseeding would delete its test account.

## Other checks

- Backend: `TEST_DATABASE_URL=postgresql+asyncpg://... backend/.venv/bin/pytest -c backend/pyproject.toml backend/tests -q`
- Frontend: `pnpm run lint`, `pnpm run typecheck`, `pnpm run test:coverage`, `pnpm run build`.
- Seeded browser smoke tests: `pnpm run test:e2e`.
- Isolated mocked UI regressions: `pnpm run test:polish`.

Use Node 24 LTS for the browser runner. Node 26 on the tested workstation stalled
while extracting Playwright browser assets and writing some failure traces.

Live payment charges, real email/Discord delivery and hosted AI providers are outside
this local test setup. Billing and webhook validation still run in the automated
unit suite.
