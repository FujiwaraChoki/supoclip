# Configuration

## Twitch and Kick imports

Twitch and Kick VOD/clip imports use an ordered, proxy-first provider chain. They reuse
`YOUTUBE_PROXY_URL`; an unconfigured proxy is skipped. Direct `yt-dlp` is followed only
by Apify fallbacks, which are skipped when `APIFY_API_TOKEN` is empty. Twitch clips do
not use the Twitch Apify actor because its clip URLs are not authenticated.

```env
TWITCH_DOWNLOAD_PROVIDERS=proxy,direct,apify_twitch
KICK_DOWNLOAD_PROVIDERS=proxy,direct_retry,apify_kick_hls,apify_kick_mp4
APIFY_STREAM_PROVIDERS=true
APIFY_TWITCH_ACTOR=abotapi/twitch-scraper
APIFY_KICK_HLS_ACTOR=hipersoft/kick-downloader
APIFY_KICK_MP4_ACTOR=parsebird/kick-video-downloader
APIFY_STREAM_TIMEOUT_SECONDS=300
APIFY_KICK_MAX_VIDEO_SIZE_MB=2000
```

The Apify runs use small memory limits and charge caps. The final Kick provider downloads
a complete 720p file and is therefore intentionally last; its size cap is duration-based,
at least 100 MB, and never exceeds `APIFY_KICK_MAX_VIDEO_SIZE_MB`.

Task creation runs a metadata preflight with one overall 30-second deadline: `yt-dlp`
through the proxy, then direct, then (only if both fail) the cheap metadata actor
(`apify_twitch` or `apify_kick_hls`). The full-video Kick actor never runs during
preflight. An actor result found there is stored with the task and reused by the worker,
so each actor runs at most once per task, and a chain that has failed completely is not
rerun for the same job.

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
| `OPENROUTER_API_KEY` | If using OpenRouter | Required for `openrouter:*` models (see [OpenRouter analysis](openrouter-analysis.md)) |
| `OLLAMA_BASE_URL` | If using Ollama remotely | Base URL for Ollama-compatible endpoints |
| `OLLAMA_API_KEY` | Optional | Used for hosted Ollama providers such as Ollama Cloud |

The backend can infer a default LLM from whichever API key is present, but setting `LLM` explicitly is safer and easier to debug.

## Core Application Settings

| Variable | Default | Purpose |
|---|---|---|
| `FRONTEND_PORT` | `3001` (legacy fallback); `.env.example` sets `3107` | Docker host port; the container always listens on `3107`. Preserve the existing port in production |
| `NEXT_PUBLIC_APP_URL` | `http://localhost:3107` | Browser-facing app URL; use the public HTTPS URL behind a reverse proxy |
| `BETTER_AUTH_URL` | `http://localhost:3107` | Browser-facing auth URL; use the public HTTPS URL behind a reverse proxy |
| `BETTER_AUTH_TRUSTED_ORIGINS` | unset | Additional explicit comma-separated HTTP(S) browser origins for authentication; no wildcard support |
| `BETTER_AUTH_SECRET` | Dev secret | Frontend auth secret; must be changed in non-local environments |
| `DISABLE_SIGN_UP` | `false` | Prevents creation of new user accounts when set |
| `NEXT_PUBLIC_LANDING_ONLY_MODE` | `false` | Restricts the UI to the landing page only |
| `TEMP_DIR` | `/app/uploads` in Docker | Working directory for uploads, caches and renders. With `MEDIA_STORAGE=local` the API and workers must share it; with `s3` each machine has its own |
| `MEDIA_STORAGE` | `local` | `s3` stores durable media in a private bucket; see [Media storage](media-storage.md) for configuration, retention and migration |
| `CORS_ORIGINS` | `http://localhost:3107,http://sp.localhost:3107,http://supoclip.localhost:3107` | Allowed browser origins for backend requests, including direct browser video uploads |

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

### PostHog (product analytics, session replay, error tracking)

The web app can also report to PostHog. It stays fully disabled unless `NEXT_PUBLIC_POSTHOG_KEY` is set, so self-hosted installs send nothing by default.

| Variable | Default | Kind | Purpose |
|---|---|---|---|
| `NEXT_PUBLIC_POSTHOG_KEY` | unset | build arg | PostHog project token (`phc_...`). Empty disables PostHog entirely |
| `NEXT_PUBLIC_POSTHOG_HOST` | `https://eu.i.posthog.com` | build arg | Ingest host the `/ingest` proxy and the server client send to |
| `POSTHOG_PERSONAL_API_KEY` | unset | build arg | Personal API key with error tracking write access; enables source map upload |
| `POSTHOG_PROJECT_ID` | unset | build arg | PostHog project ID for source map upload |
| `POSTHOG_RELEASE_VERSION` | unset | build arg | Optional release version (e.g. the git SHA) for uploaded source maps |

- `NEXT_PUBLIC_*` values are inlined at build time, so pass them as Docker build args.
- Events go through the same-origin `/ingest` reverse proxy (Next.js rewrites). Session replay masks all inputs; only identified users get person profiles, keyed by the Better Auth user id.
- Browser errors, `error.tsx`/`global-error.tsx`, server request errors (`instrumentation.ts`), and 5xx responses from `/api/*` are reported as exceptions. Each `/api/*` request carries an `x-trace-id` that the route handlers forward to the backend, so an exception's `trace_id` matches the backend logs.
- Source maps upload only when both `POSTHOG_PERSONAL_API_KEY` and `POSTHOG_PROJECT_ID` are present at build time.

## Processing Settings

These settings affect clip generation speed, throughput, and defaults.

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_PROCESSING_MODE` | `fast` | Default mode for new tasks |
| `FAST_MODE_MAX_CLIPS` | `10` | Clip cap used by fast mode (never above `MAX_CLIPS`) |
| `FAST_MODE_TRANSCRIPT_MODEL` | `universal-3-5-pro` | Fast mode uses the Pro model too; `universal-2` covers languages Pro doesn't. `nano`/`universal-2` select the cheaper model for self-hosting |
| `ASSEMBLY_AI_SPEECH_MODEL` | Empty | Overrides the AssemblyAI model for every processing mode. Set `universal-2` to use only the non-Pro model while retaining word timings and speaker labels; takes precedence over `FAST_MODE_TRANSCRIPT_MODEL` |
| `ASSEMBLY_AI_REQUEST_TIMEOUT_SECONDS` | `60` | Per-request upload/submit timeout (5–300). Transient upload failures retry up to 3 times; the paid submission is attempted once |
| `WHISPER_MODEL_SIZE` | `medium` in `.env.example` | Whisper model size when Whisper is used locally |
| `QUEUED_TASK_TIMEOUT_SECONDS` | `180` | Marks stale queued tasks as failed instead of leaving them stuck forever |
| `VIDEO_JOB_USER_CONCURRENCY` | `1` | Long video renders one account may run at once. Extra videos from that account wait without taking a worker slot. `1` is the default because a render can occupy a slot for hours; set `2` to allow two at once |
| `VIDEO_JOB_DEFER_SECONDS` | `20` | How long a video over the per-account cap waits before a worker checks again (5–300). The waiting job is not charged again |
| `QUEUE_DELAY_ALERT_SECONDS` | `600` | Pickup delay, from enqueue until a worker starts the job, that reports `queue_delay_exceeded` to PostHog |
| `QUEUE_DELAY_ALERT_INTERVAL_SECONDS` | `900` | Minimum time between those queue-delay reports |
| `EMBED_CRON_WORKER` | `1` | When `1`, the worker process also starts the cron worker on queue `supoclip_cron`. Set `0` if the optional Compose `cron-worker` service (profile `cron`) is running instead, and on video workers that run on a different machine from the API |
| `UPLOAD_COPY_WAIT_SECONDS` | `1800` | How long a video worker on another machine waits for an upload to be copied from the API's disk to object storage before the task fails (minimum 60). See [Video workers on other machines](./render-worker.md) |
| `MAX_VIDEO_DURATION` | `5400` | Maximum accepted upload length and baseline YouTube length in seconds |
| `MAX_VIDEO_UPLOAD_BYTES` | `4000000000` | Maximum uploaded file size, in bytes (4 GB). Checked in the browser before the upload starts, and again by the API and presigned upload session so it cannot be bypassed. Set `NEXT_PUBLIC_MAX_VIDEO_UPLOAD_BYTES` to the same value so the pre-upload check matches before the billing summary loads. Sources above 1080p or 60 fps are scaled down to that ceiling before transcription and rendering; plans with a lower output resolution still receive that lower resolution |
| `PRO_YOUTUBE_MAX_VIDEO_DURATION` | `5400` | Maximum YouTube length for active or trialing Pro subscriptions, in seconds |
| `SCALE_YOUTUBE_MAX_VIDEO_DURATION` | `10800` | Maximum YouTube length for active or trialing Creator (`scale`) subscriptions, in seconds |
| `MAX_CLIPS` | `10` | Hard cap on clips per task in every processing mode |
| `CLIP_DURATION` | `30` | Default clip duration target in seconds |

### Workflows

Series, topic, recap and trailer runs (and creator instructions on Highlights)
are gated separately from the rest of processing. Set these on **both the API
and the worker**; `docker-compose.yml` passes them to both. See
[workflows.md](workflows.md) for what each workflow makes.

The env gate is the ceiling. The `workflows` feature flag (`feature_flags`
table, switched in `/admin` without a deploy) also has to be on for the
caller's platform, web or iOS; admins bypass it by default. Both must allow a
request. The migration seeds it off on web and iOS, so after deploying, an
admin turns it on per platform. See
[workflows.md](workflows.md#the-workflows-feature-flag).

| Variable | Default | Purpose |
|---|---|---|
| `WORKFLOWS_ACCESS` | `admins` | `admins` (admins plus `WORKFLOWS_ALLOWED_EMAILS`), `all`, or `off`. Anyone outside the gate only sees Highlights; the API answers other workflows, and resumes of workflow tasks, with 403 `WORKFLOW_NOT_AVAILABLE`. Jobs already queued finish. Self-hosters usually want `all` |
| `WORKFLOWS_ALLOWED_EMAILS` | empty | Comma-separated emails admitted when access is `admins`. Only accounts with a verified email match |
| `WORKFLOWS_PUBLIC` | empty | Comma-separated workflows (for example `trailer,topic`) open to every user while `WORKFLOWS_ACCESS` is `admins`, with instructions and advanced settings. Ignored by `off` and `all`. |
| `WORKFLOW_TOPIC_MAX_CLIPS` | `15` | Most clips one topic run returns |
| `WORKFLOW_PLAN_TIMEOUT_SECONDS` | `1800` | Wall-clock limit for planning one workflow (capped at 6 hours); a plan that runs over fails with `plan_failed` and the minutes are refunded |
| `WORKFLOW_FULL_CONTEXT_MAX_WORDS` | model default | Transcript size, in English-word equivalents, up to which the whole video is outlined in one model call; longer videos are outlined in windows and merged. Empty uses 40,000 for hosted models and a smaller limit for `ollama:` models |

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
| `PRO_PLAN_MINUTE_LIMIT` | `600` | Hosted-mode Pro allowance, in minutes of source video per month (`0` = unlimited) |
| `SCALE_PLAN_MINUTE_LIMIT` | `1500` | Hosted-mode Creator (`scale`) allowance, in minutes of source video per month |
| `NEXT_PUBLIC_PRO_PRICE_MONTHLY` | `9.99` | Frontend display price for the Pro plan |
| `NEXT_PUBLIC_SCALE_PRICE_MONTHLY` | `24.99` | Frontend display price for the Creator plan |
| `NEXT_PUBLIC_PRO_PRICE_ANNUAL` | `99.90` | Frontend display price for a year of Pro |
| `NEXT_PUBLIC_SCALE_PRICE_ANNUAL` | `249.90` | Frontend display price for a year of Creator |
| `NEXT_PUBLIC_PRO_PLAN_MINUTE_LIMIT` | `600` | Frontend display allowance for the Pro plan |
| `NEXT_PUBLIC_SCALE_PLAN_MINUTE_LIMIT` | `1500` | Frontend display allowance for the Creator plan |
| `NEXT_PUBLIC_SOCIAL_PUBLISHING` | `soon` | How Creator's scheduled posting appears on pricing pages: `soon` ("Coming soon" tag, YouTube and TikTok), `live`, or `off` |

Plans are metered in minutes of source video. A video's full length, rounded up
to the minute, is charged when the task is created (YouTube length comes from
the metadata preflight, uploads are probed with ffprobe). Jobs that fail without
producing clips release their minutes. Allowances reset monthly, also on yearly
plans. Pro clips are downscaled to 720p after rendering; Creator clips, and Pro
subscribers who were subscribed before the change (`users.legacy_hd`), get
1080p. 720p plans also download YouTube sources at 720p (capped below
`APIFY_YOUTUBE_DEFAULT_QUALITY`, never above it), with a separate processing
cache entry so a 1080p request never reuses a 720p source. Scheduling posts requires Creator; publishing immediately works on Pro.

AI editing has a separate monthly allowance: 10 requests on Pro and 100 on
Creator, shared across all clips. It resets with the monthly video allowance,
including on annual subscriptions. Requests reserve an edit when queued; failed
jobs restore it, while completed requests count even if the draft is unchanged.
The editor shows the remaining allowance and reset date. Usage is stored in
Postgres and survives clip deletion and Redis restarts. Self-hosted editing is
unmetered. The former `AI_EDIT_DAILY_LIMIT` setting is no longer used.

### Stripe settings

Required when `SELF_HOST=false` and you want subscription management:

| Variable | Purpose |
|---|---|
| `STRIPE_SECRET_KEY` | Server-side Stripe API access |
| `STRIPE_WEBHOOK_SECRET` | Verifies Stripe webhook signatures |
| `STRIPE_PRO_PRICE_ID` | Price identifier for the Pro plan |
| `STRIPE_SCALE_PRICE_ID` | Price identifier for the Creator (`scale`) plan |
| `STRIPE_PRO_ANNUAL_PRICE_ID` | Yearly price identifier for the Pro plan |
| `STRIPE_SCALE_ANNUAL_PRICE_ID` | Yearly price identifier for the Creator plan |
| `STRIPE_PRO_LEGACY_PRICE_IDS` | Comma-separated earlier Pro prices existing subscribers still pay; they stay Pro and keep 1080p |
| `STRIPE_SCALE_LEGACY_PRICE_IDS` | Comma-separated earlier Creator prices that still map to Creator |
| `STRIPE_PRICE_ID` | Legacy fallback price identifier for the Pro plan |
| `STRIPE_CHECKOUT_URL` | Optional fallback checkout URL |
| `STRIPE_CUSTOMER_PORTAL_URL` | Optional fallback billing portal URL |

## Email and Feedback Settings

### Amazon SES

| Variable | Purpose |
|---|---|
| `AWS_REGION` | AWS region for Amazon SES, default `us-east-1` |
| `AWS_ACCESS_KEY_ID` | AWS access key ID used to send hosted billing lifecycle emails |
| `AWS_SECRET_ACCESS_KEY` | AWS secret access key used to send hosted billing lifecycle emails |
| `SES_FROM_EMAIL` | Verified sender address for subscription emails, default `SupoClip <onboarding@example.com>` |

#### Scheduled cancellation emails

The frontend Stripe webhook at `/api/billing/webhook` handles
`customer.subscription.updated`. When `cancel_at_period_end` becomes true or a
`cancel_at` date is first set, it saves the subscription state and calls the
authenticated backend `/billing/subscription-email` endpoint with
`{"event":"cancellation_scheduled"}`. The backend sends the customer an immediate
email with their access end date, a request for feedback, and an invitation to
reply to `support@supoclip.com` for 30% off for three months. The sender uses
`SES_FROM_EMAIL`; the signature is Team SupoClip.

The discount message uses the existing [product-email preferences and unsubscribe
links](product-update-emails.md). Opted-out customers receive a cancellation
confirmation without the offer. Routine updates, date changes to an existing
cancellation, and undoing a cancellation do not send another email. Successfully
processed Stripe event IDs suppress redelivery; failed email attempts return an
error so Stripe can retry. The existing end-of-subscription email remains.

Deploy both frontend and backend, ensure the Stripe webhook endpoint subscribes
to `customer.subscription.updated`, and configure the existing SES credentials,
an HTTPS `NEXT_PUBLIC_APP_URL` on the backend, and `BACKEND_AUTH_SECRET`. This
change adds no new environment variables or migrations; the existing email
preference migration must already be applied. Verify a scheduled cancellation
using a sandbox subscription and test inbox before enabling it in production.

The email does not apply a discount automatically. When the customer replies,
support must apply the 30%-off, three-month Stripe coupon and, if the subscription
has not yet ended, remove the scheduled cancellation. If it has ended, arrange a
new subscription with the coupon. Check annual-plan behavior separately: a
classic repeating coupon can discount an entire annual renewal within its
discount period. It does not enforce a once-per-customer offer limit; support
must check previous use before applying it.

### Discord feedback forwarding

| Variable | Purpose |
|---|---|
| `DISCORD_FEEDBACK_WEBHOOK_URL` | Receives product feedback messages |
| `DISCORD_SALES_WEBHOOK_URL` | Receives sales or lead-oriented submissions |

## Social Publishing Settings

Social publishing is optional. When no provider is configured the Publish
button and the Social Accounts page explain what is missing instead of failing.

SupoClip uses one OAuth app per platform, registered by whoever operates the
server. Users connect their own YouTube, TikTok and Instagram accounts through
that app from **Settings → Social Accounts**; tokens are stored encrypted with
`APP_SETTINGS_ENCRYPTION_KEY` (falling back to `BACKEND_AUTH_SECRET`).

| Variable | Purpose |
|---|---|
| `YOUTUBE_OAUTH_CLIENT_ID` / `YOUTUBE_OAUTH_CLIENT_SECRET` | Google OAuth client (Web application) with the YouTube Data API v3 enabled. `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` are accepted as aliases. |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | TikTok for Developers app with Login Kit and the Content Posting API (scopes `user.info.basic`, `video.publish`, `video.list`) |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | Meta app using "Instagram API with Instagram Login". `META_APP_ID` / `META_APP_SECRET` are accepted as aliases. |
| `SOCIAL_OAUTH_REDIRECT_BASE_URL` | Base URL for OAuth callbacks; defaults to `NEXT_PUBLIC_APP_URL` |
| `SOCIAL_PUBLIC_MEDIA_BASE_URL` | Base URL Instagram fetches videos from; defaults to `NEXT_PUBLIC_APP_URL` and must be publicly reachable |
| `SOCIAL_METRICS_REFRESH_HOURS` | Minimum hours between metric pulls per post (default `6`) |
| `SOCIAL_METRICS_MAX_POST_AGE_DAYS` | Stop refreshing metrics for posts older than this (default `60`) |
| `SOCIAL_PERFORMANCE_MIN_POSTS` | Measured posts required before clip selection is personalized (default `3`) |
| `SOCIAL_PUBLISH_MAX_ATTEMPTS` | Retries for transient publish failures (default `3`) |
| `SOCIAL_PUBLISHING_ACCESS` | `off` (default), `admins`, or `all`. `off` hides Social and Analytics from users. |
| `SOCIAL_PUBLISHING_PROVIDERS` | Comma-separated platform allowlist; defaults to `youtube,tiktok,instagram`. An explicitly empty value disables all platforms, even when credentials exist. |
| `SOCIAL_PUBLISHING_ALLOWED_EMAILS` | Additional users admitted by email when access is `admins`; empty means admins only. |

For an admin-only YouTube scheduling trial, set the following on **both the API
and worker** and restart them using the coordinated deployment procedure:

```dotenv
SOCIAL_PUBLISHING_ACCESS=admins
SOCIAL_PUBLISHING_PROVIDERS=youtube
SOCIAL_PUBLISHING_ALLOWED_EMAILS=
```

Admins can schedule during this restricted trial without a Creator subscription.
Allowlisted non-admins retain their normal plan requirements. TikTok and Instagram
remain unavailable even if their credentials or old connections exist. The API
rejects new connections, posts, and retries for disabled providers; the worker
rechecks platform and user access before each new upload, including jobs queued
before the rollout changed. Already accepted uploads may still be polled and
accounts can still be disconnected. This gate does not change pricing-page
availability labels or publish any clips automatically.

Register this redirect URI on every platform, replacing `<provider>` with
`youtube`, `tiktok` or `instagram`:

```
${NEXT_PUBLIC_APP_URL}/api/social/callback/<provider>
```

Platform review notes (the APIs themselves are free):

- **YouTube**: uploads from an unverified Google project are forced private
  until the project passes the YouTube API compliance audit. Check the project's
  current upload quota in Google Cloud before enabling publishing for users.
- **TikTok**: unaudited apps can only post with `private` (`SELF_ONLY`)
  visibility, only from TikTok accounts that are themselves set to Private at
  the time of posting, and for up to 5 accounts per 24 hours. A public account
  gets `unaudited_client_can_only_post_to_private_accounts` even for a private
  post; no API parameter avoids this. Public posting from public accounts
  requires TikTok's app audit. Leave `TIKTOK_APP_AUDITED` unset (false) until
  the audit passes: SupoClip then offers only Private and tells users to switch
  their account to Private. Set it to `true` after approval.
- **Instagram**: requires a professional (Business or Creator) account. Meta App
  Review is needed before accounts outside the app's testers can connect.

### Deployment and verification

The API applies `20260901_0001_social_publishing.sql` on startup through the
existing migration runner. It adds four social publishing tables; it does not
change existing clip or account records. Back up the database before deploying.
The API and worker need the same database, clip storage, encryption key, and
provider credentials. Restart both after changing environment variables. The
frontend proxies OAuth and media requests; provider secrets belong on the API
and worker, not in `NEXT_PUBLIC_*` variables.

With no provider credentials, task creation and editing remain available and
Social Accounts explains that publishing is not configured. Configure one
provider at a time and register its exact callback URL above. Instagram's media
URL must be reachable from the public internet.

Before enabling each provider for users:

- Connect a test account, disconnect it, and reconnect it. Confirm token refresh
  works and revoked credentials prompt a reconnect.
- Publish one clip using a visibility level allowed for the test account. Check
  the result on the platform as well as in SupoClip.
- Schedule a clip, confirm it publishes once, and cancel another scheduled clip.
- For TikTok, verify asynchronous processing and private completion with no
  public post ID. Status-check failures should keep checking the existing upload;
  after 45 minutes, Retry checks that same upload again instead of uploading it.
  A post TikTok rejects (`FAILED`) uploads again on Retry.
- For Instagram, confirm its public media fetch works through the frontend
  (local storage) or the presigned S3 URL (`MEDIA_STORAGE=s3`).
- Refresh metrics for a published post with an external ID and confirm the
  performance dashboard updates. Private posts without a public ID have no metrics.

Automated tests cover mocked provider requests, PostgreSQL migrations and
repository workflows, and the browser experience without keys. They do not
verify platform app approval or live OAuth, uploads, and metrics.

## YouTube proxy downloads

Plain `yt-dlp` from a datacenter IP is often stopped with "Sign in to confirm you're not a bot". Set `YOUTUBE_PROXY_URL` to put a residential-proxy `yt-dlp` attempt first. Leave it empty and the download chain is unchanged. See [YouTube download runbook](./youtube-download.md) for the proxy template, the bgutil sidecar, and a canary check.

| Variable | Default | Purpose |
| --- | --- | --- |
| `YOUTUBE_PROXY_URL` | empty | Proxy template. `{session}` becomes a per-job sticky id. `{country}` is optional. |
| `YOUTUBE_PROXY_COUNTRY` | empty | Two-letter country code substituted into `{country}`. Other values are ignored. |
| `YOUTUBE_POT_PROVIDER_URL` | empty | bgutil HTTP base URL, for example `http://bgutil:4416`. |
| `YOUTUBE_DOWNLOAD_MAX_BYTES` | `2000000000` | Per-job byte cap across audio, sections, and a full-file fallback. |
| `YOUTUBE_SECTION_PADDING_SECONDS` | `10` | Padding around each selected clip before overlapping ranges merge. |
| `YOUTUBE_MAX_HEIGHT` | `1080` | Video height cap. Values above 1080 are clamped. |
| `YOUTUBE_SOURCE_CACHE` | `on` | Keep audio and sections so later edits reuse them. `off` deletes sources after each task. |
| `YOUTUBE_SOURCE_CACHE_TTL_SECONDS` | `604800` | Age after which unused cache directories are removed. |

With a proxy set, a job downloads modest audio first, picks clips, then fetches only the padded ranges at or below 1080p. Section bytes go through yt-dlp's own HTTP client. ffmpeg only trims files that are already local, because ffmpeg does not route SOCKS proxies and mishandles authenticated HTTP proxies.

## Apify YouTube Downloader

SupoClip uses the actor explicitly configured in the private environment as the primary YouTube download path. Metadata preflight and fallback downloads rely on the local `yt-dlp` stack over a direct connection. When `YOUTUBE_PROXY_URL` is set, the proxy attempt runs before Apify.

| Variable | Default | Purpose |
|---|---|---|
| `APIFY_API_TOKEN` | empty | Enables the primary Apify download flow |
| `APIFY_YOUTUBE_DEFAULT_QUALITY` | `1080` | Requested Apify download resolution (`360`, `480`, `720`, or `1080`) |
| `APIFY_RUN_TIMEOUT_SECONDS` | `900` | Maximum Apify actor run time before SupoClip gives up and falls back |

## RapidAPI YouTube Downloader

An optional paid download service. Set `YOUTUBE_DOWNLOAD_PROVIDER=rapidapi` to try it first, then
the Apify pool, then yt-dlp; with `apify` it runs after the Apify pool. Missing settings skip it.

| Variable | Default | Purpose |
|---|---|---|
| `RAPIDAPI_YOUTUBE_API_KEY` | empty | RapidAPI key for the download service |
| `RAPIDAPI_YOUTUBE_HOST` | empty | Extraction API host (private configuration) |
| `RAPIDAPI_YOUTUBE_RELAY_HOST` | empty | Media relay host the API returns (private configuration) |
| `RAPIDAPI_YOUTUBE_TIMEOUT_SECONDS` | `1200` | Budget for extraction, transfer, muxing and verification (60–1800) |

Each attempt makes one extraction request, downloads video and original audio in parallel
ranges, muxes them and verifies byte counts, durations, both streams and decode samples before
accepting the file. Any contract or relay change fails closed.

## YouTube Metadata Provider

YouTube metadata lookup is configurable independently from the download provider.

| Variable | Default | Purpose |
|---|---|---|
| `YOUTUBE_METADATA_PROVIDER` | `yt_dlp` | Primary metadata provider: `yt_dlp` or `youtube_data_api` |
| `YOUTUBE_DATA_API_KEY` | empty | Preferred credential for the YouTube Data API metadata path |
| `YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS` | `30` | How long task creation waits for YouTube length metadata. Blank or invalid values use 30. Values above 180 are capped at 180. |

Behavior notes:

- `yt_dlp` preserves the current metadata preflight behavior.
- Task creation gives that lookup `YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS` (default 30, the same budget Twitch and Kick already use). Metadata requests and region retries share this deadline, with time reserved for the alternate metadata provider. A timeout returns HTTP 503 `VIDEO_METADATA_TIMEOUT` and does not create a task or use minutes. An exhausted regional restriction returns HTTP 422 `YOUTUBE_GEO_RESTRICTED` with upload guidance. Other metadata failures return `VIDEO_METADATA_UNAVAILABLE`.
- On Apple Silicon, the default `BACKEND_PLATFORM=linux/amd64` runs this lookup under emulation, and yt-dlp's deno step can use most of the 30 seconds. Raise the timeout (for example to 60) or set `BACKEND_PLATFORM=linux/arm64` with `BACKEND_CPU_ONLY=true` and rebuild the backend and worker. See [setup](./setup.md).
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
| `NEXT_PUBLIC_POSTHOG_KEY` | PostHog project token; empty disables PostHog |
| `NEXT_PUBLIC_POSTHOG_HOST` | PostHog ingest host (default EU) |
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
STRIPE_PRO_PRICE_ID=price_xxx
STRIPE_SCALE_PRICE_ID=price_xxx
STRIPE_PRO_ANNUAL_PRICE_ID=price_xxx
STRIPE_SCALE_ANNUAL_PRICE_ID=price_xxx
PRO_PLAN_MINUTE_LIMIT=600
SCALE_PLAN_MINUTE_LIMIT=1500
NEXT_PUBLIC_PRO_PRICE_MONTHLY=9.99
NEXT_PUBLIC_SCALE_PRICE_MONTHLY=24.99
NEXT_PUBLIC_PRO_PRICE_ANNUAL=99.90
NEXT_PUBLIC_SCALE_PRICE_ANNUAL=249.90
NEXT_PUBLIC_PRO_PLAN_MINUTE_LIMIT=600
NEXT_PUBLIC_SCALE_PLAN_MINUTE_LIMIT=1500
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
SES_FROM_EMAIL="SupoClip <onboarding@example.com>"
```

## Related Reading

- [Setup](./setup.md)
- [Troubleshooting](./troubleshooting.md)
- [Architecture](./architecture.md)

Select paid workers explicitly with `APIFY_YOUTUBE_DOWNLOADER_ACTOR`. Set
`APIFY_YOUTUBE_INPUT_ADAPTER` to `start_urls`, `progressive`, or `kv_videos`
to match the selected worker. Keep production actor identities and private
research outside the repository. No paid actor is selected by default.

Provider identities and pinned builds belong in `APIFY_YOUTUBE_PROVIDERS`, a private
JSON array of objects with `actor`, `build`, `adapter`, and optional `max_charge_usd` and `run_timeout_seconds` (60–1800).
Set the primary in `APIFY_YOUTUBE_DOWNLOADER_ACTOR` and ordered fallback IDs in
`APIFY_YOUTUBE_FALLBACK_ACTORS`. No paid actor is selected automatically. Fallback
actors after the primary stay unused unless `APIFY_EXPENSIVE_FALLBACKS=true`.
Those extra actors are the ones that can spend up to their `$6` run cap (leave
high-cap actors such as marielise.dev, scraper_one, and dami_studio out of the
list unless that flag is on). A single cheap primary, such as a titan-style
actor configured in the private environment, is the default. Supported
adapters are `kv_file`, `url_objects`, `signed_url`, `kv_media`, and `private_s3`. Configure only
workers whose input/output contract matches the selected adapter. Private provider
research and production environment files must stay outside the repository.

For `private_s3`, set `APIFY_STORAGE_BUCKET`, `APIFY_STORAGE_REGION`, and
`APIFY_STORAGE_KEY_TEMPLATE` (containing `{video_id}`), plus dedicated
`APIFY_STORAGE_WRITER_ACCESS_KEY_ID` / `APIFY_STORAGE_WRITER_SECRET_ACCESS_KEY`
and `APIFY_STORAGE_READER_ACCESS_KEY_ID` / `APIFY_STORAGE_READER_SECRET_ACCESS_KEY`.
The bucket must block public access, require HTTPS, enable versioning, and expire
current and noncurrent objects after one day. Give the worker write access only to
this bucket; give the application versioned read access and bucket versioning
inspection. Delivery accepts only a new object version created after submission,
with bounded file size, independent source duration, audio, and the requested
resolution. A known missing-output reporting error may be recovered only when
that complete private file passes validation. Unexpected errors use the configured
fallback pool. The shared download spending limit covers Apify charges; S3 storage
and network transfer are billed separately.

### Social post caption drafts

The publishing and scheduling form drafts editable captions with `claude-haiku-5-5`.
This requires `ANTHROPIC_API_KEY`, independently of the clip-selection `LLM`.
Drafting failures keep the caption editable and posting available. No migration is required.
