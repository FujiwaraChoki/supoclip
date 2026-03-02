#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_DIR="$REPO_DIR/.cron"
UPDATER_SCRIPT="$CRON_DIR/update_and_restart.sh"
LOG_FILE="$REPO_DIR/cron_update.log"
LOCK_FILE="/tmp/supoclip_auto_update.lock"

CRON_TAG_START="# supoclip-auto-update-start"
CRON_TAG_END="# supoclip-auto-update-end"
CRON_SCHEDULE="0 */3 * * *"

mkdir -p "$CRON_DIR"

cat > "$UPDATER_SCRIPT" <<EOF
#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$REPO_DIR"
LOG_FILE="$LOG_FILE"
LOCK_FILE="$LOCK_FILE"

exec >> "\$LOG_FILE" 2>&1

echo "[\$(date '+%Y-%m-%d %H:%M:%S')] Starting auto-update check"

if ! command -v flock >/dev/null 2>&1; then
  echo "flock command not found; aborting"
  exit 1
fi

exec 9>"\$LOCK_FILE"
if ! flock -n 9; then
  echo "Another update process is running; exiting"
  exit 0
fi

cd "\$REPO_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git repository: \$REPO_DIR"
  exit 1
fi

if [ -n "\$(git status --porcelain)" ]; then
  echo "Repository has local changes; skipping update"
  exit 0
fi

git fetch --quiet

LOCAL_COMMIT="\$(git rev-parse HEAD)"
REMOTE_COMMIT="\$(git rev-parse @{u})"

if [ "\$LOCAL_COMMIT" = "\$REMOTE_COMMIT" ]; then
  echo "No updates found (\$LOCAL_COMMIT)"
  exit 0
fi

echo "Update detected: \$LOCAL_COMMIT -> \$REMOTE_COMMIT"
git pull --ff-only --quiet

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "Neither 'docker compose' nor 'docker-compose' is available"
  exit 1
fi

echo "Restarting Docker services"
\$COMPOSE_CMD down
\$COMPOSE_CMD up -d --build

echo "Update complete"
EOF

chmod +x "$UPDATER_SCRIPT"

EXISTING_CRON="$(crontab -l 2>/dev/null || true)"

UPDATED_CRON="$(printf '%s\n' "$EXISTING_CRON" | awk -v start="$CRON_TAG_START" -v end="$CRON_TAG_END" '
  $0 == start { skip = 1; next }
  $0 == end { skip = 0; next }
  !skip { print }
')"

CRON_LINE="$CRON_SCHEDULE $UPDATER_SCRIPT"

{
  printf '%s\n' "$UPDATED_CRON"
  printf '%s\n' "$CRON_TAG_START"
  printf '%s\n' "$CRON_LINE"
  printf '%s\n' "$CRON_TAG_END"
} | crontab -

echo "Installed cron job: $CRON_LINE"
echo "Logs will be written to: $LOG_FILE"
