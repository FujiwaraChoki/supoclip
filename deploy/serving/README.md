# Switching the serving release

On the hosted Linux application server, keep the old frontend and API running while a candidate starts. nginx sends new connections to the candidate after its API can query PostgreSQL and ping Redis and its frontend can serve an anonymous Better Auth session and read the user, account and session tables through its packaged Prisma client. An nginx worker keeps its loaded upstream configuration for the lifetime of the requests it is already serving. The old frontend also keeps using its own API. Video, AI and cron workers are a separate rollout.

## Install once

1. Check that PostgreSQL migrations, frontend assets, queue payloads and storage formats can coexist across these two releases. Stage code, Compose overrides and private configuration in a **separate release directory**. The legacy containers can still bind-mount the current checkout. Keep that checkout intact until they retire. Build the final frontend runner and backend images with the committed locks and patched base images. Run the repository's Image security checks against the images being released.
2. Initialize the upstream include pointing at the **currently serving** web and API ports. For the original layout run `sudo python3 deploy/serving/switch.py init 3001 8000`. This creates `/etc/nginx/conf.d/supoclip-serving.conf` and reserves loopback port 18091 for a local release marker. The nginx `http` context must include that file.
3. In **every** SupoClip web/API proxy location, including the upload locations, change the fixed `proxy_pass http://127.0.0.1:3001;` and `proxy_pass http://127.0.0.1:8000;` targets to `proxy_pass http://supoclip_web;` and `proxy_pass http://supoclip_api;` respectively. Keep the route's original streaming, timeouts, forwarded headers and TLS settings. Keep third-party proxy locations as configured. Run `sudo nginx -t && sudo nginx -s reload`. Verify both public routes and `curl -fsS http://127.0.0.1:18091/serving`. It should return `3001/8000`. Remove any forced `worker_shutdown_timeout` before using graceful draining.
4. Retain Next static assets so an already-open page can load an older chunk. Copy the currently serving image's assets and each subsequent candidate's assets before switching. Use a web-readable directory, for example `/var/lib/supoclip-assets/_next/static/`. Stage with `docker cp CONTAINER:/app/.next/static/. STAGING_DIR/` into an empty directory, then `rsync -rt --ignore-existing --chmod=D755,F644 STAGING_DIR/ /var/lib/supoclip-assets/_next/static/`. In the web vhost serve `location ^~ /_next/static/ { root /var/lib/supoclip-assets; try_files $uri @supoclip_static; }` and define `location @supoclip_static { proxy_pass http://supoclip_web; }`. Verify an old and a candidate chunk. Keep retained files through the lifetime of open clients and the rollback window.
5. Identify callers bypassing nginx. In the original deployment the MCP container calls `http://backend:8000` directly. Move its `SUPOCLIP_API_URL` to the stable API vhost during its own drained rollout, or retain the original backend until that migration completes. Update the host deployment command to use the prepare/switch steps below for serving containers. In-place Compose replacement and checkout updates would bypass this switch.

## Release

From the staged checkout with its production environment and Compose overrides, choose a fresh release name and two free loopback ports. The default network and media volume names match the hosted installation. Use `SERVING_NETWORK`, `SERVING_UPLOADS_VOLUME` and `SERVING_CLIPS_VOLUME` for a different installation. Compose must support `!override` and `!reset` (2.24.4+).

```sh
deploy/serving/prepare.sh release-abc123 13001 18000 "$SCANNED_FRONTEND_IMAGE" "$SCANNED_BACKEND_IMAGE"
```

Prepare pins the supplied local image IDs and starts only the candidate API and frontend. Each release has its own Docker names. The API uses the existing data volumes and the image's packaged source, fonts and transitions. The frontend uses the standalone runner from its image and the candidate API's name. Verify any installation-specific media mounts against that layout. R2 and AWS settings come from the staged environment. Failed preparation leaves the existing serving release running and reports the error.

The switch needs Docker access to check the frontend container published on the selected web port. Retain the candidate's assets, check both loopback ports, and verify the frontend can reach its paired API. Run the cutover from the application host:

```sh
sudo python3 deploy/serving/switch.py switch 13001 18000
```

Readiness failure leaves routing untouched. The switch atomically replaces the include, tests the entire nginx configuration, reloads gracefully, and confirms the new marker. A failed activation restores the previous include and reloads it. A successful activation waits for the previous nginx worker processes to exit. This includes uploads, downloads, SSE and keep-alive requests across the old frontend/API chain. Both releases remain running. Look at the web and API access/error logs before and after the switch, and check the live changelog for product releases.

A drain timeout exits nonzero. New requests still use the candidate. Leave the old pair running and inspect the remaining nginx workers and transfers. Repeat the same switch to verify the drain later. To roll traffic back, run the switch with the previous web and API ports. This also waits for the candidate's connections to drain. If an operator process was killed between the include update and activation, compare the loopback marker with the include, restore the configuration matching the serving release, test/reload and retry.

Retire the old pair only after the drain succeeds, direct callers have moved, queued/processing tasks and active worker jobs have cleared, and the rollback window is over. Check for background work in the old API container as well, including legacy upload persistence. Leave the old containers running if any check is uncertain. Stop only their specific container names. Never use project-wide Compose down or remove shared volumes during a serving rollout.

Preparation refuses to add a third running release pair. Retained idle database
pools still consume PostgreSQL slots, so a drained release must be stopped before
staging the next candidate. Each API slot can hold 15 ORM connections (pool 10,
overflow 5) and each worker 5; include workers, the direct MCP API and
advisory-lock connections in the database budget (`docs/configuration.md`,
"Connection budget"). A refusal never stops containers or interrupts a transfer.

## Rehearsal

On a Linux host with Python 3 and nginx, run `python3 deploy/serving/test_switch.py`. The rehearsal starts its own unprivileged nginx master and two frontend/API pairs on ephemeral loopback ports. It verifies readiness refusal, an upload and download spanning the switch, new traffic on both upstreams, drain timeout, byte integrity and rollback. It uses synthetic HTTP services and temporary configuration.
