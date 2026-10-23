# Backend Docs

## Requirements

Ensure you have `ffmpeg` installed.

```
# MacOS
brew install ffmpeg

# Linux (Ubuntu)
sudo apt update -y && sudo apt install install ffmpeg -y

# Windows (Chocolatey https://chocolatey.org/)
choco install ffmpeg
```

You must also have `uv` package manager installed.

1. Create a virtual environment

```
uv venv .venv
source .venv/bin/activate
```

## Running Tests

The backend test suite uses `pytest` and is organized into:

- `tests/unit` for fast unit coverage around helpers and services
- `tests/integration` for FastAPI, database, and queue-backed API checks
- legacy `unittest`-style tests, which still run under `pytest`

Install dependencies:

```bash
uv sync --all-groups
```

Run the backend suite:

```bash
DATABASE_URL=postgresql+asyncpg://localhost:5432/supoclip \
TEST_DATABASE_URL=postgresql+asyncpg://localhost:5432/supoclip \
REDIS_HOST=127.0.0.1 \
REDIS_PORT=6379 \
.venv/bin/pytest
```

Notes:

- `TEST_DATABASE_URL` should point at a disposable local test database.
- Redis is only required for the integration paths that validate queue and health behavior.
- Coverage thresholds are enforced in `pyproject.toml` during the test run.
- For repo-level entrypoints, use `make test-backend` or `make test-ci` from the repository root.

## Email Configuration

The backend now sends subscription lifecycle emails through Amazon SES.

Set these env vars when using hosted billing:

```
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
SES_FROM_EMAIL="SupoClip <onboarding@example.com>"
```

Notes:

- `SES_FROM_EMAIL` must be a verified identity/domain in Amazon SES.
- The thank-you email is triggered after a successful Stripe checkout.
- The cancellation email is triggered after Stripe subscription deletion.
- Password reset links ("Forgot password?" on sign-in) are sent via `POST /account/password-reset-email`, which only accepts the frontend's signed session headers. Without SES configured, reset requests succeed silently but no email is sent.

## YouTube Downloads

YouTube downloads use `yt-dlp` by default so self-hosted and free deployments do not need Apify.

```env
YOUTUBE_DOWNLOAD_PROVIDER=yt_dlp
```

Set `YOUTUBE_DOWNLOAD_PROVIDER=apify` only when you want to use the paid Apify actor as the primary downloader:

```env
YOUTUBE_DOWNLOAD_PROVIDER=apify
APIFY_API_TOKEN=your_apify_token
# Select your own worker explicitly; match its input contract.
APIFY_YOUTUBE_DOWNLOADER_ACTOR=your-account/your-worker
APIFY_YOUTUBE_INPUT_ADAPTER=start_urls
APIFY_YOUTUBE_DEFAULT_QUALITY=1080
APIFY_RUN_TIMEOUT_SECONDS=900
```

Notes:

- `yt_dlp` is the free default and does not require `APIFY_API_TOKEN`.
- If `yt-dlp` fails and both `APIFY_API_TOKEN` and an explicit worker are configured, the backend can try Apify as a fallback.
- If Apify is selected but unavailable or fails, the backend falls back to `yt-dlp`.
- `APIFY_RUN_TIMEOUT_SECONDS` caps the Apify actor run time before SupoClip gives up.
- `APIFY_EXPENSIVE_FALLBACKS` defaults to off, so only the primary actor runs. Set it to `true` to allow the configured fallback actors.
- `YOUTUBE_PROXY_URL` inserts a residential-proxy yt-dlp attempt ahead of that chain and switches jobs to audio-then-sections. Video is capped at 1080p either way. Setup is in [docs/youtube-download.md](../docs/youtube-download.md).

For an ordered verified provider pool and private S3 delivery, configure
`APIFY_YOUTUBE_PROVIDERS` and the related private settings documented in
[configuration](../docs/configuration.md). Legacy input adapters are `start_urls`,
`progressive`, and `kv_videos`; use the contract expected by your chosen worker.

## YouTube Metadata Provider

Metadata lookup is configurable separately from downloads.

Set these env vars to use the official YouTube Data API v3 for title, duration, channel, thumbnail, and view-count preflight:

```env
YOUTUBE_METADATA_PROVIDER=youtube_data_api
YOUTUBE_DATA_API_KEY=your_youtube_data_api_key
```

Notes:

- `YOUTUBE_METADATA_PROVIDER=yt_dlp` preserves the previous metadata behavior.
- If `YOUTUBE_DATA_API_KEY` is not set, the backend will try `GOOGLE_API_KEY` for YouTube metadata requests.
- The selected metadata provider is the primary path only; the backend automatically falls back to the other provider if the first one fails.
- `videos.list` costs 1 quota unit per request in the YouTube Data API.
- The public API does not expose some `yt-dlp`-specific metadata fields like `format_id`, `resolution`, `fps`, or file size.
- Enable the YouTube Data API v3 for your Google Cloud project before using this mode.
