#!/usr/bin/env bash
# Run from the staged release, using its deployment env and Compose overrides.
set -euo pipefail
if (( $# != 5 )); then
  echo "usage: $0 release web-port api-port scanned-web-image scanned-api-image" >&2
  exit 2
fi
export RELEASE=$1
if [[ ! $RELEASE =~ ^[a-z0-9][a-z0-9-]{0,38}$ ]]; then
  echo "Use a 1–39 character release name starting with a lowercase letter or digit." >&2
  exit 2
fi
web_port=$2
api_port=$3
for port in "$web_port" "$api_port"; do
  [[ $port =~ ^[1-9][0-9]{0,4}$ ]] && (( port < 65536 )) || exit 2
done
[[ $web_port != "$api_port" ]]
# Serialize the count and candidate creation across operator checkouts.
lock_dir="${XDG_STATE_HOME:-$HOME/.local/state}/supoclip"
mkdir -p "$lock_dir"
exec 9>"$lock_dir/prepare.lock"
flock -w 30 9
# Keep at most the serving pair and one candidate/draining pair. Never stop
# transfers here: the operator must verify the previous release has drained.
live_releases=$(docker ps --format '{{.Names}}' | awk '/^supoclip-(api|web)-/ {print}')
live_count=$(printf '%s\n' "$live_releases" | awk 'NF {n++} END {print n+0}')
if (( live_count > 2 )); then
  echo "Retire the drained previous release before staging another pair:" >&2
  printf '%s\n' "$live_releases" >&2
  exit 1
fi
WEB_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$4")
API_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$5")
export WEB_IMAGE_ID API_IMAGE_ID
overlay=$(cd "$(dirname "$0")" && pwd)/candidate.yml
base=docker-compose.yml
if [[ -f docker-compose.override.yml ]]; then base=$base:docker-compose.override.yml; fi
export COMPOSE_FILE="${COMPOSE_FILE:-$base}:$overlay"
compose=(docker compose --project-name "supoclip-slot-$RELEASE")
api="supoclip-api-$RELEASE"
web="supoclip-web-$RELEASE"
for name in "$api" "$web"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "Release container $name already exists; choose a fresh release name." >&2
    exit 1
  fi
done
# 'run' does not publish service ports or add its DNS aliases. New processes
# cannot replace live containers, even if the API or frontend fails to boot.
"${compose[@]}" run --no-deps --pull never -d --name "$api" -p "127.0.0.1:$api_port:8000" backend
"${compose[@]}" run --no-deps --pull never -d --name "$web" -p "127.0.0.1:$web_port:3107" frontend
docker update --restart unless-stopped "$api" "$web" >/dev/null
echo "Candidate $RELEASE started on web $web_port and API $api_port."
