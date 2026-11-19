# Autoscaling render workers

The primary render host runs the video queue all the time. When the queue
backs up, the render autoscaler adds cloud workers (Hetzner Cloud servers,
billed by the hour) and removes them again once they have been idle for a
while. It is off by default (`AUTOSCALER_ENABLED=false`).

Read [render-worker.md](./render-worker.md) first: an autoscaled worker is an
ordinary video worker on another machine, built and connected the same way.

Code: `deploy/autoscaler/` (controller, decision logic, probe, systemd units,
snapshot build script) and `backend/src/workers/host_jobs.py` (which jobs run
on which machine).

## How it works

A systemd timer on the application host runs `autoscaler.py` every two
minutes. One run at a time: a run that finds the lock held exits. Each run:

1. **Reads the queue.** `probe.py` is piped into the backend container on the
   application host (`AUTOSCALER_PROBE_CONTAINER`) and reads, without writing:
   - tasks with `status = 'queued'` and how long the oldest has waited. This is
     the real backlog. The ARQ queue key `supoclip_tasks` also holds running
     jobs and stale jobs of cancelled tasks, so it is only logged;
   - of those, the **startable** ones: tasks whose job is waiting on the queue
     (not already picked up, not cancelled, not waiting for its upload) and
     whose account has a free render slot (`VIDEO_JOB_USER_CONCURRENCY` minus
     its processing tasks). An account that already uses all of its slots is
     not helped by another machine, and a queued task whose job is gone never
     starts anywhere, so only startable tasks count for scaling up and for
     idleness. Their wait is measured from when the job became due, so a task
     that was resumed after days is not "waiting for days";
   - `pg_stat_activity` against `max_connections`;
   - each cloud worker's running jobs from the per-host job registry.
2. **Lists its servers** through the cloud API, by the label
   `supoclip-autoscaler=managed`. Servers without that label do not exist for
   the autoscaler: it never counts, drains or deletes them.
3. **Decides** (`decide.py`, pure and unit-tested) and acts.
4. **Alerts** the operator chat for every scale-up, scale-down, failure and
   spend-cap hit.

### Scaling up

A worker is added when the oldest startable task has waited
`AUTOSCALER_SCALE_UP_WAIT_MINUTES` (10) or `AUTOSCALER_SCALE_UP_QUEUE_DEPTH`
(20) startable tasks are queued, and all of these hold:

- no other worker is being provisioned, and the last scale-up was more than
  `AUTOSCALER_SCALE_UP_COOLDOWN_MINUTES` (10) ago, so a new worker gets to work
  through the queue before the next one is considered;
- fewer than `AUTOSCALER_MAX_EXTRA_WORKERS` (2) cloud workers are active;
- **database budget:** `max_connections − in use − AUTOSCALER_DB_CONNECTION_RESERVE (20)`
  is at least `AUTOSCALER_DB_CONNECTIONS_PER_WORKER` (25);
- **spend:** today's spend plus one more hour of every active worker and the new
  one stays within `AUTOSCALER_DAILY_SPEND_CAP_EUR` (40);
- no cloud API error and no failed scale-up in the last
  `AUTOSCALER_ERROR_BACKOFF_MINUTES` (30). A server that is created and deleted
  again is still billed for a started hour, so a failing scale-up is not retried
  every cooldown;
- the current release image is known.

When a limit blocks a wanted scale-up it alerts once an hour, not every run.

Provisioning a worker (about 3–5 minutes):

1. Create the server from the newest golden snapshot, labelled
   `supoclip-autoscaler=managed`, `state=provisioning` and the release image.
   Cloud-init sets its hostname to the server name and installs an SSH host
   key the autoscaler generated and pinned, so the first connection is
   verified.
2. Start `supoclip-render-tunnel@<name>`: a reverse SSH tunnel that puts
   PostgreSQL, Redis and bgutil on the worker's loopback (15432/16379/14416).
   Nothing is exposed publicly.
3. Copy `worker.env`, the compose env file (database URL, image, concurrency,
   pool size) and the YouTube proxy secret **over SSH stdin**. No secret is
   ever on a command line, in a label or in the logs.
4. Copy the release image (`docker save | gzip | ssh docker load`) and check
   its layers match the application host's.
5. **Check connectivity before anything can take a job.** A throwaway container
   with the worker's exact settings must load the configuration, find the
   `tasks` table in PostgreSQL, ping Redis and see the video workers'
   heartbeat key there. A worker pointed at the wrong database would otherwise
   fail every job it picks up within seconds.
6. `docker compose up`, confirm the container stays up, label it `state=active`.

If anything before step 6 fails, the server is deleted (no worker ever ran on
it) and an alert is sent. If step 6 fails, the worker might already have taken
jobs, so it is drained instead of deleted.

### Scaling down

A worker is **idle** while nothing runs on it and no startable task is queued. After
`AUTOSCALER_IDLE_MINUTES` (30) of continuous idleness it is drained. A worker
is also drained when the release changes (it runs an older image), when the
daily spend cap is reached, and when there are more than
`AUTOSCALER_MAX_EXTRA_WORKERS` (least busy first).

**Draining never stops a job.** The autoscaler runs `docker update
--restart=no` and `docker kill -s TERM` on the worker container. ARQ then
takes no new jobs and waits up to `job_completion_wait` (3 h) for running ones
before it exits. The server is labelled `state=draining`.

A draining server is deleted only after `docker ps` on it shows no running
container at all, checked again right before the delete call. The check is not
limited to the worker's compose label, so a worker started another way can
never look like it has exited. A server it cannot reach is not deleted, in any
state: one that never finished provisioning and cannot be reached is reported
and left for the operator. A drain still running after 3 h 15 min is reported,
never forced. After deletion its tunnel is stopped and its host key forgotten.

### Which jobs run where

ARQ's in-progress keys do not say which machine runs a job. Every video worker
records its jobs in `supoclip:render-host-jobs:<host>` (a sorted set of job id
→ start time, `on_job_start`/`on_job_end` hooks). Entries older than the job
timeout are ignored, so a worker that crashed cannot keep a machine "busy"
for more than about three hours. Workers use host networking, so `<host>` is
the server's hostname; `RENDER_HOST_NAME` overrides it. The autoscaler only
treats a worker as idle when the registry answers.

## Golden snapshot and release image

**Choice: a snapshot without the release image, plus the image streamed from the
application host at boot.**

- The snapshot (`deploy/autoscaler/build-snapshot.sh`) holds what changes
  rarely: Docker and Compose, the `supoclip` admin user and the restricted
  `tunnel` user, hardened SSH (keys only, no root, forwarding only for the
  tunnel user's three listen ports), a firewall allowing SSH from the
  application host only, unattended security updates without reboots, the
  worker compose file and directories. No credentials.
- The release image is never baked in. Each new worker gets the image the
  application host is running right now, so a worker can never come up on a
  stale release. The application host is already where release images are
  built and scanned, and there is no registry to keep in sync. The copy is
  about 1.2 GB compressed, under two minutes within one region.
- Cloud-init alone (installing Docker on every boot) would add several minutes
  and a dependency on outside package mirrors to every scale-up.

`AUTOSCALER_IMAGE_SOURCE` says where the current image comes from:

- `container:<name>` (default `container:supoclip-worker`): the image of that
  running container on the application host.
- `file:<path>`: a file the deploy flow writes with the image tag. Use this
  when the render hosts run a different image from the container above (for
  example a worker-only hotfix build). Update the file in the same step that
  rolls the primary render host. The image must exist on the application host;
  a file naming an image that is not there counts as "no image" (alert, no
  scale-up, no drains).

A worker on another image than the current one is drained and, if the queue
still needs it, replaced by one on the new release.

Rebuild the snapshot only when the base system needs to change.

## Setting it up

Everything below runs on the application host. Nothing is enabled until the
last step.

1. **Deploy a release that contains `host_jobs.py`** to every video worker,
   including the primary render host. Without it, the autoscaler cannot see
   that a cloud worker is idle and would never scale it down.
2. **Raise `max_connections`** if needed. Each worker process holds 10–20
   connections; check `AUTOSCALER_DB_CONNECTIONS_PER_WORKER` against reality
   with `SELECT usename, client_addr, count(*) FROM pg_stat_activity GROUP BY 1, 2`.
3. **A user** `supoclip-autoscaler` in the `docker` group, with a sudoers rule
   limited to the tunnel units:

   ```
   supoclip-autoscaler ALL=(root) NOPASSWD: /usr/bin/systemctl restart supoclip-render-tunnel@*.service, /usr/bin/systemctl stop supoclip-render-tunnel@*.service
   ```

4. **Files** in `/etc/supoclip-autoscaler/` (directory 0700, files 0600, owned by
   that user):
   - `autoscaler.env` from `deploy/autoscaler/autoscaler.env.example`;
   - `hcloud-token`: a cloud API token for the project (read/write);
   - `admin_key` and `tunnel_key`: two new SSH key pairs; their public halves
     go into the snapshot;
   - `worker.env`: the primary render host's worker env with database and
     Redis addresses pointing at the tunnel ports on loopback;
   - `render-compose.env`: `WORKER_DATABASE_URL` (port 15432),
     `WORKER_REDIS_HOST=127.0.0.1`, `WORKER_REDIS_PORT=16379` and any
     `YOUTUBE_PROXY_*` values the primary render host uses;
   - `tunnel.env` from `tunnel.env.example`: the loopback gateways on this host
     that reach PostgreSQL, Redis and bgutil. Double-check the PostgreSQL
     port reaches the SupoClip database and not another local server: the
     connectivity check refuses a database without a `tasks` table.
   - `/var/lib/supoclip-autoscaler/` (0700, same owner) for state, known hosts
     and tunnel instances.
5. **Golden snapshot.** Create a small server from the stock Debian 13 image,
   copy `build-snapshot.sh`, `worker-compose.yml` and the two public keys to it,
   run `sudo sh build-snapshot.sh <app host IPv4> <app host IPv6/64 or -> admin.pub tunnel.pub worker-compose.yml`,
   power it off, snapshot it with the label `supoclip-render-golden=true`
   and delete the server. Create a cloud firewall allowing SSH from the
   application host only and put its name in `AUTOSCALER_FIREWALL`.
6. **Install** `deploy/autoscaler/{autoscaler.py,decide.py,probe.py}` to
   `/opt/supoclip-autoscaler/` and the units from `deploy/autoscaler/systemd/`
   to `/etc/systemd/system/`, then `systemctl daemon-reload`.
7. **Dry run.** Set `AUTOSCALER_DRY_RUN=true` (leave `AUTOSCALER_ENABLED=false`)
   and run `systemctl start supoclip-render-autoscaler.service` once, then start
   the timer. Do not expand the env file onto a command line: it holds
   secrets, and command lines end up in process lists and logs. A dry run reads the
   queue, the database and the cloud API, logs what it would do, and changes
   nothing: no server, label, drain, tunnel or alert. Its timers go to
   `state.dry-run.json`. Watch it with `journalctl -u supoclip-render-autoscaler`.
8. **Alerts.** Add an ops-alert route on the operator chat gateway and set
   `HERMES_OPS_ALERT_URL` and `HERMES_OPS_ALERT_SECRET`. The body is
   `{"type": "supoclip.ops_alert", "version": 1, "event_id", "source": "autoscaler", "level", "title", "message", "created_at"}`
   with `level` `info` (workers added and removed), `warning` (a blocked
   scale-up, an unreadable queue) or `error` (failures), signed like the other
   routes (`X-Webhook-Timestamp`, `X-Webhook-Signature-V2`). An answer of
   `"status": "ignored"`, an error or no answer is retried three times with the
   same `event_id`; an alert that still was not filed is sent again on the next
   run. Check the route with `autoscaler.py --test-alert` (run it through the
   unit's environment, not with the secret on a command line).
9. **Enable.** `AUTOSCALER_ENABLED=true`, `AUTOSCALER_DRY_RUN=false`,
   `systemctl enable --now supoclip-render-autoscaler.timer`.

## Configuration

| Variable | Default | |
|---|---|---|
| `AUTOSCALER_ENABLED` | `false` | Kill switch. While false a run does nothing at all. |
| `AUTOSCALER_DRY_RUN` | `false` | Read and log, change nothing (also `--dry-run`). |
| `AUTOSCALER_SCALE_UP_WAIT_MINUTES` | `10` | Oldest queued task's wait that triggers a scale-up. |
| `AUTOSCALER_SCALE_UP_QUEUE_DEPTH` | `20` | Queued tasks that trigger a scale-up. |
| `AUTOSCALER_SCALE_UP_COOLDOWN_MINUTES` | `10` | Minimum time between scale-ups. |
| `AUTOSCALER_IDLE_MINUTES` | `30` | Idle time before a worker is drained. |
| `AUTOSCALER_MAX_EXTRA_WORKERS` | `2` | Cloud workers at most. `0` drains them all. |
| `AUTOSCALER_DAILY_SPEND_CAP_EUR` | `40` | Per UTC day, autoscaled servers only, started hours rounded up. |
| `AUTOSCALER_HOURLY_PRICE_EUR` | `0.7847` | Until the cloud API reports the price of a created server. |
| `AUTOSCALER_DB_CONNECTIONS_PER_WORKER` | `25` | Connections one worker may open. |
| `AUTOSCALER_DB_CONNECTION_RESERVE` | `20` | Connections always left free. |
| `AUTOSCALER_ERROR_BACKOFF_MINUTES` | `30` | No scale-up after a cloud API error or a failed scale-up. |
| `AUTOSCALER_SERVER_TYPE` / `_LOCATION` | `ccx53` / `hel1` | |
| `AUTOSCALER_WORKER_CONCURRENCY` | `10` | ARQ `max_jobs` on a cloud worker. |
| `AUTOSCALER_WORKER_DB_POOL_SIZE` / `_MAX_OVERFLOW` | concurrency / `5` | One pool per process; it must cover every slot. |
| `AUTOSCALER_IMAGE_SOURCE` | `container:supoclip-worker` | See above. |
| `AUTOSCALER_PROBE_CONTAINER` | `supoclip-worker` | Backend container the probe runs in. |
| `AUTOSCALER_SNAPSHOT_SELECTOR` | `supoclip-render-golden=true` | Newest matching snapshot is used. |
| `AUTOSCALER_FIREWALL`, `AUTOSCALER_CLOUD_SSH_KEY` | | Cloud firewall and SSH key names. |
| `AUTOSCALER_NAME_PREFIX` | `supoclip-render-auto` | Servers are `<prefix>-1`, `-2`, ... |
| `HERMES_OPS_ALERT_URL`, `HERMES_OPS_ALERT_SECRET` | | Ops-alert route of the operator chat. No alerts without both. |

## Adopting a hand-built cloud worker

A worker built by hand is left alone. To let the autoscaler drain and delete
it, give it the labels `supoclip-autoscaler=managed`, `state=active` and
`image=<image label>` (`decide.image_label(<image>)`), make sure its hostname
equals the server name and that it runs a release with the job registry.
The autoscaler must be able to reach it like its own machines: authorize the
autoscaler's admin key for `AUTOSCALER_SSH_USER` there (with passwordless
`sudo docker`) and add the machine's host key to
`/var/lib/supoclip-autoscaler/known_hosts`. Until it can, the machine is never
drained or deleted. It counts towards `AUTOSCALER_MAX_EXTRA_WORKERS` and the
spend cap (billed from its creation). The autoscaler does not stop a
hand-made tunnel unit; remove that yourself after the server is deleted.

## Turning it off and rolling back

- **Stop adding workers, keep the current ones:** `AUTOSCALER_ENABLED=false`.
  The next run does nothing; existing workers keep running (and billing).
- **Remove every cloud worker without killing jobs:** set
  `AUTOSCALER_MAX_EXTRA_WORKERS=0` and leave it enabled. Each worker is drained,
  then deleted after its jobs finish.
- **Remove the autoscaler:** after the above has finished,
  `systemctl disable --now supoclip-render-autoscaler.timer`. By hand, a
  worker is drained with `docker update --restart=no` and
  `docker kill -s TERM` on its container, and deleted only after the container
  has exited.
- The backend change (`host_jobs.py`) only writes a small Redis key per
  running job; rolling the release back simply stops writing it.

## Cost

CCX53 (32 dedicated vCPU, 128 GB) is about EUR 0.78 an hour gross in `hel1`,
billed per started hour while the server exists (a powered-off server still
bills). Two workers around the clock would be about EUR 38 a day; the default
cap of EUR 40 a day allows that and no more. The autoscaler prefers short
bursts: a worker lives for at least its busy time plus the 30-minute idle
period plus the time to drain.

Dedicated vCPU quota is per cloud project. If a create is refused (for example
`resource_limit_exceeded: dedicated core limit exceeded`), the autoscaler
alerts and stops trying for `AUTOSCALER_ERROR_BACKOFF_MINUTES`. Count the
cores of hand-built workers against the same quota.

## Tests

```sh
python3 deploy/autoscaler/test_autoscaler.py
cd backend && .venv/bin/pytest --no-cov tests/unit/test_host_jobs.py
```

The decision tests cover thresholds, the idle timer, caps, the database
budget, back-off and drains. The controller tests use a fake cloud API and a
fake host and check that secrets only travel on stdin, that a drain only sends
SIGTERM, that a server with a running worker is never deleted, that a failed
scale-up backs off, that alerts are signed, retried and repeated until filed,
and that a dry run changes nothing. The probe tests cover which queued tasks
count as startable.
