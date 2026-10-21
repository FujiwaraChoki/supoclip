# Observability

SupoClip reports errors and product analytics to **PostHog** and ships logs to
**Axiom**. Both are optional: with no tokens configured nothing is sent, which
is the default for self-hosted installs.

| Surface | PostHog | Logs |
|---------|---------|------|
| Backend API, ARQ worker | exceptions (`posthog` Python SDK) | JSON on stdout → Vector → Axiom |
| Web (Next.js) | analytics, session replay, exceptions | container stdout → Vector → Axiom |
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
| `SUPOCLIP_SERVICE` | `backend` or `worker`; set by docker-compose. |

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
one shared `access.log`. Vector picks up SupoClip's requests once its vhost
logs to its own files. Add to the `http` block (e.g.
`/etc/nginx/conf.d/supoclip-log.conf`):

```nginx
log_format supoclip_json escape=json
  '{"time":"$time_iso8601","remote_addr":"$remote_addr","method":"$request_method",'
  '"host":"$host","path":"$request_uri","status":$status,"bytes":$body_bytes_sent,'
  '"request_time":$request_time,"upstream_time":"$upstream_response_time",'
  '"referer":"$http_referer","user_agent":"$http_user_agent",'
  '"trace_id":"$upstream_http_x_trace_id"}';
```

and in each SupoClip `server` block (`sites-available/supoclip`):

```nginx
access_log /var/log/nginx/supoclip.access.log supoclip_json;
error_log  /var/log/nginx/supoclip.error.log warn;
```

Then `sudo nginx -t && sudo systemctl reload nginx`. Add the new files to the
existing logrotate rule for `/var/log/nginx/*.log` (it already matches them).
