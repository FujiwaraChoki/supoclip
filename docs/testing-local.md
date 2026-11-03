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
export MEDIA_STORAGE=local
export YOUTUBE_DOWNLOAD_PROVIDER=yt_dlp YOUTUBE_METADATA_PROVIDER=yt_dlp
export YOUTUBE_PROXY_URL='' YOUTUBE_POT_PROVIDER_URL=''
export APIFY_API_TOKEN='' RAPIDAPI_YOUTUBE_API_KEY='' YOUTUBE_DATA_API_KEY='' GOOGLE_API_KEY=''
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
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

## Retry recovery stress tests

Start with a fresh disposable database and empty dedicated Redis. Reusing a live-test
database can leave cached media or queued jobs that interfere with other integration
tests. Run the full backend suite before the opt-in media tests below.

```sh
cd backend
export TEST_DATABASE_URL=postgresql+asyncpg://supoclip@127.0.0.1:55432/supoclip_test
export DATABASE_URL="$TEST_DATABASE_URL"
export MEDIA_STORAGE=local SELF_HOST=true
export REDIS_HOST=127.0.0.1 REDIS_PORT=56379
export YOUTUBE_PROXY_URL='' YOUTUBE_POT_PROVIDER_URL=''
export APIFY_API_TOKEN='' RAPIDAPI_YOUTUBE_API_KEY=''
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
.venv/bin/pytest
```

The retry tests include:

- A 45-case failure matrix across four planned clips, three attempts per case,
  and fresh service/database sessions between attempts. Committed clip IDs,
  metadata, file hashes, order, and completed plan positions must survive.
- Twenty simultaneous retry API calls and twenty duplicate worker invocations.
  Only one job may enqueue or enter the pipeline.
- A 24,000-word multilingual checkpoint with twenty concurrent stage updates,
  plus a late checkpoint write after task deletion.
- Real yt-dlp HTTP and HLS fragment downloads from a loopback server that injects
  502/503/504 responses, premature EOF, and disconnects. Tests verify the attempt
  limit, fresh session selection, shared byte budget, and preservation of previous
  sections. HLS outputs are muxed and decoded with real ffmpeg. No proxy is used;
  proxy configuration and timeout boundaries have separate mocked tests.

To exercise real transcription, AI selection, rendering, trimming, and recovery,
download a speech-rich excerpt directly with yt-dlp, then run the opt-in tests:

```sh
# From backend/, using the same local-only environment above.
mkdir -p ../tmp/retry-live
.venv/bin/yt-dlp --ignore-config --proxy '' \
  -f 'best[ext=mp4][height<=480]/best[ext=mp4]' --download-sections '*00:00-00:03:00' \
  -o '../tmp/retry-live/source.%(ext)s' \
  'https://www.youtube.com/watch?v=kCmyxaBRv8o'
export RETRY_LIVE_SOURCE="$(realpath ../tmp/retry-live/source.mp4)"
export RETRY_LIVE_ARTIFACTS="$(realpath ../tmp/retry-live)"
export RETRY_LIVE_LLM=ollama:YOUR_INSTALLED_MODEL
.venv/bin/pytest tests/live/test_retry_media.py -s --no-cov
```

These two opt-in tests use local Whisper (`base`), local Ollama on port 11434,
local media storage, and disabled completion notifications. The first requires a
source yielding at least two selected clips. It injects a failure after transcription,
a partial render failure, and cancellation after rendering; it deletes transcript
sidecars to verify checkpoint restoration, trims a committed clip, and checks that
retry preserves its exact bytes and database row. A final repeated attempt must do
no additional rendering. The second downloads a short public YouTube video through
the application's direct yt-dlp path and retries after a clip was saved. Both fully
decode every final clip and write `report.json` files with stage counts and hashes.

The media tests are skipped unless `RETRY_LIVE_SOURCE` is set. They create test data
and clear the short fixture's processing-cache entry, so use only the disposable
database. A three-minute excerpt validates real recovery mechanics; it is not a
full-length 108-minute run or a production residential-proxy canary.
