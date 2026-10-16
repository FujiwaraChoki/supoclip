# supoclip-ytdl

SupoClip's own YouTube download API: yt-dlp behind residential proxies, with
IP rotation, a PO-token sidecar, a result cache and per-route cost accounting.
It replaces paid download APIs (Oxylabs, VideoScale) with a proxy bill.

## Why downloads get blocked

YouTube blocks datacenter IPs (Hetzner included) with "Sign in to confirm
you're not a bot" before any format is returned. From a residential IP,
current yt-dlp clients fetch 1080p H.264 without cookies or PO tokens. So the
service routes every request, extraction *and* media, through a residential
exit, and keeps yt-dlp on the nightly channel because YouTube fixes land there
first.

Media URLs are bound to the IP that extracted them, so each attempt uses one
sticky proxy session for both. A failed attempt gets a new session (new IP),
and partially downloaded HTTPS formats resume where they stopped.

## Run

```bash
# with the main stack
COMPOSE_PROFILES=ytdl docker compose up -d ytdl ytdl-pot

# locally (a home connection usually is not blocked)
cd ytdl && uv sync
YTDL_DATA_DIR=/tmp/ytdl YTDL_API_TOKEN=dev uv run supoclip-ytdl   # :8080
```

Point the backend at it with `YOUTUBE_DOWNLOAD_PROVIDER=ytdl`,
`YTDL_API_URL=http://ytdl:8080` and the same `YTDL_API_TOKEN`. Set
`YOUTUBE_DOWNLOAD_FALLBACK_PROVIDER=oxylabs` during rollout to fall back to a
paid provider when every exit is blocked (never for private/removed videos).

## Proxies

`YTDL_PROXY_URL` is a proxy URL where `{session}` is replaced by a fresh random
token per attempt. Use your provider's sticky-session username syntax, for example:

| Provider | `YTDL_PROXY_URL` |
|---|---|
| IPRoyal | `http://USER:PASS_session-{session}_lifetime-30m@geo.iproyal.com:12321` |
| DataImpulse | `http://USER__sessid.{session}:PASS@gw.dataimpulse.com:823` |
| Decodo | `http://user-USER-session-{session}-sessionduration-30:PASS@gate.decodo.com:7000` |

Check the exact syntax in your provider's dashboard; it changes. Sessions
should last at least 30 minutes so a long download keeps its IP. Per-request
rotation without sticky sessions does not work: media URLs only play from the
IP that extracted them.

For several tiers (cheap first), use `YTDL_ROUTES` instead:

```bash
YTDL_ROUTES='[
  {"name": "direct", "attempts": 1},
  {"name": "isp", "proxy": "http://user:pass@isp-proxy:8000", "attempts": 2},
  {"name": "residential", "proxy": "http://user-session-{session}:pass@gate:7777", "attempts": 4}
]'
```

Each route has a circuit breaker: after `YTDL_CIRCUIT_THRESHOLD` consecutive
blocks it is skipped for `YTDL_CIRCUIT_COOLDOWN_SECONDS` (doubling, up to 8x),
so a blocked tier stops costing time. Fatal errors (private, removed,
age-restricted, members-only, too long) stop immediately without trying more
exits.

Bandwidth is the cost. Expect roughly 0.15–0.5 GB per hour of video at 720p
and 0.4–1.5 GB at 1080p, depending on motion. `YTDL_VIDEO_QUALITY` is the
biggest lever. Repeat requests for the same video are served from the cache
(`YTDL_CACHE_TTL_HOURS`, `YTDL_CACHE_MAX_GB`) at no proxy cost.

## API

All `/v1` routes need `Authorization: Bearer $YTDL_API_TOKEN`.

| Method | Path | |
|---|---|---|
| `POST` | `/v1/downloads` | `{"url" or "video_id", "max_height"?, "max_bytes"?}` → job (202, or 200 when cached). Identical requests share one job. |
| `GET` | `/v1/downloads/{id}` | status `queued`/`running`/`completed`/`failed`/`cancelled`, stage, bytes, `error_code`, `result` |
| `GET` | `/v1/downloads/{id}/file` | the MP4 (supports Range); 410 once evicted |
| `DELETE` | `/v1/downloads/{id}` | cancel |
| `GET` | `/v1/stats` | per-route attempts/successes/blocks/bytes, circuit state, cache size |
| `GET` | `/healthz` | unauthenticated liveness + yt-dlp version |

`error` is safe to show to users; `error_code` is one of `private`,
`age_restricted`, `members_only`, `not_started`, `live`, `removed`,
`unavailable`, `invalid_url`, `too_long`, `too_large`, `geo_blocked`,
`bot_check`, `forbidden`, `no_formats`, `proxy_error`, `network`, `stalled`,
`incomplete`, `timeout`, `cancelled`, `unknown`, `internal`.

## Staying current

The container upgrades yt-dlp on start (`YTDL_UPDATE_CHANNEL=nightly|stable`,
`YTDL_UPDATE_ON_START=false` to pin). Every `YTDL_UPDATE_CHECK_HOURS` it checks
PyPI and, when a newer build exists, restarts once idle. After six busy hours it
stops taking new work and drains first. Clients retry 503s and restarts.

Player clients are tried in `YTDL_PLAYER_CLIENTS` order (`;`-separated groups,
`default` = yt-dlp's choice), moving to the next group after a block on the same
route. `mweb` relies on the `ytdl-pot` PO-token sidecar.

## Tests

```bash
uv run pytest                        # unit tests
YTDL_LIVE_TESTS=1 uv run pytest      # + real download through a local rotating proxy
```
