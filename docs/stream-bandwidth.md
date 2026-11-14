# Twitch and Kick bandwidth protection

Stream downloads count response bytes against `YOUTUBE_DOWNLOAD_MAX_BYTES`
(default 2,000,000,000). Audio, video, provider fallbacks and repeated calls
within one task operation share the allowance. Retried bytes count again.
Exhaustion is terminal: another provider or full-video retry cannot reset it.

Every download call also has a deadline using `YOUTUBE_PROXY_TIMEOUT_SECONDS`
(default 300 seconds). Socket reads are limited to ten seconds. Task cancellation
signals the synchronous reader, then waits for it to close before cleanup.
Generation, editor preparation and clip regeneration use this cancellation path.

Network section cutting is disabled. A video rendition is downloaded once using
yt-dlp's native HTTP/HLS stack, within the shared allowance, then clips are cut
from the local file. A later section request in the same operation reuses it.
This deliberately fails safely for sources larger than the allowance; do not
raise the allowance to accommodate an unexplained download. Unsupported formats
cannot delegate network transfers to ffmpeg. Local cutting still uses ffmpeg.

The allowance is operation-local, not an account-wide billing ceiling. Distinct
editor operations, explicit new tasks and worker-process restarts have separate
allowances. Keep provider-side limits as an independent backstop.

Regression tests use loopback HTTP and synthetic HLS, with no paid traffic:
`tests/unit/test_stream_transfer.py` and `tests/unit/test_stream_source.py`.

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
