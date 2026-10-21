#!/usr/bin/env bash
# Boot Postgres, Redis, Prisma migrations, the API, the ARQ worker, and Next.js.
# Safe to rerun: services that are already up are left alone.
set -euo pipefail

if [ -d /workspace/backend ]; then
  ROOT=/workspace
else
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
bash "${ROOT}/.cursor/cloud-services.sh" ensure

mkdir -p /tmp/supoclip-media /tmp/supoclip-dev

cat > /tmp/supoclip-dev/backend.env <<'EOF'
SELF_HOST=true
DATABASE_URL=postgresql+asyncpg://supoclip:supoclip_password@127.0.0.1:5432/supoclip
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
BACKEND_AUTH_SECRET=change_me_backend_auth_secret
APP_SETTINGS_ENCRYPTION_KEY=change_me_settings_encryption_secret
ALLOW_UNSIGNED_BACKEND_AUTH=false
AUTH_SIGNATURE_TTL_SECONDS=300
CORS_ORIGINS=http://localhost:3107,http://127.0.0.1:3107,http://sp.localhost:3107,http://supoclip.localhost:3107
YOUTUBE_DOWNLOAD_PROVIDER=yt_dlp
YOUTUBE_METADATA_PROVIDER=yt_dlp
MEDIA_STORAGE=local
TEMP_DIR=/tmp/supoclip-media
NEXT_PUBLIC_APP_URL=http://127.0.0.1:3107
SUPOCLIP_SERVICE=backend
EOF

cat > /tmp/supoclip-dev/frontend.env <<'EOF'
SELF_HOST=true
NEXT_PUBLIC_SELF_HOST=true
DATABASE_URL=postgresql://supoclip:supoclip_password@127.0.0.1:5432/supoclip
BACKEND_AUTH_SECRET=change_me_backend_auth_secret
APP_SETTINGS_ENCRYPTION_KEY=change_me_settings_encryption_secret
BETTER_AUTH_SECRET=supoclip_dev_secret_change_in_production
BETTER_AUTH_URL=http://127.0.0.1:3107
NEXT_PUBLIC_APP_URL=http://127.0.0.1:3107
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
BACKEND_INTERNAL_URL=http://127.0.0.1:8000
NEXT_TELEMETRY_DISABLED=1
EOF

# dotenv files so manual commands pick up the same local defaults
cp /tmp/supoclip-dev/backend.env "${ROOT}/backend/.env"
cp /tmp/supoclip-dev/frontend.env "${ROOT}/frontend/.env.local"
cp /tmp/supoclip-dev/frontend.env "${ROOT}/.env"

(
  cd "${ROOT}/frontend"
  set -a
  # shellcheck disable=SC1091
  source /tmp/supoclip-dev/frontend.env
  set +a
  pnpm exec prisma migrate deploy
)

TMUX_BIN="tmux"
if [ -x /exec-daemon/tmux ]; then
  if [ -f /exec-daemon/tmux.portal.conf ]; then
    TMUX_BIN="tmux -f /exec-daemon/tmux.portal.conf"
  else
    TMUX_BIN="/exec-daemon/tmux"
  fi
fi

start_session() {
  local name="$1"
  local script="$2"
  if ${TMUX_BIN} has-session -t "${name}" 2>/dev/null; then
    return
  fi
  ${TMUX_BIN} new-session -d -s "${name}" "bash ${script}"
}

cat > /tmp/supoclip-dev/run-backend.sh <<EOF
#!/usr/bin/env bash
set -a
source /tmp/supoclip-dev/backend.env
set +a
cd ${ROOT}/backend
exec .venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000
EOF

cat > /tmp/supoclip-dev/run-worker.sh <<EOF
#!/usr/bin/env bash
set -a
source /tmp/supoclip-dev/backend.env
set +a
export SUPOCLIP_SERVICE=worker
cd ${ROOT}/backend
exec .venv/bin/arq src.workers.tasks.WorkerSettings
EOF

cat > /tmp/supoclip-dev/run-frontend.sh <<EOF
#!/usr/bin/env bash
set -a
source /tmp/supoclip-dev/frontend.env
set +a
cd ${ROOT}/frontend
exec pnpm exec next dev --turbopack --hostname 0.0.0.0 --port 3107
EOF

chmod +x /tmp/supoclip-dev/run-backend.sh /tmp/supoclip-dev/run-worker.sh /tmp/supoclip-dev/run-frontend.sh

start_session supoclip-backend /tmp/supoclip-dev/run-backend.sh
start_session supoclip-worker /tmp/supoclip-dev/run-worker.sh
start_session supoclip-frontend /tmp/supoclip-dev/run-frontend.sh

wait_http() {
  local url="$1"
  local attempt
  for attempt in $(seq 1 60); do
    if curl -sf "${url}" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "timed out waiting for ${url}" >&2
  return 1
}

wait_http http://127.0.0.1:8000/health
wait_http http://127.0.0.1:8000/health/db
wait_http http://127.0.0.1:8000/health/redis
wait_http http://127.0.0.1:3107/api/auth/get-session

worker_attempt=0
until curl -sf http://127.0.0.1:8000/health/worker >/dev/null; do
  worker_attempt=$((worker_attempt + 1))
  if [ "${worker_attempt}" -gt 30 ]; then
    echo "timed out waiting for worker heartbeat" >&2
    exit 1
  fi
  sleep 2
done
echo "cloud-start: api, worker, and frontend are up"
