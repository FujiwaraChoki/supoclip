#!/usr/bin/env python3
"""Add and remove cloud render workers as the video queue grows and empties.

Runs on the application host from a systemd timer (every two minutes), one run
at a time. Python 3.11+ standard library only. Read docs/autoscaling.md first.

Each run:
1. reads the queue and database connections (probe.py inside the backend container),
2. lists the cloud servers labelled as managed by the autoscaler,
3. decides (decide.py) and carries out at most one scale-up plus any drains/deletes,
4. sends an alert for each scale event, failure and spend-cap hit.

Nothing here prints a credential. Secrets reach a new machine on SSH stdin, never
on a command line, and the cloud API token is only ever sent in a request header.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))

import decide
from decide import ACTIVE, DRAINING, PROVISIONING, Action, Config, Metrics, Worker

log = logging.getLogger("render-autoscaler")
HERE = Path(__file__).resolve().parent
API = "https://api.hetzner.cloud/v1"
MANAGED_LABEL = "supoclip-autoscaler"
MANAGED_VALUE = "managed"
REMOTE_COMPOSE = "/opt/supoclip-render/compose.yml"
REMOTE_COMPOSE_ENV = "/etc/supoclip/render-compose.env"
REMOTE_WORKER_ENV = "/etc/supoclip/worker.env"
REMOTE_PROXY_SECRET = "/etc/supoclip/proxy-secret.json"
WORKER_FILTER = "label=com.docker.compose.service=worker"


class AutoscalerError(Exception):
    pass


class CloudError(AutoscalerError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"cloud API {status} {code}: {message}")
        self.status = status
        self.code = code


# --------------------------------------------------------------------------- settings


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    policy: Config
    token_file: str = ""
    server_type: str = "ccx53"
    location: str = "hel1"
    concurrency: int = 10
    poll_seconds: float = 5.0
    overflow_after_seconds: float = 15.0
    db_pool_size: int = 10
    db_max_overflow: int = 5
    snapshot_selector: str = "supoclip-render-golden=true"
    firewall: str = ""
    cloud_ssh_key: str = ""
    image_source: str = "container:supoclip-worker"
    probe_container: str = "supoclip-worker"
    worker_env_file: str = ""
    compose_env_file: str = ""
    proxy_secret_file: str = ""
    ssh_key: str = ""
    ssh_user: str = "supoclip"
    state_dir: str = "/var/lib/supoclip-autoscaler"
    tunnel_unit: str = "supoclip-render-tunnel"
    alert_url: str = ""
    alert_secret: str = ""
    error_backoff_seconds: int = 1800
    alert_repeat_seconds: int = 3600

    @property
    def known_hosts(self) -> Path:
        return Path(self.state_dir) / "known_hosts"


def load_settings(env: dict[str, str], *, dry_run: bool = False) -> Settings:
    def num(name: str, default: float, cast=int):
        raw = env.get(name, "").strip()
        return cast(raw) if raw else cast(default)

    policy = Config(
        enabled=_bool(env.get("AUTOSCALER_ENABLED")),
        dry_run=dry_run or _bool(env.get("AUTOSCALER_DRY_RUN")),
        scale_up_wait_seconds=num("AUTOSCALER_SCALE_UP_WAIT_MINUTES", 10) * 60,
        scale_up_queue_depth=num("AUTOSCALER_SCALE_UP_QUEUE_DEPTH", 20),
        scale_up_cooldown_seconds=num("AUTOSCALER_SCALE_UP_COOLDOWN_MINUTES", 10) * 60,
        idle_seconds=num("AUTOSCALER_IDLE_MINUTES", 30) * 60,
        max_extra_workers=num("AUTOSCALER_MAX_EXTRA_WORKERS", 2),
        daily_spend_cap_eur=num("AUTOSCALER_DAILY_SPEND_CAP_EUR", 40.0, float),
        hourly_price_eur=num("AUTOSCALER_HOURLY_PRICE_EUR", 0.7847, float),
        db_connections_per_worker=num("AUTOSCALER_DB_CONNECTIONS_PER_WORKER", 25),
        db_connection_reserve=num("AUTOSCALER_DB_CONNECTION_RESERVE", 20),
        name_prefix=env.get("AUTOSCALER_NAME_PREFIX", "").strip() or "supoclip-render-auto",
    )
    concurrency = num("AUTOSCALER_WORKER_CONCURRENCY", 10)
    return Settings(
        policy=policy,
        token_file=env.get("AUTOSCALER_HCLOUD_TOKEN_FILE", "").strip(),
        server_type=env.get("AUTOSCALER_SERVER_TYPE", "").strip() or "ccx53",
        location=env.get("AUTOSCALER_LOCATION", "").strip() or "hel1",
        concurrency=concurrency,
        poll_seconds=num("AUTOSCALER_WORKER_POLL_SECONDS", 5.0, float),
        overflow_after_seconds=num("AUTOSCALER_WORKER_OVERFLOW_AFTER_SECONDS", 15.0, float),
        db_pool_size=num("AUTOSCALER_WORKER_DB_POOL_SIZE", concurrency),
        db_max_overflow=num("AUTOSCALER_WORKER_DB_MAX_OVERFLOW", 5),
        snapshot_selector=env.get("AUTOSCALER_SNAPSHOT_SELECTOR", "").strip() or "supoclip-render-golden=true",
        firewall=env.get("AUTOSCALER_FIREWALL", "").strip(),
        cloud_ssh_key=env.get("AUTOSCALER_CLOUD_SSH_KEY", "").strip(),
        image_source=env.get("AUTOSCALER_IMAGE_SOURCE", "").strip() or "container:supoclip-worker",
        probe_container=env.get("AUTOSCALER_PROBE_CONTAINER", "").strip() or "supoclip-worker",
        worker_env_file=env.get("AUTOSCALER_WORKER_ENV_FILE", "").strip(),
        compose_env_file=env.get("AUTOSCALER_COMPOSE_ENV_FILE", "").strip(),
        proxy_secret_file=env.get("AUTOSCALER_PROXY_SECRET_FILE", "").strip(),
        ssh_key=env.get("AUTOSCALER_SSH_KEY", "").strip(),
        ssh_user=env.get("AUTOSCALER_SSH_USER", "").strip() or "supoclip",
        state_dir=env.get("AUTOSCALER_STATE_DIR", "").strip() or "/var/lib/supoclip-autoscaler",
        tunnel_unit=env.get("AUTOSCALER_TUNNEL_UNIT", "").strip() or "supoclip-render-tunnel",
        alert_url=env.get("HERMES_OPS_ALERT_URL", "").strip(),
        alert_secret=env.get("HERMES_OPS_ALERT_SECRET", "").strip(),
        error_backoff_seconds=num("AUTOSCALER_ERROR_BACKOFF_MINUTES", 30) * 60,
    )


def read_token(path: str) -> str:
    """A bare token, or the active context's token from an hcloud CLI config."""
    raw = Path(path).read_text()
    if "[[contexts]]" not in raw:
        return raw.strip()
    data = tomllib.loads(raw)
    contexts = data.get("contexts", [])
    active = data.get("active_context")
    for context in contexts:
        if context.get("name") == active or len(contexts) == 1:
            return context["token"]
    raise AutoscalerError("no active context in the cloud CLI config")


# --------------------------------------------------------------------------- cloud API


class Cloud:
    """The few Hetzner Cloud API calls the autoscaler makes. Never retries in a loop."""

    def __init__(self, token: str, opener=None):
        self._token = token
        self._open = (opener or build_opener(ProxyHandler({}))).open

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(API + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._open(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as error:
            try:
                detail = json.loads(error.read() or b"{}").get("error", {})
            except ValueError:
                detail = {}
            raise CloudError(error.code, detail.get("code", "http_error"), detail.get("message", "")) from None
        except (URLError, TimeoutError, OSError) as error:
            raise CloudError(0, "unreachable", type(error).__name__) from None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise CloudError(0, "bad_response", "the cloud API did not answer with JSON") from None

    def managed_servers(self) -> list[dict]:
        selector = f"{MANAGED_LABEL}={MANAGED_VALUE}"
        return self.request("GET", f"/servers?label_selector={selector}&per_page=50").get("servers", [])

    def newest_snapshot(self, selector: str) -> dict | None:
        images = self.request("GET", f"/images?type=snapshot&label_selector={selector}&per_page=50")["images"]
        images = [image for image in images if image.get("status") == "available"]
        return max(images, key=lambda image: image["created"], default=None)

    def find_id(self, kind: str, name: str) -> int:
        items = self.request("GET", f"/{kind}?name={name}")[kind]
        if len(items) != 1:
            raise AutoscalerError(f"{kind} {name!r} not found")
        return items[0]["id"]

    def create_server(self, body: dict) -> dict:
        return self.request("POST", "/servers", body)["server"]

    def get_server(self, server_id: int) -> dict:
        return self.request("GET", f"/servers/{server_id}")["server"]

    def set_labels(self, server: dict, **labels: str) -> None:
        merged = {**server.get("labels", {}), **labels}
        self.request("PUT", f"/servers/{server['id']}", {"labels": merged})
        server["labels"] = merged

    def delete_server(self, server: dict) -> None:
        if server.get("labels", {}).get(MANAGED_LABEL) != MANAGED_VALUE:
            raise AutoscalerError(f"refusing to delete {server['name']}: not managed by the autoscaler")
        self.request("DELETE", f"/servers/{server['id']}")


def hourly_price(server: dict, location: str, fallback: float) -> float:
    for price in server.get("server_type", {}).get("prices", []):
        if price.get("location") == location:
            try:
                return float(price["price_hourly"]["gross"])
            except (KeyError, TypeError, ValueError):
                break
    return fallback


def public_ip(server: dict) -> str:
    return server["public_net"]["ipv4"]["ip"]


# --------------------------------------------------------------------------- SSH


class Remote:
    """Commands on a cloud worker as the admin user. Secrets go on stdin only."""

    def __init__(self, settings: Settings, runner=subprocess.run):
        self.settings = settings
        self.runner = runner

    def base(self, ip: str) -> list[str]:
        return [
            "ssh", "-i", self.settings.ssh_key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.settings.known_hosts}",
            "-o", "ConnectTimeout=10", f"{self.settings.ssh_user}@{ip}",
        ]

    def run(self, ip: str, command: str, *, stdin: bytes | None = None, timeout: int = 60,
            check: bool = True) -> subprocess.CompletedProcess:
        result = self.runner(self.base(ip) + [command], input=stdin, capture_output=True, timeout=timeout)
        if check and result.returncode != 0:
            # stderr of these commands carries no secrets; keep it short anyway.
            tail = (result.stderr or b"").decode(errors="replace").strip().splitlines()[-3:]
            raise AutoscalerError(f"remote command failed ({result.returncode}): {' | '.join(tail)}")
        return result

    def container_running(self, ip: str) -> bool | None:
        """True while any container is up on the machine, None when that is unknown.

        Deliberately not filtered to the worker's compose label: a worker started
        another way (an adopted machine) must never look like it has exited.
        """
        try:
            result = self.run(ip, "sudo docker ps -q", timeout=30)
        except (AutoscalerError, subprocess.TimeoutExpired):
            return None
        return bool(result.stdout.strip())

    def drain(self, ip: str) -> None:
        """Stop taking jobs; let running ones finish. Never stop or kill the container."""
        self.run(ip, (
            f"set -e; for c in $(sudo docker ps -q --filter {WORKER_FILTER}); do "
            "sudo docker update --restart=no \"$c\" >/dev/null; "
            "sudo docker kill -s TERM \"$c\" >/dev/null; done"
        ))

    def write_secret(self, ip: str, path: str, content: bytes) -> None:
        directory = shlex.quote(str(Path(path).parent))
        self.run(ip, (
            f"sudo install -d -m 700 {directory} && "
            f"sudo sh -c 'umask 077; cat > {shlex.quote(path)}'"
        ), stdin=content)


# --------------------------------------------------------------------------- alerts


# Alert event -> (level, title) in the operator chat.
ALERT_KINDS = {
    "scale_up": ("info", "Render worker added"),
    "scale_down": ("info", "Render worker removed"),
    "blocked": ("warning", "Render scale-up blocked"),
    "probe": ("warning", "Render autoscaler cannot read the queue"),
    "failure": ("error", "Render autoscaler failure"),
    "test": ("info", "Autoscaler alerts connected"),
}


class Alerts:
    """Signed ops alerts to the operator chat (the Hermes V2 signature the API uses)."""

    # Waits between attempts when the gateway is unreachable, errors or does not file the alert.
    RETRY_DELAYS = (2, 6, 15)

    def __init__(self, url: str, secret: str, *, dry_run: bool, opener=None, sleep=time.sleep):
        self.url, self.secret, self.dry_run = url, secret, dry_run
        self._open = (opener or build_opener(ProxyHandler({}))).open
        self._sleep = sleep

    def send(self, event: str, message: str, *, title: str | None = None) -> bool | None:
        """True when the gateway filed the alert, False when it did not, None when none was sent."""
        level, default_title = ALERT_KINDS.get(event, ("warning", "Render autoscaler"))
        log.warning("alert %s: %s", event, message)
        if self.dry_run or not self.url or not self.secret:
            return None
        delivery = str(uuid.uuid4())
        body = json.dumps({
            "type": "supoclip.ops_alert",
            "version": 1,
            "event_id": delivery,
            "source": "autoscaler",
            "level": level,
            "title": title or default_title,
            "message": message,
            "created_at": datetime.now(UTC).isoformat(),
        }).encode()
        # The same event id on every attempt, so a retry is never filed twice.
        for attempt, delay in enumerate((0, *self.RETRY_DELAYS)):
            if delay:
                self._sleep(delay)
            outcome = self._post(body, delivery)
            if outcome == "filed":
                return True
            log.error("alert delivery attempt %d: %s", attempt + 1, outcome)
        return False

    def _post(self, body: bytes, delivery: str) -> str:
        timestamp = str(int(time.time()))
        signature = hmac.new(self.secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        request = Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature-V2": signature,
            "X-Request-ID": delivery,
        })
        try:
            with self._open(request, timeout=15) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            return f"HTTP {error.code}"
        except (URLError, TimeoutError, OSError) as error:
            return type(error).__name__
        if not 200 <= status < 300:
            return f"HTTP {status}"
        try:
            answer = json.loads(raw or b"{}")
        except ValueError:
            answer = {}
        # The gateway answers 200 with status "ignored" when it did not file the alert.
        if isinstance(answer, dict) and answer.get("status") == "ignored":
            return "ignored by the gateway"
        return "filed"


# --------------------------------------------------------------------------- state


class State:
    """Timers, spend records and alert suppression, in one JSON file."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            self.data = {}
        self.data.setdefault("workers", {})
        self.data.setdefault("spend", [])
        self.data.setdefault("alerts", {})

    def worker(self, name: str) -> dict:
        return self.data["workers"].setdefault(name, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Spend older than two days is no longer needed for today's total.
        horizon = time.time() - 2 * 86400
        self.data["spend"] = [s for s in self.data["spend"] if s.get("end") is None or s["end"] > horizon]
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as handle:
            json.dump(self.data, handle, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def open_spend(self, name: str, start: float, hourly: float) -> None:
        if not any(s["name"] == name and s.get("end") is None for s in self.data["spend"]):
            self.data["spend"].append({"name": name, "start": start, "end": None, "hourly_eur": hourly})

    def close_spend(self, name: str, end: float) -> None:
        for record in self.data["spend"]:
            if record["name"] == name and record.get("end") is None:
                record["end"] = end

    def alert_due(self, key: str, now: float, repeat: int) -> bool:
        last = self.data["alerts"].get(key) if key else None
        return last is None or now - last >= repeat

    def alerted(self, key: str, now: float) -> None:
        if key:
            self.data["alerts"][key] = now


# --------------------------------------------------------------------------- local host


def resolve_image(source: str, runner=subprocess.run) -> str | None:
    """The backend release new workers run. Follows the deployed release, never a pin.

    `container:<name>`: the image of that running container on this host (default:
    the cron worker, which every release rolls). `file:<path>`: written by the deploy flow.
    """
    kind, _, value = source.partition(":")
    if kind == "file":
        try:
            image = Path(value).read_text().strip()
        except OSError:
            return None
        if not image:
            return None
        # A typo here would otherwise drain every worker as "on another release".
        found = runner(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                       capture_output=True, text=True, timeout=20)
        if found.returncode != 0:
            log.error("the image named in %s is not on this host", value)
            return None
        return image
    if kind == "container":
        result = runner(["docker", "inspect", "--format", "{{.Config.Image}}", value],
                        capture_output=True, text=True, timeout=20)
        image = result.stdout.strip() if result.returncode == 0 else ""
        return image or None
    raise AutoscalerError(f"unknown AUTOSCALER_IMAGE_SOURCE {source!r}")


def read_metrics(container: str, hosts: list[str], runner=subprocess.run) -> Metrics:
    command = ["docker", "exec", "-i", container, "/app/.venv/bin/python", "-", *hosts]
    result = runner(command, input=(HERE / "probe.py").read_bytes(), capture_output=True, timeout=60)
    if result.returncode != 0:
        raise AutoscalerError(f"probe failed with exit code {result.returncode}")
    data = json.loads(result.stdout.decode().strip().splitlines()[-1])
    return Metrics(**data)


def tunnel(settings: Settings, name: str, ip: str | None, *, start: bool, runner=subprocess.run) -> None:
    """Start or stop the reverse tunnel that gives a worker PostgreSQL, Redis and bgutil."""
    unit = f"{settings.tunnel_unit}@{name}.service"
    env_file = Path(settings.state_dir) / "tunnels" / f"{name}.env"
    if start:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(f"WORKER_ADDRESS={ip}\n")
        action = "restart"
    else:
        action = "stop"
    result = runner(["sudo", "-n", "systemctl", action, unit], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise AutoscalerError(f"systemctl {action} {unit} failed: {result.stderr.strip()[-200:]}")
    if not start:
        env_file.unlink(missing_ok=True)


def forget_host_key(settings: Settings, ip: str) -> None:
    path = settings.known_hosts
    if path.exists():
        kept = [line for line in path.read_text().splitlines() if line.split(" ", 1)[0] != ip]
        path.write_text("".join(f"{line}\n" for line in kept))


# --------------------------------------------------------------------------- provisioning

VALIDATE = r"""
import asyncio, os, sys
async def main():
    if os.environ.get("MEDIA_STORAGE") != "s3":
        sys.exit("MEDIA_STORAGE is not s3")
    from sqlalchemy import text
    from src.database import get_session_maker
    async with get_session_maker()() as db:
        if (await db.execute(text("SELECT to_regclass('public.tasks') IS NOT NULL"))).scalar() is not True:
            sys.exit("the database has no tasks table")
    from redis.asyncio import Redis
    from src.config import get_config
    c = get_config()
    redis = Redis(host=c.redis_host, port=c.redis_port, password=c.redis_password)
    await redis.ping()
    if not await redis.exists("supoclip_tasks:health-check"):
        sys.exit("Redis has no video worker heartbeat; is this the production queue?")
    await redis.aclose()
    print("ok")
try:
    asyncio.run(main())
except SystemExit:
    raise
except BaseException as error:
    sys.exit(f"check failed: {type(error).__name__}")
"""


def cloud_init(name: str, host_private: str, host_public: str) -> str:
    """Sets the hostname and a host key we generated, so the first SSH is already pinned."""
    indented = "\n".join("      " + line for line in host_private.strip().splitlines())
    return (
        "#cloud-config\n"
        f"hostname: {name}\n"
        "preserve_hostname: false\n"
        "ssh_deletekeys: true\n"
        "ssh_genkeytypes: []\n"
        "ssh_keys:\n"
        "  ed25519_private: |\n"
        f"{indented}\n"
        f"  ed25519_public: {host_public.strip()}\n"
    )


def compose_env(settings: Settings, name: str, image: str, template: str) -> bytes:
    """The machine's render-compose.env: the host's template plus what this worker needs."""
    ours = {
        "WORKER_IMAGE": image,
        "WORKER_ENV_FILE": REMOTE_WORKER_ENV,
        "WORKER_PROXY_SECRET_FILE": REMOTE_PROXY_SECRET,
        "WORKER_SCRATCH_PATH": "/srv/supoclip-scratch",
        "VIDEO_WORKER_CONCURRENCY": str(settings.concurrency),
        "VIDEO_WORKER_POLL_DELAY": str(settings.poll_seconds),
        "VIDEO_WORKER_OVERFLOW_AFTER_SECONDS": str(settings.overflow_after_seconds),
        "WORKER_DB_POOL_SIZE": str(settings.db_pool_size),
        "WORKER_DB_MAX_OVERFLOW": str(settings.db_max_overflow),
        "RENDER_HOST_NAME": name,
    }
    lines = [line for line in template.splitlines()
             if line.strip() and not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() not in ours]
    lines += [f"{key}={value}" for key, value in ours.items()]
    return ("\n".join(lines) + "\n").encode()


class Autoscaler:
    def __init__(self, settings: Settings, cloud: Cloud, *, remote: Remote | None = None,
                 alerts: Alerts | None = None, runner=subprocess.run, clock=time.time, sleep=time.sleep):
        self.s = settings
        self.cloud = cloud
        self.remote = remote or Remote(settings)
        self.alerts = alerts or Alerts(settings.alert_url, settings.alert_secret, dry_run=settings.policy.dry_run)
        self.runner = runner
        self.clock = clock
        self.sleep = sleep
        name = "state.dry-run.json" if settings.policy.dry_run else "state.json"
        self.state = State(Path(settings.state_dir) / name)

    # ---- facts

    def workers(self, servers: list[dict]) -> list[Worker]:
        workers = []
        for server in servers:
            labels = server.get("labels", {})
            record = self.state.worker(server["name"])
            created = datetime.fromisoformat(server["created"]).timestamp()
            state = labels.get("state", ACTIVE)
            worker = Worker(
                name=server["name"], state=state, image=labels.get("image", ""), created_at=created,
                idle_since=record.get("idle_since"),
                drain_started=record.get("drain_started") or _float(labels.get("drain-started")),
            )
            if state in (DRAINING, PROVISIONING):
                worker.container_running = self.remote.container_running(public_ip(server))
            workers.append(worker)
            # A server the state file does not know (lost state, adopted machine) is billed from creation.
            self.state.open_spend(server["name"], created, record.get("hourly_eur", self.s.policy.hourly_price_eur))
        return workers

    def spend(self, now: float) -> float:
        day = datetime.fromtimestamp(now, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return decide.spend_today(self.state.data["spend"], day.timestamp(), now)

    # ---- one run

    def run(self) -> decide.Plan:
        now = self.clock()
        policy = self.s.policy
        if not policy.enabled and not policy.dry_run:
            log.info("AUTOSCALER_ENABLED is not true; nothing to do")
            return decide.Plan(notes=["disabled"])
        if policy.dry_run and not policy.enabled:
            # A dry run shows what an enabled autoscaler would do.
            self.s = replace(self.s, policy=replace(policy, enabled=True))
            policy = self.s.policy

        try:
            servers = self.cloud.managed_servers()
        except CloudError as error:
            self.alert("failure", f"list:{error.code}", f"could not list cloud workers: {error}", now)
            self.state.save()
            return decide.Plan(notes=[str(error)])
        by_name = {server["name"]: server for server in servers}
        workers = self.workers(servers)
        metrics = None
        try:
            metrics = read_metrics(self.s.probe_container, sorted(by_name), runner=self.runner)
        except (AutoscalerError, subprocess.TimeoutExpired, ValueError, TypeError) as error:
            self.alert("probe", "probe-failed", f"could not read the queue: {error}", now)
        if metrics is not None:
            for worker in workers:
                worker.running_jobs = metrics.running_by_host.get(worker.name)
        image = None
        try:
            image = resolve_image(self.s.image_source, runner=self.runner)
        except (AutoscalerError, subprocess.TimeoutExpired) as error:
            log.error("%s", error)

        spend = self.spend(now)
        plan = decide.decide(
            policy, now, metrics, workers, current_image=image, spend=spend,
            last_scale_up=self.state.data.get("last_scale_up"),
            backoff_until=self.state.data.get("backoff_until"),
        )
        summary = {
            "queued": metrics and metrics.queued_tasks,
            "startable": metrics and metrics.startable_tasks,
            "oldest_wait_min": metrics and round(metrics.oldest_queued_seconds / 60, 1),
            "oldest_startable_wait_min": metrics and round(metrics.oldest_startable_seconds / 60, 1),
            "queue_depth": metrics and metrics.queue_depth,
            "db": metrics and f"{metrics.db_connections}/{metrics.db_max_connections}",
            "workers": {w.name: f"{w.state} running={w.running_jobs}" for w in workers},
            "spend_today_eur": spend,
            "image": image,
            "actions": [asdict(a) for a in plan.actions],
            "notes": plan.notes,
        }
        log.info("%s%s", "[dry run] " if policy.dry_run else "", json.dumps(summary, sort_keys=True))

        for name, since in plan.idle_since.items():
            self.state.worker(name)["idle_since"] = since
        for action in plan.actions:
            if action.kind == "alert":
                self.alert("blocked", action.key, action.reason, now)
            elif policy.dry_run:
                log.info("[dry run] would %s %s: %s", action.kind, action.name, action.reason)
            else:
                self.apply(action, by_name.get(action.name), image, now)
        self.state.save()
        return plan

    def alert(self, event: str, key: str, message: str, now: float) -> None:
        """Send an alert, at most once per repeat interval for the same key."""
        if not self.state.alert_due(key, now, self.s.alert_repeat_seconds):
            return
        # An alert the gateway did not file is tried again on the next run.
        if self.alerts.send(event, message) is not False:
            self.state.alerted(key, now)

    def apply(self, action: Action, server: dict | None, image: str | None, now: float) -> None:
        try:
            if action.kind == "scale_up":
                self.scale_up(action.name, image, action.reason)
            elif action.kind == "drain" and server:
                self.drain(server, action.reason, now)
            elif action.kind == "delete" and server:
                self.delete(server, action.reason, now)
            elif action.kind == "activate" and server:
                self.cloud.set_labels(server, state=ACTIVE)
        except CloudError as error:
            self.state.data["backoff_until"] = now + self.s.error_backoff_seconds
            self.alert("failure", f"cloud:{action.kind}:{error.code}",
                       f"{action.kind} {action.name} failed: {error}; scale-ups paused for "
                       f"{self.s.error_backoff_seconds // 60} min", now)
        except (AutoscalerError, subprocess.SubprocessError, OSError) as error:
            message = f"{action.kind} {action.name} failed: {error}"
            if action.kind == "scale_up":
                # A server that was created and deleted again is still billed for an hour:
                # never retry a failing scale-up every cooldown.
                self.state.data["backoff_until"] = now + self.s.error_backoff_seconds
                message += f"; scale-ups paused for {self.s.error_backoff_seconds // 60} min"
            self.alert("failure", f"{action.kind}:{action.name}", message, now)

    # ---- actions

    def drain(self, server: dict, reason: str, now: float) -> None:
        # SIGTERM first: if the machine cannot be reached it stays active and is tried again.
        self.remote.drain(public_ip(server))
        self.state.worker(server["name"]).update(drain_started=now, idle_since=None)
        self.cloud.set_labels(server, state=DRAINING, **{"drain-started": str(int(now))})
        self.alerts.send("scale_down", f"draining {server['name']}: {reason}", title="Render worker draining")

    def delete(self, server: dict, reason: str, now: float) -> None:
        # Checked again right before the delete: never remove a machine with a running worker.
        if self.remote.container_running(public_ip(server)) is not False:
            raise AutoscalerError(f"{server['name']} still has a running worker (or is unreachable); not deleting")
        self.cloud.delete_server(server)
        forget_host_key(self.s, public_ip(server))
        self.state.close_spend(server["name"], now)
        self.state.data["workers"].pop(server["name"], None)
        try:
            tunnel(self.s, server["name"], None, start=False, runner=self.runner)
        except AutoscalerError as error:
            # The server is gone; a tunnel left retrying is untidy, not unsafe.
            log.error("%s", error)
        self.alerts.send("scale_down", f"deleted {server['name']}: {reason}; spend today EUR "
                         f"{self.spend(now):.2f}")

    def scale_up(self, name: str, image: str | None, reason: str) -> None:
        if not image:
            raise AutoscalerError("no release image")
        started = self.clock()
        snapshot = self.cloud.newest_snapshot(self.s.snapshot_selector)
        if snapshot is None:
            raise AutoscalerError(f"no golden snapshot matches {self.s.snapshot_selector!r}")
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "host"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key)],
                           check=True, capture_output=True, timeout=30)
            host_private, host_public = key.read_text(), key.with_suffix(".pub").read_text()
        body = {
            "name": name,
            "server_type": self.s.server_type,
            "location": self.s.location,
            "image": snapshot["id"],
            "start_after_create": True,
            "user_data": cloud_init(name, host_private, host_public),
            "labels": {MANAGED_LABEL: MANAGED_VALUE, "role": "render-worker", "state": PROVISIONING,
                       "image": decide.image_label(image)},
            "public_net": {"enable_ipv4": True, "enable_ipv6": True},
        }
        if self.s.firewall:
            body["firewalls"] = [{"firewall": self.cloud.find_id("firewalls", self.s.firewall)}]
        if self.s.cloud_ssh_key:
            body["ssh_keys"] = [self.s.cloud_ssh_key]
        server = self.cloud.create_server(body)
        now = self.clock()
        hourly = hourly_price(server, self.s.location, self.s.policy.hourly_price_eur)
        self.state.data["last_scale_up"] = now
        self.state.worker(name).update(hourly_eur=hourly)
        self.state.open_spend(name, now, hourly)
        self.state.save()
        ip = public_ip(server)
        self.alerts.send("scale_up", f"creating {name} ({self.s.server_type}, {self.s.concurrency} slots, "
                         f"EUR {hourly:.4f}/h): {reason}", title="Render worker starting")
        try:
            self.provision(server, name, ip, host_public, image)
        except BaseException as error:
            # The worker was never started, so nothing can be running there.
            self.abandon(server, name, ip)
            raise AutoscalerError(f"provisioning {name} failed; deleting the server: {error}") from error
        try:
            self.start_worker(ip)
            self.cloud.set_labels(server, state=ACTIVE)
        except BaseException as error:
            # From here the worker may have taken jobs: drain it, never delete it outright.
            try:
                self.drain(server, f"it failed to start: {error}", self.clock())
            except (AutoscalerError, CloudError, subprocess.SubprocessError, OSError) as drain_error:
                log.error("could not drain %s: %s", name, drain_error)
            raise AutoscalerError(f"starting the worker on {name} failed: {error}") from error
        self.alerts.send("scale_up", f"{name} is taking jobs ({int(self.clock() - started)} s to ready)")

    def provision(self, server: dict, name: str, ip: str, host_public: str, image: str) -> None:
        known = self.s.known_hosts
        known.parent.mkdir(parents=True, exist_ok=True)
        forget_host_key(self.s, ip)
        with known.open("a") as handle:
            handle.write(f"{ip} {' '.join(host_public.split()[:2])}\n")
        self.wait_for_ssh(server, ip)
        tunnel(self.s, name, ip, start=True, runner=self.runner)
        self.remote.write_secret(ip, REMOTE_WORKER_ENV, Path(self.s.worker_env_file).read_bytes())
        if self.s.proxy_secret_file:
            self.remote.write_secret(ip, REMOTE_PROXY_SECRET, Path(self.s.proxy_secret_file).read_bytes())
        else:
            self.remote.write_secret(ip, REMOTE_PROXY_SECRET, b"{}\n")
        template = Path(self.s.compose_env_file).read_text()
        self.remote.write_secret(ip, REMOTE_COMPOSE_ENV, compose_env(self.s, name, image, template))
        self.ship_image(ip, image)
        compose = f"sudo docker compose --env-file {REMOTE_COMPOSE_ENV} -f {REMOTE_COMPOSE}"
        # Lesson learned: a worker started against the wrong database fails every job it
        # picks up within seconds. Check PostgreSQL and Redis in a throwaway container first.
        deadline = self.clock() + 120
        while True:
            result = self.remote.run(
                ip, f"{compose} run --rm --no-deps -T worker .venv/bin/python -", stdin=VALIDATE.encode(),
                timeout=180, check=False,
            )
            if result.returncode == 0:
                break
            if self.clock() > deadline:
                reason = (result.stderr or b"").decode(errors="replace").strip().splitlines()[-1:]
                raise AutoscalerError(f"connectivity check failed: {' '.join(reason)[-200:]}")
            self.sleep(10)

    def start_worker(self, ip: str) -> None:
        compose = f"sudo docker compose --env-file {REMOTE_COMPOSE_ENV} -f {REMOTE_COMPOSE}"
        self.remote.run(ip, f"{compose} up -d --no-build --pull never", timeout=180)
        self.sleep(20)
        if self.remote.container_running(ip) is False:
            raise AutoscalerError("the worker container did not stay up")

    def wait_for_ssh(self, server: dict, ip: str) -> None:
        deadline = self.clock() + 300
        while True:
            try:
                self.remote.run(ip, "cloud-init status --wait >/dev/null 2>&1; true", timeout=200)
                return
            except (AutoscalerError, subprocess.TimeoutExpired):
                if self.clock() > deadline:
                    raise AutoscalerError("SSH did not come up within 5 minutes") from None
                self.sleep(10)

    def ship_image(self, ip: str, image: str) -> None:
        quoted = shlex.quote(image)
        if self.remote.run(ip, f"sudo docker image inspect {quoted} >/dev/null 2>&1", check=False).returncode == 0:
            return
        save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
        gzip = subprocess.Popen(["gzip", "-1"], stdin=save.stdout, stdout=subprocess.PIPE)
        save.stdout.close()
        load = subprocess.run(self.remote.base(ip) + ["gunzip | sudo docker load -q >/dev/null"],
                              stdin=gzip.stdout, capture_output=True, timeout=1800, check=False)
        gzip.stdout.close()
        if save.wait(60) != 0 or gzip.wait(60) != 0 or load.returncode != 0:
            raise AutoscalerError("copying the release image failed")
        layers = "{{json .RootFS.Layers}}"
        local = subprocess.run(["docker", "image", "inspect", "--format", layers, image],
                               capture_output=True, text=True, timeout=30, check=False).stdout.strip()
        remote = self.remote.run(ip, f"sudo docker image inspect --format '{layers}' {quoted}").stdout
        if json.loads(local) != json.loads(remote.decode()):
            raise AutoscalerError("the copied image does not match the release image")

    def abandon(self, server: dict, name: str, ip: str) -> None:
        """Undo a scale-up whose worker never started: nothing can be running on it."""
        try:
            self.cloud.delete_server(server)
        except CloudError as error:
            log.error("could not delete %s after a failed provision: %s", name, error)
            return
        self.state.close_spend(name, self.clock())
        self.state.data["workers"].pop(name, None)
        forget_host_key(self.s, ip)
        try:
            tunnel(self.s, name, None, start=False, runner=self.runner)
        except AutoscalerError as error:
            log.error("%s", error)


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="read everything, change nothing, send no alerts")
    parser.add_argument("--test-alert", action="store_true", help="send one test alert to the operator chat and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings(dict(os.environ), dry_run=args.dry_run)
    if args.test_alert:
        alerts = Alerts(settings.alert_url, settings.alert_secret, dry_run=False)
        filed = alerts.send("test", "The render autoscaler can reach the operator chat.")
        log.info("test alert %s", {True: "filed", False: "not filed", None: "not sent: no alert URL or secret"}[filed])
        return 0 if filed else 1
    if not settings.policy.enabled and not settings.policy.dry_run:
        log.info("AUTOSCALER_ENABLED is not true; nothing to do")
        return 0
    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("another run holds the lock; skipping")
            return 0
        if not settings.token_file:
            log.error("AUTOSCALER_HCLOUD_TOKEN_FILE is not set")
            return 2
        scaler = Autoscaler(settings, Cloud(read_token(settings.token_file)))
        try:
            scaler.run()
        except Exception as error:
            # Whatever the earlier steps saved stays saved; the next run starts from the cloud's state.
            log.exception("the run stopped on an unexpected error")
            scaler.alert("failure", "crashed", f"a run stopped on an unexpected error: {type(error).__name__}",
                         scaler.clock())
            scaler.state.save()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
