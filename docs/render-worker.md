# Video workers on other machines

The video worker can run on a different machine from the web app, API,
PostgreSQL and Redis. It needs a connection to PostgreSQL, Redis and the media
bucket. It does not need any disk from the application host: no NFS, no
synchronized folders.

Workers on any number of machines consume the same queue (`supoclip_tasks`).
Any of them may receive a generation, an editor preparation, an export, a
combine or a social publish.

## Requirements

- **`MEDIA_STORAGE=s3` on the API and on every worker.** With local storage,
  finished clips are files on the API's disk, so the API and its workers must
  share one volume on one machine. That setup still works as before. A worker
  that starts with local storage on a different disk from the API stops with an
  error instead of rendering files nobody can serve.
- **The same release everywhere.** Deploy the application host first: the API
  applies the database migrations when it starts.
- **Private PostgreSQL and Redis access** from the worker machine. Do not expose
  either to the public network. PostgreSQL connections must be direct or through
  a session-mode pooler; replication uses session advisory locks, which a
  transaction-mode pooler breaks.
- **The same media settings** (`MEDIA_S3_*`, `MEDIA_FALLBACK_*`,
  `MEDIA_FAILOVER_ENABLED`) and the same provider credentials the worker on the
  application host uses. The queue also carries social publishing, so include
  those credentials too.
- **Free local disk** for scratch. Allow for the largest source video, its
  1080p copy and the clips of the jobs that run at once.

## What lives where

| State | Where | Notes |
|---|---|---|
| Source videos, clips, exports, editor media, user fonts | Media bucket | Readable by every worker |
| Tasks, clips, editor sessions, drafts, export and combine jobs | PostgreSQL | `editor_sessions`, `editor_jobs` |
| Pending replication to the second bucket, storage health | PostgreSQL | `media_replication_ops`, `media_storage_health` |
| Queued deletions | PostgreSQL | A local path is tagged with the disk it is on |
| Job queue, task progress, task cancellation, per-account render slots | Redis | |
| Downloads, caches, renders in progress | Each machine's `TEMP_DIR` | Scratch. Safe to lose |

A job only reports a result after the result is in the bucket and the database.
Losing a worker's disk loses caches and the jobs that were running on it, which
the queue retries.

## How the cases that used to need a shared disk work

**Uploads.** A file sent to `/upload` lands on the API's disk and is copied to
the bucket after the response. A worker on another machine cannot read the file
before that copy exists, so it puts the job back on the queue and checks again
every `VIDEO_JOB_DEFER_SECONDS`. The task shows "Saving your upload...". The
waiting job holds no worker slot. If the API restarts mid-copy, the scheduler's
`copy_pending_uploads` job finishes it within about a minute. If the copy has
not arrived after `UPLOAD_COPY_WAIT_SECONDS` (30 minutes), the task fails with a
message asking for a new upload, and its minutes are released. Uploads sent
straight to the bucket by the browser are never delayed.

**Editor.** Sessions, drafts and jobs are rows. Saving a draft is one
compare-and-swap on its revision; it does not touch the bucket or any file.
Preparing a clip uploads its preview media before the session is marked ready.
An export or combine job carries the document it renders. The worker reports
progress and reads a cancel request through its job row about once a second.
If a worker dies, the studio shows the job as failed after three minutes, and
the queue's retry can take the job over. A preparation that stops reporting is
shown as failed after five minutes and can be retried.

**Cancellation and progress of generations** already used Redis and are
unchanged.

**User fonts.** An uploaded font is stored in the bucket as well as on the API's
disk. A worker downloads it before rendering captions with it. Fonts uploaded
before this release are copied to the bucket by the scheduler's cleanup job.

**Replication to the second bucket.** What a store has not acknowledged yet is
recorded in PostgreSQL, so every machine routes reads the same way and the
scheduler can finish a copy that any machine started. The copy is made from the
bucket that has the object; no machine has to keep the file. Pending work left
in `TEMP_DIR/media-replication/` by the earlier release is imported the first
time the replication job runs on the machine that has it.

**Cleanup.** The scheduler deletes objects in the bucket. Local files are
deleted only by a process on the same disk: each video worker cleans its own
scratch about every ten minutes (queued deletions for its disk, expired caches,
abandoned renders).

**YouTube source cache.** Each machine keeps its own cache and mirrors it to the
bucket. The stored manifest is the union of what every machine cached, and a
machine fetches a section another machine already downloaded before going back
to YouTube.

## What stays on the application host

- The API, including clip trim, split, merge, caption edits and ZIP downloads,
  which it performs itself.
- The scheduler (`supoclip_cron`). Run it on the application host, embedded in
  that host's worker (`EMBED_CRON_WORKER=1`) or as the `cron-worker` service.
  Uploads and fonts are written to that machine's disk first, and the scheduler
  is what copies leftovers to the bucket. Workers elsewhere set
  `EMBED_CRON_WORKER=0`.
- The AI worker (`supoclip_ai`).

## Running a worker

`deploy/render-worker/compose.yml` starts one video worker with a local scratch
directory and no shared mounts.

```sh
install -m 600 deploy/render-worker/.env.example /etc/supoclip/render-compose.env
# Fill in the values, and put service credentials in the file WORKER_ENV_FILE names.
docker compose --env-file /etc/supoclip/render-compose.env \
  -f deploy/render-worker/compose.yml up -d --no-build
docker compose --env-file /etc/supoclip/render-compose.env \
  -f deploy/render-worker/compose.yml logs --tail 50 worker
```

Do not run `docker compose config` against the real files: it prints resolved
credentials.

Names that only resolve inside the application host's Compose network, such as
`bgutil`, `redis`, `postgres` or `host.docker.internal`, do not work on another
machine. Replace them with addresses the worker can reach.

## Checking it works

Workers compete for jobs, so a given job may land on either machine. Look at
each machine's worker log to see what it processed.

1. Generate from a YouTube link and from an uploaded file.
2. Open a clip in the studio, save a draft, export it, cancel a second export.
3. Combine two clips.
4. Cancel a generation while it is processing.
5. Play and download the results in the app.
6. `GET /health/storage` on the API reports `healthy` when the second bucket is
   configured.

`GET /health/worker` reports that at least one video worker is alive; it does
not say which.

## Stopping a worker

Stop the container. Running jobs get up to `stop_grace_period` to finish. A job
that is cut off is retried by the queue on whichever worker is free. Queued
deletions for that machine's disk stay in the database for a month in case the
machine returns, then are dropped; the scratch directory can simply be removed.

## Rolling back

The schema changes are additive. The previous release runs on the migrated
database, but it goes back to files on the shared disk: drafts saved after the
upgrade are not visible to it, replication still owed in `media_replication_ops`
is not retried by it, and it cannot use a worker on another machine. Stop
workers on other machines before rolling back.
