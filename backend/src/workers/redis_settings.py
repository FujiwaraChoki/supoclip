"""One place to build the arq ``RedisSettings`` every process uses.

arq's defaults give a new Redis connection one second to open and never
retry it once the worker is running. On a loaded render host (Redis reached
through a socat gateway) a fresh connection at job start can take longer than
that; arq re-raises the ``TimeoutError`` from its job loop and the whole
worker exits, leaving its claimed jobs stuck until their in-progress keys
expire. A longer connect timeout plus a few backed-off retries rides that out.

The same applies when Redis restarts or drops a connection: redis-py raises
``ConnectionError`` ("Connection closed by server.") and, unless that error is
listed in ``retry_on_error``, re-raises it instead of reconnecting, which also
ends the worker.
"""

from arq.connections import RedisSettings
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

from ..config import Config, get_config


def arq_redis_settings(config: Config | None = None) -> RedisSettings:
    config = config or get_config()
    return RedisSettings(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password,
        database=0,
        conn_timeout=config.redis_connect_timeout_seconds,
        # Startup: create_pool pings this many times, conn_retry_delay apart.
        conn_retries=config.redis_connect_retries,
        conn_retry_delay=1,
        # Running: redis-py retries a command whose connection timed out,
        # failed to open or was closed by the server, reconnecting with
        # backoff of 1s, 2s, 4s… capped at 5s.
        retry_on_timeout=True,
        retry_on_error=[RedisConnectionError],
        retry=Retry(ExponentialBackoff(cap=5, base=0.5), config.redis_connect_retries),
    )
