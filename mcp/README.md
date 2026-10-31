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

1. Sign in at [supoclip.com](https://supoclip.com).
2. Go to **Settings → API Keys**.
3. Create a key and copy it (it's shown only once). It looks like `sk_…`.

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPOCLIP_API_KEY` | – | Your API key (recommended auth). |
| `SUPOCLIP_API_URL` | `https://api.supoclip.com` | Backend base URL. Set for self-hosting. |
| `SUPOCLIP_DOWNLOAD_DIR` | `./supoclip-downloads` | Where downloaded/exported clips are written. |
| `SUPOCLIP_TIMEOUT` | `60` | HTTP timeout (seconds) for non-download requests. |
| `SUPOCLIP_USER_ID` | – | Self-host only: authenticate by user id (see below). |
| `SUPOCLIP_AUTH_SECRET` | – | Self-host only: backend `BACKEND_AUTH_SECRET` for HMAC signing. |

### Self-hosting auth

If you run your own backend you have three options:

- **API key** (same as hosted): create a key and set `SUPOCLIP_API_KEY`.
- **Signed headers**: set `SUPOCLIP_USER_ID` + `SUPOCLIP_AUTH_SECRET` (your
  backend's `BACKEND_AUTH_SECRET`).
- **Unsigned**: if the backend runs with `ALLOW_UNSIGNED_BACKEND_AUTH=true`,
  just set `SUPOCLIP_USER_ID`.

Auth precedence is: API key → signed headers → unsigned user id.

## Install & run

Requires Python 3.10+. With [uv](https://docs.astral.sh/uv/):

```bash
cd mcp
uv run supoclip-mcp        # runs the stdio server
```

Or install into the current environment:

```bash
pip install -e .
supoclip-mcp
```

## Hosted SSE server

For clients that add a remote MCP URL, run the server over SSE and require a
SupoClip API key as the client Bearer token:

```bash
SUPOCLIP_API_URL=https://api.supoclip.com \
SUPOCLIP_MCP_TRANSPORT=sse \
SUPOCLIP_MCP_HOST=0.0.0.0 \
SUPOCLIP_MCP_PORT=9100 \
SUPOCLIP_MCP_PUBLIC_URL=https://mcp.supoclip.com \
SUPOCLIP_MCP_REQUIRE_BEARER_AUTH=true \
supoclip-mcp
```

Then configure the MCP client with:

```text
Server URL: https://mcp.supoclip.com/sse
Transport: SSE
Authentication: Bearer Token
Token: sk_your_supoclip_api_key
```

With Docker Compose, the `mcp` service runs this mode by default and binds to
`127.0.0.1:9100` for a reverse proxy.

## Use with Claude Desktop / Claude Code

Add to your MCP client config (e.g. `claude_desktop_config.json`, or via
`claude mcp add`). Example using `uv` to run from a checkout:

```json
{
  "mcpServers": {
    "supoclip": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/supoclip/mcp", "run", "supoclip-mcp"],
      "env": {
        "SUPOCLIP_API_KEY": "sk_your_key_here"
      }
    }
  }
}
```

For a self-hosted backend, add `"SUPOCLIP_API_URL": "http://localhost:8000"` to `env`.

With Claude Code:

```bash
claude mcp add supoclip -e SUPOCLIP_API_KEY=sk_your_key_here \
  -- uv --directory /absolute/path/to/supoclip/mcp run supoclip-mcp
```

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
