"""Queue and database facts for the render autoscaler. Read-only.

Piped into the backend container on the application host, which already has
the database and Redis settings:

    docker exec -i <backend container> /app/.venv/bin/python - HOST... < probe.py

Arguments are cloud worker host names; their running jobs come from the
per-host job registry (`src/workers/host_jobs.py`). Prints one JSON object and
never prints settings or credentials.
"""
import asyncio
import json
import sys
import time

QUEUE = "supoclip_tasks"
HOST_JOBS_PREFIX = "supoclip:render-host-jobs:"
STALE_AFTER_SECONDS = 10800 + 600


async def main(hosts):
    from redis.asyncio import Redis
    from sqlalchemy import text
    from src.config import get_config
    from src.database import get_session_maker

    result = {}
    async with get_session_maker()() as db:
        row = (await db.execute(text(
            "SELECT COUNT(*) AS queued, "
            "COALESCE(EXTRACT(EPOCH FROM NOW() - MIN(created_at)), 0) AS oldest "
            "FROM tasks WHERE status = 'queued'"
        ))).mappings().one()
        result["queued_tasks"] = int(row["queued"])
        result["oldest_queued_seconds"] = float(row["oldest"])
        # Counting rows needs no extra privilege; the details of other roles' rows do.
        result["db_connections"] = int((await db.execute(text("SELECT COUNT(*) FROM pg_stat_activity"))).scalar())
        result["db_max_connections"] = int((await db.execute(text("SHOW max_connections"))).scalar())

    config = get_config()
    redis = Redis(host=config.redis_host, port=config.redis_port, password=config.redis_password, db=0)
    try:
        result["queue_depth"] = int(await redis.zcard(QUEUE))
        now = time.time()
        result["running_by_host"] = {
            host: int(await redis.zcount(HOST_JOBS_PREFIX + host, now - STALE_AFTER_SECONDS, "+inf"))
            for host in hosts
        }
    finally:
        await redis.aclose()
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
