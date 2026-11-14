# Twitch and Kick bandwidth protection

Stream downloads count response bytes against `YOUTUBE_DOWNLOAD_MAX_BYTES`
(default 2,000,000,000). Audio, video, provider fallbacks and repeated calls
within one task operation share the allowance. Retried bytes count again.
Exhaustion is terminal: another provider or a full-video retry cannot reset it.

A download call fails when it receives no data for `YOUTUBE_PROXY_TIMEOUT_SECONDS`
(default 300 seconds). That value is an idle limit: every byte received pushes
it back. It is not a wall clock. The overall cap is the longer of
`STREAM_DOWNLOAD_MAX_SECONDS` (default 2400) and a duration-based budget, and
it never exceeds one hour unless `STREAM_DOWNLOAD_MAX_SECONDS` is set higher.
Probing, cutting and re-encoding inside a download call count towards neither
limit. Media socket reads use 30 seconds. Metadata preflight keeps 10 seconds
so one stalled proxy read does not consume the whole preflight deadline.
Task cancellation signals the synchronous reader, then waits for it to close
before cleanup. Generation, editor preparation and clip regeneration use this
cancellation path.

The same path runs on every video worker. Scratch is that machine's `TEMP_DIR`.
Workers do not share a download directory.

yt-dlp's network section cutting stays disabled (it hands the transfer to
ffmpeg). Selected sections of a plain HLS rendition (Twitch and Kick VODs) are
instead fetched fragment by fragment through the same bounded client, four at a
time: only the fragments under the padded clip ranges, timed from the playlist's
`#EXTINF` durations, then cut locally. A one-hour 1080p60 Twitch VOD is about
3.3 GB whole; a handful of clips need a fraction of it. Each span is remuxed so
it starts at zero (MPEG-TS and fragmented MP4 keep their original timestamps),
and relative entries resolve against the playlist's URL after redirects. Apify's
HLS media uses the same path, direct first, then the proxy. Only renditions
with both video and audio are chosen: merging separate tracks would need
yt-dlp's ffmpeg. Encrypted, byte-range, master or discontinuous playlists fall
back to downloading the rendition once, within the shared allowance, and cutting
from the local file. A later section request in the same operation reuses that
file. Local cutting still uses ffmpeg. The fallback fails safely for sources
larger than the allowance. Do not raise the allowance to accommodate an
unexplained download.

Each stage logs the provider, source, URL host, HTTP status, exception type,
bytes, elapsed time, attempt, and the fallback. Proxy credentials and signed
query strings are not logged. A subscriber-only or private VOD, and a VOD that
is blocked in the regions we can download from, stops before a paid fallback
and says so.

The allowance is operation-local, not an account-wide billing ceiling. Distinct
editor operations, explicit new tasks and worker-process restarts have separate
allowances. Keep provider-side limits as an independent backstop.

Regression tests use loopback HTTP and synthetic HLS, with no paid traffic:
`tests/unit/test_stream_transfer.py`, `tests/unit/test_stream_source.py` and
`tests/unit/test_stream_hls_sections.py`.
