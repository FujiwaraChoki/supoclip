# SupoClip MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server for
[SupoClip](https://supoclip.com) — turn long-form videos (YouTube links or
direct URLs) into short, vertical, subtitled viral clips from any MCP client
(Claude Desktop, Claude Code, Cursor, etc.).

By default it talks to the **official hosted SupoClip API** at
`https://api.supoclip.com`. Point it at your own deployment by setting
`SUPOCLIP_API_URL`.

## What it can do

| Tool | Auth | Description |
|------|:----:|-------------|
| `supoclip_health` | – | API status + how this server is configured |
| `supoclip_list_caption_templates` | – | Available caption styles (default, hormozi, mrbeast, …) |
| `supoclip_list_transitions` | – | Available transition effects |
| `supoclip_broll_status` | – | Whether B-roll overlays are configured |
| `supoclip_list_fonts` | ✓ | Subtitle fonts available to your account |
| `supoclip_billing_summary` | ✓ | Plan, usage and remaining quota |
| `supoclip_list_workflows` | ✓ | What your account can make (highlights, series, topic, recap, trailer) and each one's limits |
| `supoclip_create_clip_task` | ✓ | Start clipping a video → returns a `task_id`; `workflow` picks what to make |
| `supoclip_list_tasks` | ✓ | List your tasks |
| `supoclip_get_task` | ✓ | Task status, progress and clips |
| `supoclip_wait_for_task` | ✓ | Poll until a task finishes |
| `supoclip_list_clips` | ✓ | List a task's generated clips |
| `supoclip_download_clip` | ✓ | Save a clip's MP4 to disk |
| `supoclip_export_clip` | ✓ | Re-encode + save with a platform preset (tiktok/reels/shorts) |
| `supoclip_cancel_task` / `supoclip_resume_task` / `supoclip_delete_task` | ✓ | Manage tasks |

Tools marked ✓ require an API key (or self-host credentials, see below).

## Getting an API key

1. Sign in at [supoclip.com](https://www.supoclip.com).
2. Go to [**Settings → API Keys**](https://www.supoclip.com/settings/api-keys).
3. Create a key and copy it (it's shown only once). It looks like `sk_…`.

Making clips needs a paid plan (Pro or Creator). On a free account the
discovery tools work, and creating a task returns a message with the upgrade
link instead of a clip.

## Connect to the hosted server

The hosted server is at `https://mcp.supoclip.com/mcp` (Streamable HTTP). Send
your key in the `Authorization` header. In every example below, replace
`sk_...` with your own key, and put **only the URL** in a client's URL field.

### Claude Code

```bash
claude mcp add --transport http supoclip https://mcp.supoclip.com/mcp --header "Authorization: Bearer sk_..."
```

### Cursor, Windsurf and other clients with remote URL support

```json
{
  "mcpServers": {
    "supoclip": {
      "url": "https://mcp.supoclip.com/mcp",
      "headers": {
        "Authorization": "Bearer sk_..."
      }
    }
  }
}
```

Cursor reads this from `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project).

### Claude Desktop

Claude Desktop's config file starts local servers, so bridge to the hosted
server with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) (needs
Node.js). Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "supoclip": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://mcp.supoclip.com/mcp",
        "--header",
        "Authorization:${SUPOCLIP_AUTH}"
      ],
      "env": {
        "SUPOCLIP_AUTH": "Bearer sk_..."
      }
    }
  }
}
```

On the hosted server, `supoclip_download_clip` and `supoclip_export_clip` save
files on the server, not on your computer. To get the MP4s locally, run the
server locally (below) or download clips from the task page.

### Older clients that only speak SSE

Use `https://mcp.supoclip.com/sse` as the URL, with the same
`Authorization: Bearer sk_...` header. Clients already configured with the
`/sse` URL keep working; Streamable HTTP requests sent there are answered too.

### If it doesn't connect

The server answers errors in plain words. `401` with "Missing SupoClip API
key" means the header didn't arrive; "placeholder" means `sk_...` (or
`sk_your_supoclip_api_key`) was not replaced with a real key; "invalid or was
revoked" means the key was deleted, so create a new one. Opening
`https://mcp.supoclip.com/` in a browser shows the endpoints and setup.

## Run it locally (stdio)

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/). Clips you
download are saved on your machine (`SUPOCLIP_DOWNLOAD_DIR`).

```bash
claude mcp add supoclip -e SUPOCLIP_API_KEY=sk_... \
  -- uvx --from "git+https://github.com/FujiwaraChoki/supoclip#subdirectory=mcp" supoclip-mcp
```

Or in a client's JSON config:

```json
{
  "mcpServers": {
    "supoclip": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/FujiwaraChoki/supoclip#subdirectory=mcp", "supoclip-mcp"],
      "env": {
        "SUPOCLIP_API_KEY": "sk_..."
      }
    }
  }
}
```

From a checkout, use `"command": "uv", "args": ["--directory", "/absolute/path/to/supoclip/mcp", "run", "supoclip-mcp"]`
instead. For a self-hosted backend, add `"SUPOCLIP_API_URL": "http://localhost:8000"` to `env`.

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPOCLIP_API_KEY` | – | Your API key (stdio; recommended auth). |
| `SUPOCLIP_API_URL` | `https://api.supoclip.com` | Backend base URL. Set for self-hosting. |
| `SUPOCLIP_APP_URL` | `https://www.supoclip.com` | Web app, used in links to create keys and pick a plan. |
| `SUPOCLIP_DOWNLOAD_DIR` | `./supoclip-downloads` | Where downloaded/exported clips are written. |
| `SUPOCLIP_TIMEOUT` | `60` | HTTP timeout (seconds) for non-download requests. |
| `SUPOCLIP_USER_ID` | – | Self-host only: authenticate by user id (see below). |
| `SUPOCLIP_AUTH_SECRET` | – | Self-host only: backend `BACKEND_AUTH_SECRET` for HMAC signing. |
| `SUPOCLIP_MCP_TRANSPORT` | `stdio` | `stdio`, or `http` to serve Streamable HTTP and SSE (`sse` and `streamable-http` mean the same as `http`). |
| `SUPOCLIP_MCP_HOST` / `SUPOCLIP_MCP_PORT` | `127.0.0.1` / `9100` | Listen address for `http`. |
| `SUPOCLIP_MCP_PUBLIC_URL` | `http://HOST:PORT` | Public base URL (including any mount path), shown in setup help and errors. |
| `SUPOCLIP_MCP_REQUIRE_BEARER_AUTH` | `false` | Require each client's own API key as a Bearer token. |
| `SUPOCLIP_MCP_MOUNT_PATH` | `/` | Serve every route under this prefix. |

### Self-hosting auth

If you run your own backend you have three options:

- **API key** (same as hosted): create a key and set `SUPOCLIP_API_KEY`.
- **Signed headers**: set `SUPOCLIP_USER_ID` + `SUPOCLIP_AUTH_SECRET` (your
  backend's `BACKEND_AUTH_SECRET`).
- **Unsigned**: if the backend runs with `ALLOW_UNSIGNED_BACKEND_AUTH=true`,
  just set `SUPOCLIP_USER_ID`.

Auth precedence is: API key → signed headers → unsigned user id.

## Hosting the HTTP server

`SUPOCLIP_MCP_TRANSPORT=http` serves both transports from one process:

| Path | What |
|------|------|
| `/mcp` | Streamable HTTP (current MCP spec) |
| `/sse` + `/messages/` | Legacy HTTP+SSE. POST/DELETE, or a GET with `Mcp-Session-Id`, go to Streamable HTTP |
| `/` | Same routing as `/sse`; a plain GET returns setup help as JSON |
| `/health` | Liveness |

```bash
SUPOCLIP_API_URL=https://api.supoclip.com \
SUPOCLIP_MCP_TRANSPORT=http \
SUPOCLIP_MCP_HOST=0.0.0.0 \
SUPOCLIP_MCP_PORT=9100 \
SUPOCLIP_MCP_PUBLIC_URL=https://mcp.supoclip.com \
SUPOCLIP_MCP_REQUIRE_BEARER_AUTH=true \
supoclip-mcp
```

With `SUPOCLIP_MCP_REQUIRE_BEARER_AUTH=true` every path except `/`, `/health`
and the help pages needs a SupoClip API key (`Authorization: Bearer sk_...`,
or `x-api-key`), checked against the backend the same way on both transports.
Behind a reverse proxy, pass the `Authorization` header through and turn off
response buffering (`proxy_buffering off` in nginx) so streams flush.

With Docker Compose, the `mcp` service runs this mode by default and binds to
`127.0.0.1:9100` for a reverse proxy.

## Workflows

`supoclip_create_clip_task` makes highlights by default. Pass `workflow` for
something else:

| `workflow` | Makes | `clip_length` sets |
|---|---|---|
| `highlights` | The best standalone clips | Each clip |
| `series` | One story as ordered, numbered parts (Part 1..N) that share exact cuts. `part_count`: 0 = auto, or 2–10 | Each part |
| `topic` | Every strong standalone moment about `topic` (required, 2–120 characters with a letter or digit; one Chinese, Japanese or Korean character is enough) | Each clip |
| `recap` | One short video covering the main points in order | Total length |
| `trailer` | One short teaser that never reveals the payoff | Total length |

`instructions` (up to 500 characters) steers any workflow, highlights included:
"skip the sponsor read", "focus on the hiring story". Topic and instructions
are normalized like the API does (invisible characters removed, spaces
collapsed) before the length checks.

Series need a video of at least 2 minutes, recaps and trailers 3 minutes, and
topic search 1 minute. Bad arguments fail locally before anything is created,
with the same rules as the API; `topic` with a workflow other than `topic`, or
`part_count` with a workflow other than `series`, is an error rather than
silently ignored.

Workflows may be gated per account: call `supoclip_list_workflows` first. When
it reports `enabled: false`, only plain highlights without instructions work
(otherwise the API answers 403 `WORKFLOW_NOT_AVAILABLE`).

A workflow that cannot deliver fails the task instead of padding it, and the
minutes are refunded. `supoclip_get_task` then shows `status: "error"` and an
`error_code`: `topic_not_found` (with `workflow_plan.error.details.suggested_topics`
to try instead), `source_too_short`, `no_story`, `recap_unsupported`,
`trailer_unbuildable`, `transcript_untimed`, or the retryable
`analysis_incomplete`, `plan_failed` and `render_incomplete`
(use `supoclip_resume_task`). Series clips carry `part_number`; every workflow
clip carries `workflow_role` and `workflow_meta`.

## Example prompts

- "Use supoclip to make clips from https://youtu.be/… with the hormozi caption template, then wait for it and download the top clip."
- "Turn https://youtu.be/… into a 5-part series and download the parts in order."
- "Find every moment about pricing in https://youtu.be/…; if the topic isn't covered, try the first suggested topic."
- "Make a one-minute recap of https://youtu.be/… focused on the money advice."
- "List my recent supoclip tasks and show the virality scores of the latest one's clips."
- "Export clip <id> from task <id> as a TikTok preset."

## License

AGPL-3.0-or-later, matching the SupoClip project.

### Clip length

`supoclip_create_clip_task` accepts `clip_length`: `auto` (default, 15–60s),
`short` (15–30s), `medium` (30–60s), or `long` (1–2 min). This controls the
selection range and rendering limit. Removing pauses and filler words can
shorten the finished clips. For a series it sets each part's length (auto
45–150s, short 30–60s, medium 45–90s, long 90–175s); for a recap or trailer it
sets the total (recap: auto scales with the video, short ~45s, medium ~75s,
long ~2 min; trailer: auto ~30s, short ~20s, medium ~40s, long ~1 min).

## Tests

```bash
cd mcp && uv run --with pytest pytest tests -q
```
