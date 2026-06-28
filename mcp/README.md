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
| `supoclip_create_clip_task` | ✓ | Start clipping a video → returns a `task_id` |
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

## Example prompts

- "Use supoclip to make clips from https://youtu.be/… with the hormozi caption template, then wait for it and download the top clip."
- "List my recent supoclip tasks and show the virality scores of the latest one's clips."
- "Export clip <id> from task <id> as a TikTok preset."

## License

AGPL-3.0-or-later, matching the SupoClip project.
