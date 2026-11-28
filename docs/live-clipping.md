# Live clipping

Creator and Team accounts can clip a YouTube, Twitch or Kick stream while it is still
live:

- **Snapshot:** clip the newest part of the stream now: the last 15, 30, 60 or 120
  minutes, or everything so far.
- **Keep clipping:** the same, and then a new part every 30 minutes until the stream
  ends, plus one last part after it ends. Each part is its own project with its own
  clips.

It ships behind the `live_clipping` feature flag (seeded off on web and iOS, admins
bypass) and `LIVE_CLIPPING_PLATFORMS`.

## How it works

Nothing records a stream on our side. Every platform already keeps a recording of a
live stream from its first second, and a live part is a *part of that recording*,
handled by the same code as a picked part of a long VOD (`source_range.py`):

| Platform | Live link | Recording a part is cut from |
| --- | --- | --- |
| Twitch | `twitch.tv/<channel>` | The in-progress VOD (`twitch.tv/videos/<id>`). Its HLS playlist covers the stream from the start, with no `#EXT-X-ENDLIST` yet. |
| Kick | `kick.com/<channel>` | The ongoing stream's VOD (`kick.com/<channel>/videos/<uuid>`), the newest entry of the channel's videos. |
| YouTube | `watch?v=`, `/live/<id>`, `@channel/live` | The live video's DVR: DASH segments addressable by number from the start of the stream (`sq=N` covers `[N·d, (N+1)·d)`). |

1. `POST /tasks/` with a live link and no `source.live` answers **422
   `LIVE_STREAM_DETECTED`** with the stream (channel, title, elapsed time), whether the
   account may clip it (`can_clip_live`) and the longest part its plan allows. The web
   app shows the live panel; the agent and MCP ask which part to clip. A YouTube watch
   URL is only found to be live when its length preflight comes back empty.
2. With `source.live = {"window_seconds": 1800, "follow": false}` the API resolves the
   recording (`live_source.resolve_live_stream`), turns the window into
   `start_seconds`/`end_seconds` on it (ending ~10–30 s before the live edge, which may
   not be published yet), and creates an ordinary task on the recording URL. The range
   is stored with `live: true`. Only the part's minutes are charged.
3. The worker downloads only the part's audio, transcribes and analyses it, then
   downloads only the selected clip ranges, as for any part of a VOD:
   - **Twitch/Kick:** the existing HLS fragment slicer (`stream_source.py`). A live
     part skips the Apify actors (their results never match a growing VOD) and, with
     `LIVE_PREFER_DIRECT`, tries the server's own IP before the proxy.
   - **YouTube:** `youtube_proxy.live_dvr_fragments` lists the DVR segments under the
     part, yt-dlp's own HTTP client downloads them (through the proxy), and ffmpeg
     remuxes them locally (each segment repeats the MP4 header). A full download of a
     live video is never attempted. A video that has ended and is still processing
     (`post_live`) uses the same DVR path; once processed it is an ordinary video.
4. `follow: true` also creates a **live session** (`live_sessions`, `live_session_parts`).
   The cron worker runs `advance_live_sessions` every minute. For each due session it
   checks the stream (metadata only) and, once `LIVE_FOLLOW_INTERVAL_SECONDS` of new
   stream exist, creates the next part through `_create_task` with the first request's
   options. A part re-reads the last `LIVE_FOLLOW_OVERLAP_SECONDS` of the previous one,
   so a moment on the boundary isn't cut in half. Moments that ended inside the overlap
   are dropped (`SourceRange.fresh_after`), and only the new minutes are billed. Each
   part has the idempotency key `live-<session>-<part>`, so a retried run never creates
   it twice.
5. When the stream ends (or the channel starts a new broadcast), one last part covers
   the rest of the finished recording, then the session ends.

A session also stops when the person stops it (`POST /live-sessions/{id}/stop`), the
account runs out of minutes (402) or loses the plan, the flag is turned off,
`LIVE_FOLLOW_MAX_HOURS` of stream are covered, or the stream can't be reached six
checks in a row. Parts already created keep processing and keep their clips.

### Refusals

| Code | When |
| --- | --- |
| `LIVE_STREAM_DETECTED` (422) | The link is live and the request didn't say what to clip. |
| `LIVE_REQUIRES_CREATOR` (402) | The account isn't on Creator or Team. |
| `STREAM_NOT_LIVE` (422) | A channel or `/live` link that isn't live right now. |
| `LIVE_NO_RECORDING` (422) | Twitch channels that don't save past broadcasts; a Kick VOD that isn't listed yet. |
| `LIVE_TOO_SHORT` (422) | Less than a minute of stream exists. |
| `LIVE_PLATFORM_UNAVAILABLE` (422) | The platform is left out of `LIVE_CLIPPING_PLATFORMS`. |
| `LIVE_UNAVAILABLE` (503) | The platform couldn't be reached. |

With the flag off, live links are refused exactly as before ("Live streams aren't
supported" for channel links, `VIDEO_DURATION_UNAVAILABLE` for a live YouTube video).

## Cost

A live part costs what the same part of the VOD would cost afterwards, and it is
charged the same way: in minutes of source video. Continuous clipping adds only the
overlap (90 s per 30-minute part: about 5% more transcription and analysis, not billed)
and a metadata check per part.

Bandwidth measured on 2026-11-28 against live streams (direct connection):

| | Audio of the part | Clip sections (1080p60, padded) |
| --- | --- | --- |
| Twitch | 1.5 MB per 2 min (~45 MB/h, audio-only rendition) | 7.0 MB per 40 s |
| Kick | 1.5 MB per 2 min (~44 MB/h, smallest rendition) | 18.7 MB per 40 s |
| YouTube | ~60 MB/h (planning figure, `docs/youtube-download.md`) | ~120 MB/h of stream |

With about 12 clips per stream hour (~13 minutes of padded sections), one stream hour
is roughly **0.18 GB on Twitch, 0.4 GB on Kick and 0.2 GB on YouTube**. At about $1/GB
of residential proxy that is $0.18, $0.40 and $0.20, against about $1.00 of Creator
revenue per hour of allowance ($24.99 for 1,500 minutes). Twitch and Kick media try the
server's own IP first (`LIVE_PREFER_DIRECT`), so their proxy cost is zero whenever their
CDNs serve the server. YouTube needs the proxy in production. "Is it still live?" costs
nothing on YouTube when the Data API key works, a few hundred KB through the proxy
otherwise, and a few KB on Twitch and Kick.

So continuous clipping pays for itself: it is metered like any video, a session stops at
the account's minutes, and the only open-ended cost (Kick through the proxy) is bounded
by the same minutes. Watch `stream_provider` / `stream_bytes` and
`youtube_download_kind=live_audio|live_section` in Axiom during the canary.

## Rollout

1. Deploy. The migration creates the session tables and seeds `live_clipping` off.
2. Canary as an admin (the flag bypasses for admins):
   - a Twitch channel and a Kick channel that are live: last 30 minutes, then keep clipping
     for at least an hour (two parts and their clips);
   - a YouTube live: last 15 minutes. Check Axiom for `youtube_download_kind=live_audio`
     then `live_section` with `outcome=success`, and that captions line up with speech.
     The YouTube DVR path is covered by tests against a loopback server only; this is its
     first contact with YouTube;
   - stop a session from the task page; let one run until the stream ends.
3. Check Axiom for which provider served live Twitch/Kick media (direct or proxy) and the
   bytes per part.
4. Turn `live_clipping` on for web in /admin. Set `NEXT_PUBLIC_LIVE_CLIPPING=on` so pricing
   lists it, move the `live-clipping` tour's `releasedAt` to that day, and update the copy
   that still says live streams aren't supported (`frontend/src/lib/seo-pages.ts`,
   `creator-guides.ts`, `localized-guides/*`, `messages/*/seo.json`, `marketing.json`
   `faq.items.sources`, `blog.json`, the README).
5. iOS stays off until the app handles `LIVE_STREAM_DETECTED`.

## Code

- `backend/src/live_source.py`: live links, resolving a stream to its recording, the part a request clips.
- `backend/src/live_sessions.py`: sessions, the cron step, part creation.
- `backend/src/api/routes/tasks.py` (`_capture_live`), `api/routes/live_sessions.py`.
- `backend/src/youtube_proxy.py` (live DVR), `youtube_source.py`, `stream_source.py` (`_live_provider_order`).
- Frontend: `lib/live-clipping.ts`, `components/live-stream-panel.tsx`,
  `components/app/live-session-panel.tsx`, `app/api/live-sessions/`.
- Tests: `tests/unit/test_live_source.py`, `tests/unit/test_youtube_live_dvr.py`,
  `tests/integration/test_live_clipping.py`.
