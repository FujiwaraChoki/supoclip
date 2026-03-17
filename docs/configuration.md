# Configuration

This guide explains the important environment variables used by SupoClip and how they affect behavior.

Most settings are sourced from `.env.example`, `docker-compose.yml`, and the backend configuration code in `backend/src/config.py`.

## Configuration Strategy

There are three main layers:

- Root `.env`
  - The main place to configure the app
- `docker-compose.yml`
  - Supplies environment variables into the running containers
- Application defaults
  - Fallback values defined in the backend or frontend code

In most cases, edit `.env` and then rebuild or restart the stack.

## Required Settings

### Transcription

| Variable | Required | Purpose |
|---|---|---|
| `ASSEMBLY_AI_API_KEY` | Yes | Enables word-level transcription used for clip extraction and subtitles |

### LLM selection

| Variable | Required | Purpose |
|---|---|---|
| `LLM` | Yes | Selects the provider and model, for example `google-gla:gemini-3-flash-preview` |
| `OPENAI_API_KEY` | If using OpenAI | Required for `openai:*` models |
| `GOOGLE_API_KEY` | If using Google | Required for `google-gla:*` models |
| `ANTHROPIC_API_KEY` | If using Anthropic | Required for `anthropic:*` models |
| `OLLAMA_BASE_URL` | If using Ollama remotely | Base URL for Ollama-compatible endpoints |
| `OLLAMA_API_KEY` | Optional | Used for hosted Ollama providers such as Ollama Cloud |

The backend can infer a default LLM from whichever API key is present, but setting `LLM` explicitly is safer and easier to debug.

## Core Application Settings

| Variable | Default | Purpose |
|---|---|---|
| `BETTER_AUTH_SECRET` | Dev secret | Frontend auth secret; must be changed in non-local environments |
| `DISABLE_SIGN_UP` | `false` | Prevents creation of new user accounts when set |
| `NEXT_PUBLIC_LANDING_ONLY_MODE` | `false` | Restricts the UI to the landing page only |
| `TEMP_DIR` | `/app/uploads` in Docker | Temporary backend working directory for uploads and processing |
| `CORS_ORIGINS` | `http://localhost:3000,http://sp.localhost:3000` | Allowed browser origins for backend requests |

## Analytics Settings

SupoClip can send pageviews and custom product events to DataFast from the `frontend` app.

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_DATAFAST_WEBSITE_ID` | unset | Public DataFast website ID used by the tracking script |
| `NEXT_PUBLIC_DATAFAST_DOMAIN` | unset | Root domain tracked by DataFast, for example `supoclip.com` |
| `NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST` | `false` | Enables local tracking on `localhost` when explicitly set to `true` |

### DataFast behavior

- Tracking stays disabled unless both `NEXT_PUBLIC_DATAFAST_WEBSITE_ID` and `NEXT_PUBLIC_DATAFAST_DOMAIN` are set.
- The frontend proxies DataFast through `/js/script.js` and `/api/events` using Next.js rewrites to reduce ad blocker loss.
- Localhost is excluded by default to avoid polluting production analytics.
- The current custom goals are:
  - `signup_completed`
  - `signin_completed`
  - `task_created`
  - `billing_checkout_started`
  - `billing_portal_opened`
  - `preferences_saved`
  - `feedback_submitted`
  - `waitlist_submitted`

## Processing Settings

These settings affect clip generation speed, throughput, and defaults.

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_PROCESSING_MODE` | `fast` | Default mode for new tasks |
| `FAST_MODE_MAX_CLIPS` | `4` | Clip cap used by fast mode |
| `FAST_MODE_TRANSCRIPT_MODEL` | `nano` | Lightweight transcript path for fast mode |
| `WHISPER_MODEL_SIZE` | `medium` in `.env.example` | Whisper model size when Whisper is used locally |
| `QUEUED_TASK_TIMEOUT_SECONDS` | `180` | Marks stale queued tasks as failed instead of leaving them stuck forever |
| `MAX_VIDEO_DURATION` | `5400` | Maximum accepted input video length in seconds |
| `MAX_CLIPS` | `10` | Upper bound used by backend logic |
| `CLIP_DURATION` | `30` | Default clip duration target in seconds |

### Processing modes

Current code and defaults emphasize a `fast` mode. If you expose additional modes such as `balanced` or `quality`, make sure the frontend and backend behavior stay aligned.

## Media and Creative Settings

| Variable | Default | Purpose |
|---|---|---|
| `PEXELS_API_KEY` | unset | Enables AI B-roll sourcing from Pexels |

Fonts and transitions are configured by mounted files rather than environment variables:

- Add `.ttf` or `.otf` files to `backend/fonts/`
- Add transition `.mp4` files to `backend/transitions/`

## Redis and Database Settings

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_HOST` | `redis` in Docker, `localhost` in code | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `DATABASE_URL` | injected by Docker | PostgreSQL connection string |
| `POSTGRES_DB` | `supoclip` | PostgreSQL database name |
| `POSTGRES_USER` | `supoclip` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `supoclip_password` | PostgreSQL password |

## Self-Host and Monetization Settings

These determine whether the app behaves like an open self-hosted product or a monetized hosted product.

| Variable | Default | Purpose |
|---|---|---|
| `SELF_HOST` | `true` | When `true`, monetization is disabled |
| `BACKEND_AUTH_SECRET` | unset in practice | Shared secret for trusted frontend-to-backend requests in hosted mode |
| `AUTH_SIGNATURE_TTL_SECONDS` | `300` | Lifetime of backend request signatures |
| `FREE_PLAN_TASK_LIMIT` | `10` | Hosted-mode free plan generation allowance |
| `PRO_PLAN_TASK_LIMIT` | `0` | Hosted-mode pro allowance; `0` is unlimited |
| `NEXT_PUBLIC_PRO_PRICE_MONTHLY` | `9.99` | Frontend display price for the pro plan |

### Stripe settings

Required when `SELF_HOST=false` and you want subscription management:

| Variable | Purpose |
|---|---|
| `STRIPE_SECRET_KEY` | Server-side Stripe API access |
| `STRIPE_WEBHOOK_SECRET` | Verifies Stripe webhook signatures |
| `STRIPE_PRICE_ID` | Price identifier for the paid plan |
| `STRIPE_CHECKOUT_URL` | Optional fallback checkout URL |
| `STRIPE_CUSTOMER_PORTAL_URL` | Optional fallback billing portal URL |

## Email and Feedback Settings

### Resend

| Variable | Purpose |
|---|---|
| `RESEND_API_KEY` | Sends hosted billing lifecycle emails |
| `RESEND_FROM_EMAIL` | Verified sender address for subscription emails |

### Discord feedback forwarding

| Variable | Purpose |
|---|---|
| `DISCORD_FEEDBACK_WEBHOOK_URL` | Receives product feedback messages |
| `DISCORD_SALES_WEBHOOK_URL` | Receives sales or lead-oriented submissions |

## Apify YouTube Downloader

SupoClip uses Apify's `epctex/youtube-video-downloader` actor as the primary YouTube download path. Metadata preflight and fallback downloads rely on the local `yt-dlp` stack over a direct connection.

| Variable | Default | Purpose |
|---|---|---|
| `APIFY_API_TOKEN` | empty | Enables the primary Apify download flow |
| `APIFY_YOUTUBE_DEFAULT_QUALITY` | `1080` | Requested Apify download resolution (`360`, `480`, `720`, or `1080`) |

## YouTube Metadata Provider

YouTube metadata lookup is configurable independently from the download provider.

| Variable | Default | Purpose |
|---|---|---|
| `YOUTUBE_METADATA_PROVIDER` | `yt_dlp` | Primary metadata provider: `yt_dlp` or `youtube_data_api` |
| `YOUTUBE_DATA_API_KEY` | empty | Preferred credential for the YouTube Data API metadata path |

Behavior notes:

- `yt_dlp` preserves the current metadata preflight behavior.
- `youtube_data_api` uses YouTube Data API v3 `videos.list` as the primary metadata source.
- If `YOUTUBE_DATA_API_KEY` is empty, the backend tries `GOOGLE_API_KEY` for YouTube Data API requests.
- The configured provider is the primary path only; the backend automatically falls back to the alternate provider on misconfiguration, HTTP errors, empty API results, or parsing failures.
- `videos.list` costs 1 quota unit per request.
- Public `videos.list` metadata covers fields like title, description, channel title, published date, duration, thumbnails, views, and likes, but not download-oriented fields such as `format_id`, `resolution`, `fps`, or file size.
- Enable the YouTube Data API v3 in Google Cloud before selecting `youtube_data_api`.

## Frontend Runtime Variables

These are especially relevant in Docker and deployments:

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_API_URL` | Browser-facing backend base URL |
| `NEXT_PUBLIC_APP_URL` | Canonical frontend URL |
| `NEXT_PUBLIC_DATAFAST_WEBSITE_ID` | Public DataFast website ID |
| `NEXT_PUBLIC_DATAFAST_DOMAIN` | Domain passed to the DataFast script |
| `NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST` | Enables local DataFast testing |
| `BACKEND_INTERNAL_URL` | Internal backend URL used by frontend server routes |
| `BETTER_AUTH_URL` | Auth origin for Better Auth |
| `NEXT_PUBLIC_SELF_HOST` | Exposes self-host mode to the frontend |

## Changing Configuration Safely

When changing `.env`:

1. Update the file.
2. Rebuild if build-time frontend values changed:

```bash
docker-compose up -d --build
```

3. Otherwise a restart is often enough:

```bash
docker-compose down
docker-compose up -d
```

## Recommended Minimal `.env`

For basic self-hosted use:

```env
ASSEMBLY_AI_API_KEY=your_key
LLM=google-gla:gemini-3-flash-preview
GOOGLE_API_KEY=your_key
BETTER_AUTH_SECRET=replace_me
SELF_HOST=true
```

To enable DataFast on a deployed frontend, add:

```env
NEXT_PUBLIC_DATAFAST_WEBSITE_ID=dfid_xxxxx
NEXT_PUBLIC_DATAFAST_DOMAIN=your-domain.com
NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST=false
```

For hosted monetized use, add at minimum:

```env
SELF_HOST=false
BACKEND_AUTH_SECRET=replace_me
STRIPE_SECRET_KEY=your_key
STRIPE_WEBHOOK_SECRET=your_key
STRIPE_PRICE_ID=price_xxx
RESEND_API_KEY=your_key
RESEND_FROM_EMAIL="SupoClip <onboarding@your-domain.com>"
```

## Related Reading

- [Setup](./setup.md)
- [Troubleshooting](./troubleshooting.md)
- [Architecture](./architecture.md)
