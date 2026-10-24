# Keeping dependency fixes in deployments

The backend and frontend lockfiles are part of the deployment. Install with
`uv sync --frozen` and `pnpm install --frozen-lockfile`; do not upgrade packages
inside a running container. The yt-dlp PO-token plugin is included in the backend
lockfile, alongside yt-dlp, so rebuilding cannot bypass the tested dependency set.

Build fresh images before a maintenance window:

```sh
docker compose build --pull
```

For a production frontend, set `FRONTEND_BUILD_TARGET=runner` as documented in
the setup guide. Both frontend stages use the same patched Node version.
Review and test base-image version changes together with dependency updates.

The **Image security** workflow builds the frontend production image, CPU backend
image and MCP image, then fails on critical vulnerabilities with published fixes.
It scans installed image contents, including bundled tools, rather than only
the lockfiles. Keep this check enabled for dependency and deployment changes.
The CUDA backend uses the same application lockfile, but CUDA/system packages
and externally supplied PostgreSQL, Redis and sidecar images need their own
image scans. This check does not establish that every scanner finding is
exploitable, or that an image with no reported findings is vulnerability-free.

Before restarting production services, check both the task database and worker
queue. Let active clips finish, stop new submissions, and check again before
stopping workers or their database/Redis dependencies. Keep database backups and
the previous image IDs for rollback. After deployment, verify worker health,
the API health route and the frontend authentication/database health check.

Server-only image overrides are not a substitute for committed Dockerfiles and
lockfiles: include these source changes in the revision used by future builds.
