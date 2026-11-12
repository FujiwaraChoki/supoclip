# Observability

SupoClip reports errors and product analytics to **PostHog** and ships logs to
**Axiom**. Both are optional: with no tokens configured nothing is sent, which
is the default for self-hosted installs.

| Surface | PostHog | Logs |
|---------|---------|------|
| Backend API, ARQ worker | exceptions, product events (`posthog` Python SDK) | JSON on stdout → Vector → Axiom |
| Web (Next.js) | analytics, product events, session replay, exceptions | container stdout → Vector → Axiom |
| Desktop (Electron) | analytics, session replay, exceptions | — |
| iOS | analytics, crashes | — |

Production uses the PostHog project **SupoClip** (EU cloud, project id
`314504`) and the Axiom dataset **`supoclip-prod`** (org `supoclip-tjzv`).

## Following an error from PostHog to the logs

Every API request carries a trace id. Clients send `x-trace-id`; the backend
generates one otherwise, echoes it in the response header and in error bodies,
and stamps it on every log line. Worker jobs use `task-<task_id>` and
`social-<post_id>`.

Exceptions in PostHog carry the same `trace_id` property and, when a user was
authenticated, the Better Auth user id as the distinct id. To see what the
backend did around an error, query Axiom with the trace id:

```kusto
['supoclip-prod']
| where trace_id == "task-6f1c..."
| sort by _time asc
```

All logs for one user: `| where user_id == "<user id>"`. Errors only:
`| where level == "ERROR"`. Each line has a `service` field (`backend`,
`worker`, `frontend`, `mcp`, `nginx`, ...).

## Backend and worker

`src/observability.py` configures logging. Every record logged with a
traceback (`logger.exception(...)` or `exc_info=True`) is also sent to PostHog
error tracking, as are uncaught exceptions. Records without a traceback only
go to the logs.

| Variable | Purpose |
|----------|---------|
| `POSTHOG_PROJECT_TOKEN` | Project token (`phc_...`). Empty disables PostHog. |
| `POSTHOG_HOST` | Ingest host, default `https://eu.i.posthog.com`. |
| `SUPOCLIP_SERVICE` | `backend`, `worker`, or `cron-worker`; set by docker-compose. |

A video-queue job that waits longer than `QUEUE_DELAY_ALERT_SECONDS` (default
10 minutes) before a worker starts it sends one PostHog exception,
`$exception_fingerprint` `queue_delay_exceeded`, with `queue_depth` and
`wait_seconds`. The worker sends at most one of these per
`QUEUE_DELAY_ALERT_INTERVAL_SECONDS` (default 15 minutes), which is what the
critical-alert webhook should match. The distinct id is `supoclip-worker`.
The event has no account id. A job that waited only because that account was
already rendering does not use this clock: it is queued again with a new
enqueue time.

## Product funnel events

Custom events measure signup → first generation → payment → retention. Each
one is sent only when its side has a PostHog token (`NEXT_PUBLIC_POSTHOG_KEY`
for the web app and its server routes, `POSTHOG_PROJECT_TOKEN` for the backend
and worker). Sending never raises or waits on PostHog.

The distinct id is always the Better Auth user id, so browser, Next server and
backend events land on one person. Properties never carry emails, names, source
URLs, file names, transcripts, instructions, topics, captions or creator codes.

Names and properties are typed in one place per side:
`frontend/src/lib/analytics.ts` (browser), `frontend/src/server/analytics.ts`
(Next server) and `backend/src/analytics.py` (backend and worker).

### Browser (`trackEvent`)

| Event | Fired | Properties |
|-------|-------|------------|
| `signup_completed` | Email sign-up succeeded (`components/auth/sign-up.tsx`) | `method` |
| `source_entered` | A supported link is in the create composer, or a video file was picked; once per distinct source | `source_kind` (`youtube`, `twitch`, `kick`, `upload`) |
| `generate_clicked` | Generate pressed with a valid request, before the plan check | `workflow`, `source_kind` (or `unknown`), `has_instructions`, `blocked_by_upgrade` |
| `upgrade_dialog_shown` | The upgrade dialog opens | `reason` (`start`, `limit`, `video_too_long`, `custom_fonts`, `explore`), `plan`, `source` |
| `editor_opened` | The clip studio loaded a finished task; once per task | `clip_count` |
| `ai_edit_submitted` | The studio accepted an AI edit, or refused it at the daily limit | `outcome` (`accepted`, `limit_reached`) |
| `clip_downloaded` | A clip download started from the task page or studio | `surface` (`task`, `editor`, `editor_queue`), `format` (preset or `original`) |
| `clips_zip_downloaded` | The ZIP of every clip finished downloading | `surface` |

There is no `pricing_viewed`: the pricing page is covered by `$pageview`, and
the in-app pricing surface is the upgrade dialog.

### Next server (billing)

| Event | Fired | Properties |
|-------|-------|------------|
| `checkout_started` | `POST /api/billing/checkout` returned a Stripe URL | `plan`, `interval`, `provider` (`stripe`), `has_creator_code`, `flow` (`checkout`, `plan_change`, `payment_link`) |
| `subscription_started` | Stripe `checkout.session.completed` with a paid plan; RevenueCat `INITIAL_PURCHASE` | `plan`, `interval`, `provider` (`stripe`, `apple`), `trial`, `has_creator_code` (Stripe) |
| `subscription_cancel_scheduled` | Stripe `customer.subscription.updated` that newly schedules a cancellation (after the email); RevenueCat `CANCELLATION` | `plan`, `interval`, `provider`, `reason` (RevenueCat's `cancel_reason`) |
| `subscription_canceled` | Stripe `customer.subscription.deleted`; RevenueCat `EXPIRATION` that ended the plan | `plan`, `interval`, `provider`, `kept_creator_pro` |
| `subscription_renewed` | Stripe `invoice.paid` / `invoice.payment_succeeded` with `billing_reason: subscription_cycle`; RevenueCat `RENEWAL` | `plan`, `interval`, `provider` |

Subscription events also set the person properties `plan` and
`subscription_status` (`$set`). Server events carry `platform: web-server`.

Webhook events are idempotent. The existing `stripe_webhook_events` /
`revenuecat_webhook_events` rows already drop redeliveries; the events are held
until the handler has succeeded, so a delivery that fails and is retried is
counted once. Each event's PostHog `uuid` is derived from the webhook event id
and its timestamp is the provider's, so PostHog drops a resend too. Stripe sends
both invoice events for one renewal: the first to insert
`posthog:subscription_renewed:<invoice id>` into `stripe_webhook_events` sends
it. That row is only written when PostHog is configured.

### Backend and worker

| Event | Fired | Properties |
|-------|-------|------------|
| `task_created` | `POST /tasks/` persisted the task and queued its job | `task_id`, `workflow`, `source_kind` (`youtube`, `twitch`, `kick`, `upload`, `video_url`), `source_duration_seconds`, `has_instructions`, `plan`, `client` (`web`, `ios`, `api_key`, `mcp`), `processing_mode`, `output_format`, `clip_length` |
| `task_completed` | The worker marked the task completed | `task_id`, `workflow`, `has_instructions`, `source_kind`, `clip_count`, `processing_seconds`, `cache_hit` |
| `task_failed` | The worker marked the task failed (not cancelled) | `task_id`, `workflow`, `source_kind`, `error_code`, `processing_seconds`, `cache_hit`, `kept_outputs` |
| `clip_exported` | The task's owner downloads a file: a preset export, the original (`?download=true`), the ZIP, or a studio export | `task_id`, `surface` (`preset`, `original`, `zip`, `editor`), `format`, `workflow`, `clip_count` (ZIP), `client` |
| `social_post_published` | A post went live on its platform | `post_id`, `provider`, `scheduled`, `attempts` |

Backend events carry the `platform` super property (`backend` or `worker`) and
the request's `trace_id` (`task-<id>` in the worker). `client` is `mcp` when an
API-key request comes from the MCP server (`User-Agent: supoclip-mcp/<version>`),
`ios` for the iOS app's header or user agent, else `web`.

### Suggested funnel

`signup_completed` → `source_entered` → `generate_clicked` →
`upgrade_dialog_shown` → `checkout_started` → `subscription_started` →
`task_completed` → `clip_downloaded` or `clip_exported`. Retention: a trend of
`subscription_renewed` against `subscription_cancel_scheduled` and
`subscription_canceled`, broken down by `plan` and `provider`.

## Shipping logs to Axiom

The `vector` service in `docker-compose.yml` (config in
`observability/vector.yaml`) reads the logs of every `supoclip-*` container
through the Docker socket, parses the backend's JSON lines into fields, drops
health-check noise and sends everything to Axiom. It only starts with the
`axiom` profile:

```bash
# in the production .env
COMPOSE_PROFILES=axiom
AXIOM_TOKEN=xaat-...        # ingest-only token for the dataset
AXIOM_DATASET=supoclip-prod

docker compose up -d vector
```

### nginx

On the production host nginx runs outside Docker and serves several sites into
one shared `access.log`. The SupoClip vhosts (`www` and `api`, port 443) also
write their own files, which Vector picks up. They show what the backend never
sees: 413s for oversized uploads, 502s while a container restarts, 504s on slow
uploads, and frontend page/asset requests. Upload body size and the
response-header timeout for `/api/upload` and `POST /upload` are also set in
that host config; the values that keep a multi-gigabyte upload from ending in
504 are in `docs/media-storage.md`.

`/etc/nginx/conf.d/supoclip-log.conf`:

```nginx
# $uri, not $request_uri: query strings can carry tokens.
log_format supoclip_json escape=json
  '{"time":"$time_iso8601","host":"$host","method":"$request_method","path":"$uri",'
  '"status":$status,"bytes":$body_bytes_sent,"request_length":$request_length,'
  '"request_time":$request_time,"upstream_status":"$upstream_status",'
  '"upstream_time":"$upstream_response_time","remote_addr":"$remote_addr",'
  '"referer":"$http_referer","user_agent":"$http_user_agent",'
  '"trace_id":"$upstream_http_x_trace_id"}';
```

In the `www.supoclip.com` and `api.supoclip.com` 443 server blocks of
`sites-available/supoclip` (the shared logs are listed again because a
server-level `access_log` replaces the inherited one):

```nginx
access_log /var/log/nginx/access.log;
access_log /var/log/nginx/supoclip.access.log supoclip_json;
error_log /var/log/nginx/error.log;
error_log /var/log/nginx/supoclip.error.log warn;
```

Apply with `sudo nginx -t && sudo systemctl reload nginx`. The existing
logrotate rule for `/var/log/nginx/*.log` covers the new files.
