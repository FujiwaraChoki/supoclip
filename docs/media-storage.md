# Media storage

Set `MEDIA_STORAGE=s3` to store uploaded originals, generated clips, clip timing
metadata, editor previews, drafts and exports in a private S3 bucket. The default
`local` mode supports self-hosted installations without AWS. Existing local clip
paths remain readable alongside new S3 references.

## Configuration

Backend and worker need the same settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEDIA_STORAGE` | `local` | `local` or `s3` |
| `MEDIA_S3_BUCKET` | empty | Required for S3 mode |
| `MEDIA_S3_PREFIX` | `media` | Dedicated application prefix |
| `MEDIA_S3_REGION` | `AWS_REGION` or `eu-north-1` | Bucket region; Compose defaults to `eu-central-1` |
| `MEDIA_S3_ENDPOINT` | AWS regional endpoint | Optional S3-compatible endpoint reachable by browsers and workers |
| `MEDIA_S3_ACCESS_KEY_ID` | empty | Optional dedicated media identity |
| `MEDIA_S3_SECRET_ACCESS_KEY` | empty | Secret for that identity |
| `MEDIA_S3_SESSION_TOKEN` | empty | Token when using temporary dedicated credentials |
| `MEDIA_URL_TTL_SECONDS` | `900` | Signed download validity, clamped to 60–3600 seconds |
| `MEDIA_CACHE_TTL_SECONDS` | `86400` | Expiry of unused local caches, at least four hours |
| `MEDIA_ORPHAN_GRACE_SECONDS` | `86400` | Minimum age before reclaiming unreferenced objects, at least one day |
| `MEDIA_UPLOAD_CONCURRENCY` | `3` | Maximum active upload sessions per user, shared across API workers |
| `MEDIA_UPLOAD_DAILY_LIMIT` | `20` | Maximum upload authorizations per user in a rolling day, including aborted attempts |

When dedicated media keys are omitted, boto3 uses its standard credential chain,
including workload IAM roles. Browser/CLI login credentials are for administrative
setup; use a restricted runtime identity for the services. Media keys are separate
from other service credentials, including SES.

`scripts/setup-media-storage.py` reports its plan by default. With `--apply`, it
creates a private encrypted bucket, an HTTPS-only bucket policy, browser CORS,
one-day expiry for unfinished multipart uploads, and a runtime IAM identity scoped
to the bucket's `media/` prefix. It writes runtime configuration to a new file with
mode `0600` and never prints key values. It refuses to overwrite an existing secret
file or create another key for an identity that already has one.

```sh
aws login
backend/.venv/bin/python scripts/setup-media-storage.py
backend/.venv/bin/python scripts/setup-media-storage.py --apply \
  --credentials-file "$HOME/.config/supoclip/media-storage.env"
```

Supply every deployed frontend origin with repeated `--origin` arguments if the
defaults (`https://supoclip.com`, `https://www.supoclip.com`, localhost port 3107)
do not cover the installation. Keep secrets outside Git. Copy configuration into
the deployment's secret store or private environment file. Apply backend migrations
before starting the updated worker.

## Uploads and delivery

The authenticated frontend requests multipart authorization from the backend.
Browsers PUT 64 MiB parts directly to S3, with up to three concurrent requests,
then call the authenticated completion endpoint. Object keys include a hash of the
owning user ID. PostgreSQL records the owner, upload ID, object path and expected
size before returning authorization. Each part URL signs its exact byte length;
refresh requests can only renew parts within that recorded size. Completion
verifies the assembled size and the 12 GB limit. The backend also
checks ownership and object size before admitting a task. Failed browser uploads
are aborted; bucket lifecycle rules catch uploads abandoned by closing a tab.
Expired part URLs can be refreshed during the session's one-day lifetime without
restarting the upload. Expired sessions retain their capacity until the worker
successfully aborts them. Completion is
retryable after an interrupted response or a temporary validation request failure.
Uploads authorized before the session-tracking upgrade must be restarted; their
abort endpoint remains available. Previously issued URLs keep their original
expiry, and bucket lifecycle cleanup still reclaims abandoned multipart data.

The existing `/upload` endpoint continues to accept REST/MCP uploads. In S3 mode
it uploads the temporary incoming file to the bucket and removes the local copy.

Database `file_path` fields contain `s3://bucket/key`, never expiring signed URLs.
Clip IDs and API routes remain stable. Ownership checks or enabled share tokens
authorize playback before the API redirects to a signed S3 URL. The frontend
passes that redirect through without following it using backend authentication
headers. S3 handles playback, range requests and downloads.
The MCP downloader follows these redirects explicitly, strips backend credentials
for storage requests, and replaces a local download only after a successful stream.
Download buttons request an attachment disposition; playback remains inline.

## Processing and retention

FFmpeg uses temporary local copies. Uploaded originals remain in S3 while a task
references them, so regeneration and caption editing can retrieve them later.
YouTube downloads are removed after processing and edits; subsequent edits can
download them again. Word timings for uploaded sources and clips are retained.
YouTube word timings are also backed up under `media/transcripts/` and restored
on later downloads; their S3 copy is removed when no task still uses that video.

The worker keeps downloads under `TEMP_DIR/media-cache/`. Renders acquire shared
file leases; the last renderer releases the downloaded files, including on failure
or cancellation. Word timings are backed up before transcript analysis; if S3 is
unavailable, eviction preserves the small timing sidecars for the worker to retry.
A cache sweeper reclaims leftovers after the cache expiry period.
Generated local outputs are queued for deletion after their durable database
reference commits. Preset exports returned directly by the API are removed after
the response finishes.

Editor assets and changed draft/job documents are copied to S3. Unchanged media is
not uploaded on every draft save. Missing local editor assets can be restored from
S3. Unused, successfully uploaded local editor videos expire from the cache; drafts
remain available. The API and worker still share `TEMP_DIR` for operational editor
state, file locks, progress and cancellation. This change does not remove that
coordination requirement for an installation with multiple hosts.
Exports become downloadable after rendering and their S3 upload finish. Autosave
returns the committed local revision even if its snapshot upload fails; the worker
retries changed draft snapshots and unfinished asset uploads. Autosave uses the
draft's file lock and revision check, so clip renders do not block it. Complete
local editor state remains usable during an S3 outage. Processing caches keep durable source references,
and readers of older materialized cache paths also acquire file leases.

Deleting/replacing clips queues their previous files, sidecars and obsolete editor
directories in PostgreSQL in the same transaction. Deleting a task also queues
its uploaded source. Every five minutes the worker processes committed deletions,
checks that no live records still reference the file, and retries failed deletions.
Individual failures back off without blocking unrelated deletion requests.
Uploaded sources shared by multiple tasks are removed after the last task is
deleted. A source lock serializes new S3 task admission with source deletion.

Every six hours the worker queues old unreferenced local/S3 media for deletion.
This catches abandoned completed uploads, outputs from rolled-back/interrupted
jobs, old YouTube media and deleted-account leftovers. Live clips and editor drafts
are excluded. There is no bucket-wide expiry rule for active originals or clips.
The one-day orphan grace period is longer than the worker's three-hour job timeout.

## Migrating existing files

Back up PostgreSQL and drain processing/editor jobs first. Run the migration from
the backend directory with the deployment's database, S3 and `TEMP_DIR` settings:

```sh
.venv/bin/python scripts/migrate_media_to_s3.py
.venv/bin/python scripts/migrate_media_to_s3.py --apply
```

The default reports counts without transferring/deleting files. Apply mode uploads
referenced clips and their timing metadata, snapshots existing editor directories,
updates file references while preserving clip IDs, and queues obsolete local files
for deletion after commit, held under the printed `cleanup_token`. It also transfers uploaded originals and updates source
URLs and persisted retry metadata. Active tasks, missing files and historical
sources shared across different owners, oversized originals and invalid historical
source references are skipped and counted. Those skipped
uploads need an ownership decision before migration. The script is restartable:
already migrated S3 references are skipped.
Use `--task-id TASK_ID` to migrate one task first and verify its playback and edits.

New tasks may rebuild their transcript-analysis cache after the source URL changes;
the uploaded word-timing sidecar is preserved. Check playback, editing and a
regeneration, then release each verified batch explicitly:

```sh
.venv/bin/python scripts/migrate_media_to_s3.py --release-cleanup CLEANUP_TOKEN
```

The cleanup worker and orphan sweeper preserve held files until release. Each
apply run prints its token before transferring files, including if a later step
fails. The new recovery migration adds retry fields to installations that already
applied the first storage migration; run pending backend migrations before startup.

Rollback the application storage mode to `local` while retaining the S3 bucket
configuration and runtime credentials: existing S3 clip/source references still need them. Restoring a
database backup after local files have been removed requires restoring those files
or migrating their references again.
