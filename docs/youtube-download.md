# YouTube download runbook

SupoClip can download YouTube media with `yt-dlp` through a residential proxy, then keep that media so later edits do not fetch it again. The proxy path is off until `YOUTUBE_PROXY_URL` is set. A datacenter IP (the production host is in this situation) gets YouTube's "Sign in to confirm you're not a bot" page from plain `yt-dlp`.

## What a configured job does

1. One sticky proxy session for the job, forced over IPv4.
2. Modest audio only (about 128 kbps m4a) for the duration check, transcription, audio peaks, and clip selection.
3. After clips are chosen, only those time ranges, padded (default ±10 seconds) with overlaps merged, at or below 1080p.
4. Audio and sections land in `{TEMP_DIR}/youtube-cache/{video_id}/`. Regenerating clips, editing captions, and opening the editor reuse that cache. A new section is fetched only when an edit extends past the cached ranges.
5. Each attempt logs `youtube_provider`, `youtube_video_id`, `youtube_download_kind`, `youtube_outcome`, `youtube_bytes`, and `task_id`. Axiom can sum bytes per provider. Proxy URLs are redacted.

Uploads and non-YouTube sources are unchanged. Without a proxy, YouTube jobs still download a full file, now capped at 1080p, and that file is cached too.

Section bytes go through yt-dlp's HTTP client. ffmpeg only trims and muxes local files. `--download-sections` is not used: ffmpeg does not route SOCKS proxies and does not reliably send authenticated HTTP proxy credentials, so the media would leave from the server IP.

Complete, unencrypted HLS media playlists are read through that same proxy
client, capped at 4 MB, and expanded using their `EXTINF` timings. Only the
overlapping segments and any initialization segment are passed to yt-dlp as
a shortened in-memory playlist. HLS audio renditions with omitted codec
metadata are supported. Automatic dubs are excluded when selecting original
audio for both analysis and sections.

Local cuts use accurate video trimming and encode audio once after joining,
so keyframe alignment and AAC padding do not shift later transcript spans.
This uses additional local CPU for the selected ranges. Cache manifests mark
the original-audio selection policy; older audio and section entries are
regenerated, while an existing full source remains available.

If a format cannot be sliced, the job tries a bounded full 1080p download through the proxy. A proxy network failure skips that repeat attempt. With a proxy configured, the fallback order is always **proxy → API Router → Apify primary (Titan in production) → configured emergency actors → direct yt-dlp**, regardless of the legacy primary-provider setting. `APIFY_EXPENSIVE_FALLBACKS` defaults to `false`, so only the primary Apify actor runs. Production explicitly enables Marielise then Scraper One with a shared $1 Apify budget and a $0.50 per-actor ceiling (Titan keeps its smaller $0.03 cap). The spending allowance is shared, not reset between actors. Leave high-cap actors (marielise.dev, scraper_one, dami_studio, and anything else with a multi-dollar run cap) out of `APIFY_YOUTUBE_FALLBACK_ACTORS` unless that flag is `true`. Actor ids stay in private environment configuration.

Proxy attempts and API Router default to 300 seconds each (`YOUTUBE_PROXY_TIMEOUT_SECONDS` and `RAPIDAPI_YOUTUBE_TIMEOUT_SECONDS`). Proxy socket requests and progress checks honor the attempt deadline; a timed-out provider hands off to the next provider. Production actor run timeouts are 300 seconds, with a separate bounded allowance for file transfer and verification. The worker job deadline is independent of actor deadlines.

## Bandwidth

Planning figure: about **0.18 GB per source hour**.

| Pass | Estimate |
| --- | --- |
| Audio at 128 kbps | ~0.06 GB per source hour |
| Selected video | ~12 clips per hour, ~60 seconds each after padding, ~1.5–2 Mbps talking-head 1080p → ~0.12 GB |

Together that is about 0.18–0.25 GB per source hour. At $1/GB that is about $0.18–$0.25, against roughly $1.65 per video hour on the paid downloader path and up to $6 for a fallback actor run. A full 1080p download of a source hour is several times the section path; the per-job cap `YOUTUBE_DOWNLOAD_MAX_BYTES` (default 2 GB) stops a runaway fetch.

These are planning estimates, not a bandwidth guarantee. Live or incomplete
playlists, encrypted media, byte ranges, gaps, and discontinuities use the
full-file fallback. That path consumes more bandwidth. Playlist bytes count
toward the shared job byte limit as well as downloaded media.

An isolated DataImpulse canary on 2026-10-21 downloaded full analysis audio
and selected HLS ranges from two public sources:

| Source length | Selected ranges including padding | Resolution | Audio + section bytes |
| --- | --- | --- | --- |
| 9m 48s | 70s | 720p | 24,387,300 |
| 63m 23.5s | 105s | 1080p | 113,810,315 |

Both reused the cache with zero additional proxy bytes. Audio correlation
against the original analysis track was within 37 ms at every checked span,
including near the end of the long source. These are measured examples,
not whole-hour bandwidth projections or a production rollout.

## Proxy

Use a provider that supports sticky sessions and HTTP proxies (DataImpulse is the reference at about $1/GB). Put the session and country in the URL template:

```env
YOUTUBE_PROXY_URL=http://user-country-{country}-session-{session}:pass@gw.example:823
YOUTUBE_PROXY_COUNTRY=us
```

For DataImpulse, use its documented username parameters rather than the
generic example above:

```env
YOUTUBE_PROXY_URL=http://LOGIN__cr.{country};sessid.{session}:PASSWORD@gw.dataimpulse.com:823
YOUTUBE_PROXY_COUNTRY=us
```

URL-encode special characters in the login and password. Session IDs normally
keep an exit IP for about 30 minutes; an offline residential peer can cause
the provider to replace the IP sooner. Verify the same exit is used for
metadata, audio, and selected sections in a canary before enabling production.

`{session}` becomes an 8–24 character id derived from the task id, so one job keeps one exit IP. `{country}` must be two letters; anything else is ignored and the placeholder is removed. Do not log the rendered URL.

yt-dlp is forced to IPv4 (`source_address=0.0.0.0`, the same as `-4`). Player clients stay at yt-dlp defaults.

## bgutil sidecar

PO tokens do not bypass an IP block on their own. They help once traffic already exits through the proxy. The image is `brainicism/bgutil-ytdlp-pot-provider` and listens on port 4416. It has no authentication, so the compose file does not publish that port to the host. Start it only with the profile:

```bash
COMPOSE_PROFILES=youtube-proxy docker compose up -d bgutil
```

Then point the backend and the worker at it:

```env
YOUTUBE_POT_PROVIDER_URL=http://bgutil:4416
```

The Python plugin `bgutil-ytdlp-pot-provider` is installed in the backend image next to yt-dlp. For a local venv:

```bash
cd backend && uv pip install bgutil-ytdlp-pot-provider
```

Deno is already installed in the backend image. yt-dlp uses it for YouTube's player challenge when the binary is on `PATH`.

## Keeping yt-dlp current

YouTube's player changes often. The backend image force-reinstalls yt-dlp at build time. Rebuild the backend and worker after a breakage:

```bash
docker compose build backend worker && docker compose up -d backend worker
```

Locally, `uv pip install --upgrade "yt-dlp[default]"` in `backend/`. Do not pin a player client in code to compensate; update yt-dlp instead.

## Canary

Use one short public video before pointing production at the proxy.

1. Set the proxy URL (and the PO token URL if the sidecar is up) on a single worker, not the whole fleet.
2. Submit that video and wait for clips.
3. In Axiom, filter `youtube_provider` and `youtube_bytes`. Expect `yt_dlp_proxy` with `kind=audio` then `kind=section`, and a byte total near the table above for the video's length. `outcome=failure` with a bot-check in the message means the exit IP is burned; the job should continue to the next provider.
4. Regenerate a clip or open the editor. The next lines should be `provider=cache` `outcome=cache_hit` `bytes=0`, or a section fetch only for a range that was not cached.
5. Confirm captions still match speech. A shifted timeline that is wrong shows up as captions sliding off the words.

`YOUTUBE_SOURCE_CACHE=off` restores delete-after-task if disk use matters more than repeat downloads. Cache entries older than `YOUTUBE_SOURCE_CACHE_TTL_SECONDS` (default 7 days) are removed on the next job.
