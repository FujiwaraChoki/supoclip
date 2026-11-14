"""One bounded production trial; the normal proxy remains the fallback.

Admission is atomic and persistent in Redis. Routing is local to an admitted
generation and propagates through asyncio.to_thread, never global config.
"""
import copy
import functools
import json
import logging
import os
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_route = ContextVar("youtube_proxy_trial_route", default=None)
_provider = ContextVar("youtube_proxy_trial_provider", default=None)

ADMIT = """
local existing = redis.call('HGET', KEYS[2], 'sequence')
if existing then return tonumber(existing) end
if redis.call('HGET', KEYS[1], 'status') ~= 'active' then return 0 end
local count = tonumber(redis.call('HGET', KEYS[1], 'admitted') or '0')
if count >= 30 then return 0 end
count = count + 1
redis.call('HSET', KEYS[1], 'admitted', count)
if count == 30 then redis.call('HSET', KEYS[1], 'status', 'draining') end
redis.call('HSET', KEYS[2], 'sequence', count, 'task_id', ARGV[1],
           'admitted_at', ARGV[2], 'active_provider', 'geonode')
redis.call('ZADD', KEYS[3], count, ARGV[1])
return count
"""


@dataclass
class Route:
    prefix: str
    task_id: str
    sequence: int
    template: str
    active: str = "geonode"
    lock: object = field(default_factory=threading.Lock)

    @property
    def key(self):
        return self.prefix + ":job:" + self.task_id


@functools.lru_cache(maxsize=1)
def _sync_redis():
    import redis
    from .config import get_config
    c = get_config()
    return redis.Redis(host=c.redis_host, port=c.redis_port,
                       password=c.redis_password, socket_connect_timeout=1,
                       socket_timeout=1)


def _record(route, event, **values):
    """Metrics must never turn a successful download into a job failure."""
    try:
        values.update(event=event, timestamp=time.time(), task_id=route.task_id,
                      sequence=route.sequence)
        with route.lock:
            p = _sync_redis().pipeline(transaction=True)
            p.rpush(route.prefix + ":events", json.dumps(values))
            if os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER") in {"geonode", "dataimpulse"}:
                p.ltrim(route.prefix + ":events", -10000, -1)
                p.expire(route.prefix + ":events", 604800)
            if event == "provider_call":
                name = values["provider"]
                p.hincrby(route.key, name + "_bytes", values["bytes"])
                p.hincrbyfloat(route.key, name + "_seconds", values["seconds"])
                p.hincrby(route.key, name + "_calls", 1)
                if not values["success"]:
                    p.hincrby(route.key, name + "_failed_calls", 1)
            elif event == "fallback":
                p.hset(route.key, mapping={"active_provider": values.get("provider", "dataimpulse"),
                                          "fallback": "1"})
            if os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER") in {"geonode", "dataimpulse"}:
                p.expire(route.key, 604800)
            p.execute()
    except Exception:
        logger.warning("YouTube trial metric write failed task_id=%s", route.task_id)


def trial_config(config):
    route = _route.get()
    if route is None or (_provider.get() or route.active) != "geonode":
        return config
    result = copy.copy(config)
    result.youtube_proxy_url = route.template
    return result


def trial_log(function):
    @functools.wraps(function)
    def wrapped(**kwargs):
        route = _route.get()
        if route is not None and kwargs.get("provider") == "yt_dlp_proxy":
            provider = _provider.get() or route.active
            kwargs["provider"] = "yt_dlp_" + provider
            _record(route, "attempt", provider=provider,
                    kind=kwargs.get("kind"), outcome=kwargs.get("outcome"),
                    bytes=int(kwargs.get("nbytes", 0)))
        return function(**kwargs)
    return wrapped


def trial_retry(function):
    @functools.wraps(function)
    def wrapped(operation, **kwargs):
        route = _route.get()
        if route is None:
            if os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER") not in {"geonode", "dataimpulse"}:
                return function(operation, **kwargs)
            try:
                route = Route(os.environ["YOUTUBE_PROXY_TRIAL_PREFIX"],
                              kwargs.get("task_id") or "standalone-" + uuid.uuid4().hex,
                              0, _template(), active=os.environ["YOUTUBE_PROXY_DEFAULT_PROVIDER"])
            except Exception:
                return function(operation, **kwargs)
            token = _route.set(route)
            try:
                return wrapped(operation, **kwargs)
            finally:
                _route.reset(token)
        budget = kwargs["budget"]
        provider = route.active
        while True:
            token = _provider.set(provider)
            before, started, success = budget.snapshot(), time.monotonic(), False
            try:
                result = function(operation, **kwargs)
                success = True
                return result
            except Exception as exc:
                terminal = any(c.__name__ in {"DownloadBudgetExceeded", "TransferStopped"}
                               for c in type(exc).__mro__)
                exhausted = budget.limit > 0 and budget.snapshot() >= budget.limit
                primary = os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER", "geonode")
                if provider != primary or terminal or exhausted:
                    raise
                # Preserve the same shared byte budget. Retry metadata and media
                # through DataImpulse, then keep it for this job's later phases.
                provider = "dataimpulse" if primary == "geonode" else "geonode"
                route.active = provider
                _record(route, "fallback", provider=provider, error_type=type(exc).__name__)
            finally:
                # The provider ContextVar still identifies this attempt even
                # after route.active has changed for the next iteration.
                _record(route, "provider_call", provider=_provider.get(),
                        success=success, bytes=max(0, budget.snapshot() - before),
                        seconds=round(time.monotonic() - started, 4))
                _provider.reset(token)
    return wrapped


def _template():
    secret = json.loads(Path(os.environ["YOUTUBE_PROXY_TRIAL_SECRET_FILE"]).read_text())
    template = secret["GEONODE_PROXY_URL"]
    parsed = urlsplit(template)
    if parsed.scheme != "http" or parsed.hostname != "proxy.geonode.io" or not parsed.password:
        raise ValueError("Invalid primary proxy configuration")
    return template


async def run_trial_job(function, redis, trial_task_id, trial_source_type, **kwargs):
    task_id, source_type = trial_task_id, trial_source_type
    prefix = os.environ.get("YOUTUBE_PROXY_TRIAL_PREFIX", "")
    if not prefix or source_type != "youtube":
        return await function(**kwargs)
    try:
        template = _template()
        job_key = prefix + ":job:" + task_id
        if os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER") in {"geonode", "dataimpulse"}:
            sequence = 0
        else:
            sequence = await redis.eval(ADMIT, 3, prefix, job_key, prefix + ":jobs",
                                        task_id, str(time.time()))
    except Exception:
        logger.warning("YouTube trial admission unavailable; using normal proxy task_id=%s", task_id)
        return await function(**kwargs)
    if not sequence and os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER") not in {"geonode", "dataimpulse"}:
        return await function(**kwargs)
    try:
        active = await redis.hget(job_key, "active_provider")
    except Exception:
        active = b"dataimpulse"
    if isinstance(active, bytes):
        active = active.decode()
    route = Route(prefix, task_id, int(sequence), template,
                  active=active if active in {"geonode", "dataimpulse"} else os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER", "geonode"))
    token = _route.set(route)
    started = time.monotonic()
    result, failure = None, None
    logger.info("YouTube proxy routing provider=%s sequence=%s task_id=%s", route.active, sequence, task_id)
    try:
        try:
            await redis.hset(job_key, mapping={"last_started_at": str(time.time()),
                                              "worker_status": "running"})
        except Exception:
            logger.warning("YouTube trial start metric write failed task_id=%s", task_id)
        result = await function(**kwargs)
        return result
    except BaseException as exc:
        failure = type(exc).__name__
        raise
    finally:
        _route.reset(token)
        try:
            await redis.hset(job_key, mapping={
                "worker_status": "raised" if failure else "returned",
                "exception_type": failure or "",
                "last_finished_at": str(time.time()),
                "last_worker_seconds": str(round(time.monotonic() - started, 4)),
            })
            if os.environ.get("YOUTUBE_PROXY_DEFAULT_PROVIDER") in {"geonode", "dataimpulse"}:
                await redis.expire(job_key, 604800)
        except Exception:
            logger.warning("YouTube trial final metric write failed task_id=%s", task_id)
