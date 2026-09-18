# Local application test report — 2026-09-16

Tested the working tree using isolated PostgreSQL (55432), Redis-compatible Valkey
(56379), FastAPI (8100), an ARQ worker, Next.js development and production servers,
and system Chromium. YouTube metadata and downloads used **local yt-dlp**.
Apify credentials and admin overrides were absent; no Apify service calls were made.
Transcription used local Whisper `base`; analysis used local Ollama `qwen3.8:27b`.

## Results

| Checks | Result |
| --- | --- |
| Backend unit and PostgreSQL integration suite | 160 passed |
| Frontend unit suite | 67 passed across 19 files |
| Real browser workflows | 8 passed |
| Production browser smoke suite | 2 passed |
| Mocked UI failure/recovery regressions | 4 passed |
| Real API/ARQ/media runner | 6 scenario groups passed |
| Caption cache recovery and repeat processing | Both runs passed; retained word timings and visually verified captions |
| MCP stdio protocol | 17 tools discovered; 14 tool calls passed |
| Production page sweep | 10 public routes and 3 authenticated routes passed; missing route returned 404 |
| Frontend lint, TypeScript, production build | Passed |
| Python compilation and `git diff --check` | Passed |

The real browser suite exercised signup/signout, invalid login, preferences,
API-key creation/authentication/revocation, custom font upload/serve/delete, YouTube
submission, live completion without reload, playback, narrow layouts, caption saves
and persistence, trim, repeated split, merge, sharing/revocation, direct video upload,
clip/task deletion, and browser/server exports.

FFprobe confirmed exported video dimensions and audio tracks. Decoding the browser
export's audio confirmed that a 50% volume setting produced approximately half the
original RMS amplitude. Range requests returned the requested 1,024 bytes with 206.
Repeated splitting preserved neighboring clips and their filenames.

Additional real worker checks covered invalid media reaching a terminal error,
cancellation followed by worker exit and resumption, renaming, regeneration with
changed caption style/size/color, original/pan/split framing, subtitle-free output,
pause/filler cleanup, invalid export presets and duplicate merge IDs.

The MCP checks included health, templates, transitions, B-roll configuration status,
fonts, billing summary, task listing, creation, waiting, retrieval, clip listing,
download, export and deletion. These used the real local backend.

## Bugs fixed during testing

- Clip insertion omitted IDs and failed on a database initialized by Prisma.
- A failed database write left the transaction aborted, preventing error-state persistence.
- Splitting a clip could overwrite the next clip through the order-based upsert.
- Editing/deleting clips left the task's stored clip-ID list stale.
- Clip deletion checked the task owner but not whether the clip belonged to that task.
- Dead-letter job details were accessible without admin authentication.
- SSE completion closed Redis before its subscription generator, causing cleanup errors;
  cancelled tasks also now close their progress stream.
- Authentication created separate Prisma clients across reloads, exhausting database
  connections. It now shares the existing singleton. The repeat navigation run stayed
  at eight database connections after the fix, versus exhausting 100 beforehand.
- Permanently cached custom fonts remained accessible after deletion. Serving now revalidates.
- Falsy/malformed preference values and malformed task/edit payloads produced invalid
  persistence or server errors instead of validation responses.
- YouTube cleanup removed word-timing caches, leaving repeated jobs with transcript
  text but no timed captions. Cleanup now retains timings and the pipeline repairs old caches.
- The documented `WHISPER_MODEL_SIZE` variable was ignored; it is now supported, with
  `WHISPER_MODEL` taking precedence.
- Video players requested a nonexistent placeholder image; upload size copy said
  500 MB while the implemented limit is 12 GB.
- The standard Playwright launcher forwarded an extra command separator and mixed
  isolated mocked checks with database-backed checks. Test suites are now separated.

## Evidence and reproduction

See [local testing instructions](testing-local.md),
[real browser scenarios](../frontend/e2e/local-workflow.spec.ts), and
[real API/worker runner](../backend/tests/live/run_local_workflows.py).

Session logs and rendered evidence are retained on this workstation under
`/tmp/supoclip-full-test/`. The `evidence/` directory includes desktop/mobile
screenshots and browser/server MP4 exports. `caption-cache-1.png` shows timed
captions on the repeated YouTube run. Temporary services were stopped after testing.
No commits or deployments were made.

## Limits and remaining observations

- Live media used a short, single-speaker YouTube fixture and uploads of that file.
  This does not establish hour-long/4K reliability, multi-speaker tracking quality,
  or production concurrency/load capacity.
- The installed MediaPipe release lacks `mp.solutions`. The existing OpenCV fallback
  worked and detected faces; MediaPipe-specific tracking was not exercised.
- Whisper `base` misheard “trunks” as “puns” in this fixture. Caption editing corrected
  the text successfully; passing pipeline tests does not guarantee transcript accuracy.
- Real payment charges, Stripe/RevenueCat delivery, SES/Discord delivery, hosted AI
  providers and paid B-roll retrieval were not exercised. Applicable billing and
  webhook unit tests passed without sending messages or charging anything.
- The repository contains no `waitlist/` app directory in this checkout.
- Browser installation/trace extraction stalled under Node 26 on this workstation;
  Node 24 LTS completed the browser suites.
