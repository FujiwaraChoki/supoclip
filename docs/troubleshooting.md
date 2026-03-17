# Troubleshooting

This guide collects the most common operational issues with SupoClip and how to diagnose them.

## Start Here

When the app is misbehaving, check these first:

```bash
docker-compose ps
docker-compose logs -f
docker-compose logs -f backend
docker-compose logs -f worker
```

Also verify:

- `http://localhost:3000` loads
- `http://localhost:8000/health` responds
- `http://localhost:8000/docs` opens

## Services Fail to Start

### Symptom

One or more containers never become healthy.

### Checks

```bash
docker-compose ps
docker-compose logs -f backend
docker-compose logs -f frontend
docker-compose logs -f postgres
docker-compose logs -f redis
```

### Common causes

- Docker Desktop is not running
- `.env` is missing or incomplete
- PostgreSQL or Redis failed health checks
- frontend build-time variables are invalid
- backend startup is failing because a required key or URL is malformed

### Fixes

- Confirm Docker is running with `docker info`
- Rebuild after config changes:

```bash
docker-compose up -d --build
```

- If the environment is badly out of sync, restart everything:

```bash
docker-compose down
docker-compose up -d --build
```

## Tasks Stay Queued Forever

### Symptom

Task creation succeeds, but progress never moves beyond `queued`.

### Most likely causes

- Worker container is not running
- Redis is unavailable
- Worker cannot reach backend dependencies
- Task timed out in queue handling

### Checks

```bash
docker-compose logs -f worker
docker-compose logs -f redis
docker-compose ps
```

Also inspect:

- task status in the UI
- backend progress endpoint behavior
- `QUEUED_TASK_TIMEOUT_SECONDS`

### Fixes

- Start or restart the worker
- Confirm Redis is healthy
- Confirm the task was actually enqueued
- Review worker exceptions around download, transcription, or rendering

## Backend Starts But Clip Generation Fails

### Symptom

Tasks begin processing and then move to `error`.

### Common causes

- Invalid API key
- LLM/provider mismatch
- YouTube download issue
- AssemblyAI failure
- FFmpeg or media-processing dependency issue
- Rendering failure caused by fonts or clip options

### Checks

```bash
docker-compose logs -f backend
docker-compose logs -f worker
```

Verify:

- `ASSEMBLY_AI_API_KEY` is set
- `LLM` matches the provider key you supplied
- The provider account is active and has quota

### Provider mismatch examples

- `LLM=openai:...` requires `OPENAI_API_KEY`
- `LLM=google-gla:...` requires `GOOGLE_API_KEY`
- `LLM=anthropic:...` requires `ANTHROPIC_API_KEY`
- `LLM=ollama:...` requires a reachable Ollama endpoint

## YouTube Downloads Fail

### Symptom

YouTube tasks error early or cannot fetch source media.

### Common causes

- Expired or invalid cookies
- YouTube anti-bot restrictions
- Network restrictions
- Apify actor failures or missing `APIFY_API_TOKEN`
- `yt-dlp` fallback edge-case changes

### Checks

- Review backend and worker logs
- Confirm `APIFY_API_TOKEN` is set if you expect the primary download path to use Apify
- Confirm `APIFY_YOUTUBE_DEFAULT_QUALITY` is one of `360`, `480`, `720`, or `1080`

### Fixes

- Verify the source URL is publicly reachable by either Apify or plain `yt-dlp`
- Retry with `APIFY_API_TOKEN` configured if the direct `yt-dlp` fallback is being rate-limited

## Frontend Loads But Shows Errors

### Symptom

The UI opens, but parts of it fail to load or authenticated actions do not work.

### Common causes

- Backend unreachable from frontend
- `NEXT_PUBLIC_API_URL` is wrong
- auth secret or origin mismatch
- database/auth tables not initialized

### Checks

- Browser network tab
- frontend logs
- backend logs
- Better Auth configuration in `frontend/src/lib/auth.ts`

### Fixes

- Confirm `NEXT_PUBLIC_API_URL` points to the backend
- Confirm `BETTER_AUTH_SECRET` and `BETTER_AUTH_URL` are correct
- Confirm trusted origins include your actual frontend URL

## Cannot Sign In or Sign Up

### Common causes

- Database not ready
- Better Auth misconfiguration
- `DISABLE_SIGN_UP=true`
- Cookies blocked by wrong origin or protocol setup

### Checks

- Inspect frontend auth route behavior
- Verify users/session tables exist in PostgreSQL
- Verify app URL and auth URL match your active hostname

## Fonts Are Missing or Upload Fails

### Symptom

Font picker is empty, custom fonts do not appear, or font upload fails.

### Checks

- Verify `backend/fonts/` contains valid `.ttf` or `.otf` files
- Verify `GET /fonts` returns entries
- Check backend logs for font registry or upload errors

### Hosted-mode note

Some font upload behavior can differ when monetization is enabled, so verify whether you are in self-host or hosted mode.

### Fixes

- Add font files to `backend/fonts/`
- Rebuild or restart if the mount or registry state is stale
- Confirm the frontend is calling `/api/fonts` successfully

## Caption Templates or B-roll Are Missing

### Caption templates

If templates are missing:

- check `GET /caption-templates`
- inspect backend logs
- verify template definitions are still valid

### B-roll

If B-roll is unavailable:

- confirm `PEXELS_API_KEY` is set
- check `GET /broll/status`
- confirm the provider account has not been rate-limited or disabled

## Billing or Subscription Flow Is Broken

### Symptom

Checkout, portal access, billing summary, or subscription emails do not work.

### Checks

- Confirm `SELF_HOST=false`
- Confirm Stripe keys are set
- Confirm `STRIPE_PRICE_ID` is valid
- Confirm `BACKEND_AUTH_SECRET` is set
- Confirm `RESEND_API_KEY` and `RESEND_FROM_EMAIL` are configured

### Webhook checks

- Verify Stripe is sending events to the frontend webhook route
- Confirm webhook signature validation succeeds
- Confirm the database can persist webhook event records

### Email checks

- Confirm the sender domain is verified in Resend
- Check backend logs for subscription email errors

## Database Problems

### Symptom

Auth, tasks, or admin pages fail with database errors.

### Checks

- Confirm PostgreSQL container health
- Confirm the init script ran
- Confirm the app is using the same connection string you expect

### Fixes

If the database is disposable and you want a clean reset:

```bash
docker-compose down -v
docker-compose up -d --build
```

Warning: this deletes persisted data.

## Redis Problems

### Symptom

Queueing, progress updates, or worker behavior break.

### Checks

```bash
docker-compose logs -f redis
docker-compose exec redis redis-cli ping
```

If Redis is unavailable, task creation may still appear to work while background processing does not.

## Performance Is Poor

### Common causes

- Large or long source videos
- Slow external providers
- Resource constraints on your machine
- Too aggressive clip generation settings

### Practical mitigations

- Keep `DEFAULT_PROCESSING_MODE=fast`
- Lower `FAST_MODE_MAX_CLIPS`
- Use a lighter model where acceptable
- Avoid enabling B-roll unless needed
- Watch `GET /tasks/metrics/performance` for aggregate timing

## Task Detail Page Never Finishes Refreshing

### Symptom

The task page shows progress, but completed clips do not appear.

### Checks

- Verify `GET /tasks/{task_id}` returns `completed`
- Verify `GET /tasks/{task_id}/clips` returns clip data
- Check browser network logs for failed proxy requests
- Check whether the SSE stream ended normally

## Admin Features Do Not Work

### Common causes

- User is not marked `is_admin`
- frontend auth state is stale
- backend admin routes are blocked by auth or proxy configuration

### Checks

- Confirm the admin page is loading with an admin session
- Confirm user records in PostgreSQL have `is_admin=true`
- Confirm frontend admin proxy routes respond

## Recovery Playbook

If you just need to get back to a known good local state:

1. Stop the stack.

```bash
docker-compose down
```

2. Review `.env`.
3. Rebuild and restart.

```bash
docker-compose up -d --build
```

4. If needed, reset volumes.

```bash
docker-compose down -v
docker-compose up -d --build
```

## What to Collect Before Filing an Issue

- Exact command you ran
- `.env` values involved, with secrets redacted
- Browser error message
- Relevant `backend` and `worker` logs
- Whether the problem happens for YouTube, uploads, or both
- Whether `SELF_HOST` is `true` or `false`

## Related Reading

- [Setup](./setup.md)
- [Configuration](./configuration.md)
- [Architecture](./architecture.md)
