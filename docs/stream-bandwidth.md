# Twitch and Kick bandwidth protection

Stream downloads count response bytes against `YOUTUBE_DOWNLOAD_MAX_BYTES`
(default 2,000,000,000). Audio, video, provider fallbacks and repeated calls
within one task operation share the allowance. Retried bytes count again.
Exhaustion is terminal: another provider or full-video retry cannot reset it.

A download call fails when it receives no data for `YOUTUBE_PROXY_TIMEOUT_SECONDS`
(default 300 seconds), and in any case after `STREAM_DOWNLOAD_MAX_SECONDS`
(default 2400). Until 2026-11-14 the 300 seconds capped the whole call, which a
proxied connection (~1 MiB/s) could not meet for long VODs. Socket reads are
limited to ten seconds. Task cancellation signals the synchronous reader, then
waits for it to close before cleanup. Generation, editor preparation and clip
regeneration use this cancellation path.

yt-dlp's network section cutting stays disabled (it hands the transfer to
ffmpeg). Selected sections of a plain HLS rendition (Twitch and Kick VODs) are
instead fetched fragment by fragment through the same bounded client, four at a
time: only the fragments under the padded clip ranges, timed from the playlist's
`#EXTINF` durations, then cut locally. A one-hour 1080p60 Twitch VOD is ~3.3 GB
whole; twelve clips need roughly a tenth of it. Encrypted, byte-range, master or
discontinuous playlists fall back to downloading the rendition once, within the
shared allowance, and cutting from the local file. A later section request in
the same operation reuses that file. The fallback deliberately fails safely for
sources larger than the allowance; do not raise the allowance to accommodate an
unexplained download. Local cutting still uses ffmpeg.

The allowance is operation-local, not an account-wide billing ceiling. Distinct
editor operations, explicit new tasks and worker-process restarts have separate
allowances. Keep provider-side limits as an independent backstop.

Regression tests use loopback HTTP and synthetic HLS, with no paid traffic:
`tests/unit/test_stream_transfer.py`, `tests/unit/test_stream_source.py` and
`tests/unit/test_stream_hls_sections.py`.

During the 2026-11-13 incident, `dgeft87wbj63p.cloudfront.net` was blocked in
DataImpulse and cancellation was requested for task
`89254edf-68bb-445c-9a0c-f6e71fff4b5a`. The offending ffmpeg child was terminated
and the old worker stopped. At approximately 13:54 UTC, Sora and the API were
updated to `supoclip-bandwidth-fix:20261113`. Their production allowance was
reduced from the existing 10 GB override to 2 GB. Both effective settings were
verified, no offending child processes remained, and API, database, worker and
storage health checks returned 200. The host block remains enabled.

The image passed all 61 focused tests on each host before activation. Sora's
previous Compose and worker env files have `.before-bandwidth-fix-20261113`
backups in `/etc/supoclip/`. Hetzner's source, env and Compose rollback files
are in `/home/fuji/supoclip/backups/bandwidth-fix-20261113/before-api/`.
