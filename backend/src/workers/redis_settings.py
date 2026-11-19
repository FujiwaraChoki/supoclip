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

A connection can also die without being closed (Redis restarted behind a
tunnel): with no read timeout the worker's next poll waits on it for as long as
the OS takes to notice, and the worker silently stops taking jobs. arq's
``RedisSettings`` has no socket options, so ``use_socket_options`` adds a read
timeout and TCP keepalive to a pool once it exists; the timeout is retried on a
new connection like any other. No arq pool runs blocking commands (BLPOP,
pub/sub); progress streams use their own Redis client.
"""

import socket

from arq.connections import ArqRedis, RedisSettings
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


# Probe an idle connection after 60s, give up after three missed probes 10s apart.
_KEEPALIVE = {"TCP_KEEPIDLE": 60, "TCP_KEEPINTVL": 10, "TCP_KEEPCNT": 3}


def arq_socket_options(config: Config | None = None) -> dict:
    config = config or get_config()
    return {
        "socket_timeout": config.redis_socket_timeout_seconds,
        "socket_keepalive": True,
        "socket_keepalive_options": {
            getattr(socket, name): value
            for name, value in _KEEPALIVE.items()
            if hasattr(socket, name)
        },
    }


async def use_socket_options(redis: ArqRedis, config: Config | None = None) -> None:
    """Give every connection of ``redis`` a read timeout and keepalive."""
    options = arq_socket_options(config)
    pool = redis.connection_pool
    pool.connection_kwargs.update(options)
    for connection in (*pool._available_connections, *pool._in_use_connections):
        for name, value in options.items():
            setattr(connection, name, value)
    # Keepalive is set when a socket opens; idle ones reopen on next use.
    await pool.disconnect(inuse_connections=False)


async def on_arq_startup(ctx: dict) -> None:
    """``on_startup`` for workers that have nothing else to start."""
    await use_socket_options(ctx["redis"])
