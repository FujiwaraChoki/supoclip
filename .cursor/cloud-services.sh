#!/usr/bin/env bash
# Start local Postgres and Redis and ensure the SupoClip role and database exist.
# Usage: cloud-services.sh ensure
set -euo pipefail

start_postgres() {
  if pg_isready -h 127.0.0.1 -p 5432 -q; then
    return
  fi
  local ver
  ver="$(ls /etc/postgresql | head -1)"
  sudo pg_ctlcluster "${ver}" main start
  local attempt
  for attempt in $(seq 1 30); do
    if pg_isready -h 127.0.0.1 -p 5432 -q; then
      return
    fi
    sleep 1
  done
  echo "postgres did not become ready" >&2
  exit 1
}

start_redis() {
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    return
  fi
  sudo mkdir -p /var/lib/redis /var/log/redis /var/run/redis
  sudo chown redis:redis /var/lib/redis /var/log/redis /var/run/redis
  sudo -u redis redis-server /etc/redis/redis.conf \
    --daemonize yes \
    --supervised no \
    --bind 127.0.0.1 \
    --port 6379 \
    --protected-mode yes \
    --dir /var/lib/redis \
    --pidfile /var/run/redis/redis-server.pid
  local attempt
  for attempt in $(seq 1 30); do
    if redis-cli ping 2>/dev/null | grep -q PONG; then
      return
    fi
    sleep 1
  done
  echo "redis did not become ready" >&2
  exit 1
}

ensure_database() {
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'supoclip') THEN
    CREATE ROLE supoclip LOGIN PASSWORD 'supoclip_password';
  ELSE
    ALTER ROLE supoclip WITH LOGIN PASSWORD 'supoclip_password';
  END IF;
END
$$;
SQL
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='supoclip'" | grep -q 1; then
    sudo -u postgres createdb -O supoclip supoclip
  fi
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d supoclip -c "GRANT ALL ON SCHEMA public TO supoclip;"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d supoclip -c "ALTER SCHEMA public OWNER TO supoclip;"
}

start_postgres
start_redis
ensure_database
echo "cloud-services: postgres and redis ready"
