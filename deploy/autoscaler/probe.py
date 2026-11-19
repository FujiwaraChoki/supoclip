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


def summarize(queued, processing, jobs, limit):
    """Count the queued tasks another worker could start right now.

    `queued`: the queued tasks, oldest first, as {"task", "user", "age"}.
    `processing`: processing tasks per user. `jobs`: per task, what Redis says
    about its ARQ job: {"waited": seconds since it was due, or None when it is
    not on the queue; "running": a worker holds it; "held": cancelled or still
    waiting for its upload}. `limit`: render slots per account.

    A task is startable when its job is waiting on the queue, nothing holds it
    back and its account has a free slot (the rules of video_admission.py). A
    queued task whose job is gone never starts on any worker, so it must not
    keep a machine alive or add one. The wait is measured from when the job
    became due, not from when the task was created: a resumed task is old but
    has only just been queued.
    """
    result = {"queued_tasks": len(queued), "oldest_queued_seconds": 0.0,
              "startable_tasks": 0, "oldest_startable_seconds": 0.0}
    free = {}
    for task in queued:
        result["oldest_queued_seconds"] = max(result["oldest_queued_seconds"], float(task["age"]))
        user = task["user"]
        free.setdefault(user, max(0, limit - int(processing.get(user, 0))))
        job = jobs.get(task["task"]) or {}
        waited = job.get("waited")
        if waited is None or job.get("running") or job.get("held") or free[user] <= 0:
            continue
        free[user] -= 1
        result["startable_tasks"] += 1
        result["oldest_startable_seconds"] = max(result["oldest_startable_seconds"], float(waited), 0.0)
    return result


async def main(hosts):
    from redis.asyncio import Redis
    from sqlalchemy import text
    from src.config import get_config
    from src.database import get_session_maker

    config = get_config()
    async with get_session_maker()() as db:
        queued = [
            {"task": row["id"], "user": row["user_id"], "job": row["job_id"], "age": float(row["age"])}
            for row in (await db.execute(text(
                "SELECT id, user_id, source_metadata->>'processing_job_id' AS job_id, "
                "EXTRACT(EPOCH FROM NOW() - created_at) AS age "
                "FROM tasks WHERE status = 'queued' ORDER BY created_at ASC, id ASC"
            ))).mappings().all()
        ]
        processing = {
            row["user_id"]: int(row["n"])
            for row in (await db.execute(text(
                "SELECT user_id, COUNT(*) AS n FROM tasks WHERE status = 'processing' GROUP BY user_id"
            ))).mappings().all()
        }
        # Counting rows needs no extra privilege; the details of other roles' rows do.
        db_connections = int((await db.execute(text("SELECT COUNT(*) FROM pg_stat_activity"))).scalar())
        db_max_connections = int((await db.execute(text("SHOW max_connections"))).scalar())

    redis = Redis(host=config.redis_host, port=config.redis_port, password=config.redis_password, db=0)
    try:
        now = time.time()
        jobs = {}
        for task in queued:
            if not task["job"]:
                continue
            pipe = redis.pipeline(transaction=False)
            pipe.zscore(QUEUE, task["job"])
            pipe.exists("arq:in-progress:" + task["job"])
            pipe.exists(f"task_cancel:{task['task']}", f"upload_wait:{task['task']}")
            score, running, held = await pipe.execute()
            jobs[task["task"]] = {
                # The score is when the job is due, in milliseconds.
                "waited": None if score is None else now - float(score) / 1000,
                "running": bool(running),
                "held": bool(held),
            }
        # Per-account render slots (video_admission.py).
        result = summarize(queued, processing, jobs, config.video_job_user_concurrency)
        result["db_connections"] = db_connections
        result["db_max_connections"] = db_max_connections
        result["queue_depth"] = int(await redis.zcard(QUEUE))
        result["running_by_host"] = {
            host: int(await redis.zcount(HOST_JOBS_PREFIX + host, now - STALE_AFTER_SECONDS, "+inf"))
            for host in hosts
        }
    finally:
        await redis.aclose()
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
