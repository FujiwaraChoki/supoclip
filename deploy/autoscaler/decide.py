"""What the render autoscaler should do this run. Pure: no I/O, easy to test.

`autoscaler.py` gathers the facts (queue, database connections, cloud workers,
spend) and carries out the plan this module returns.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Server states, kept in the server's `state` label.
PROVISIONING = "provisioning"
ACTIVE = "active"
DRAINING = "draining"


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    dry_run: bool = False
    scale_up_wait_seconds: int = 600
    scale_up_queue_depth: int = 20
    scale_up_cooldown_seconds: int = 600
    idle_seconds: int = 1800
    max_extra_workers: int = 2
    daily_spend_cap_eur: float = 40.0
    hourly_price_eur: float = 0.7847
    db_connections_per_worker: int = 25
    db_connection_reserve: int = 20
    provision_timeout_seconds: int = 1800
    # job_completion_wait (3 h) plus a margin; past it a drain is reported, never forced.
    drain_timeout_seconds: int = 3 * 3600 + 900
    name_prefix: str = "supoclip-render-auto"


@dataclass(frozen=True)
class Metrics:
    queued_tasks: int
    oldest_queued_seconds: float
    queue_depth: int
    db_connections: int
    db_max_connections: int
    # Queued tasks whose account has a free render slot: the ones a new worker could start.
    startable_tasks: int = 0
    oldest_startable_seconds: float = 0.0
    running_by_host: dict[str, int] = field(default_factory=dict)


@dataclass
class Worker:
    name: str
    state: str
    image: str
    created_at: float
    # From the per-host job registry. None when unknown.
    running_jobs: int | None = None
    # From `docker ps` on the machine. None when it could not be reached.
    container_running: bool | None = None
    idle_since: float | None = None
    drain_started: float | None = None


@dataclass(frozen=True)
class Action:
    kind: str  # scale_up | drain | delete | activate | alert
    name: str = ""
    reason: str = ""
    # For alerts: a key that suppresses repeats (see Plan.alert_keys).
    key: str = ""


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    idle_since: dict[str, float | None] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def kinds(self) -> list[str]:
        return [action.kind for action in self.actions]

    def alert(self, key: str, reason: str) -> None:
        self.actions.append(Action("alert", reason=reason, key=key))


def image_label(image: str) -> str:
    """A release image as a valid label value (no ':' or '/', at most 63 characters)."""
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", image).strip("_.-")
    return value[-63:].strip("_.-")


def spend_today(records: list[dict], day_start: float, now: float) -> float:
    """EUR billed today, counting every started hour of each server's time today.

    Each record is {"start", "end" (None while running), "hourly_eur"}. Hours are
    rounded up per server, which overestimates slightly: the cap errs on the safe side.
    """
    total = 0.0
    for record in records:
        start = max(float(record["start"]), day_start)
        end = now if record.get("end") is None else min(float(record["end"]), now)
        if end <= start:
            continue
        hours = math.ceil((end - start) / 3600 - 1e-9)
        total += hours * float(record["hourly_eur"])
    return round(total, 4)


def next_name(prefix: str, taken: set[str], limit: int) -> str | None:
    for index in range(1, limit + 1):
        name = f"{prefix}-{index}"
        if name not in taken:
            return name
    return None


def decide(
    config: Config,
    now: float,
    metrics: Metrics | None,
    workers: list[Worker],
    *,
    current_image: str | None,
    spend: float,
    last_scale_up: float | None = None,
    backoff_until: float | None = None,
) -> Plan:
    plan = Plan()
    if not config.enabled:
        plan.notes.append("disabled (AUTOSCALER_ENABLED is not true); doing nothing")
        return plan

    active = [w for w in workers if w.state == ACTIVE]
    provisioning = [w for w in workers if w.state == PROVISIONING]
    draining = [w for w in workers if w.state == DRAINING]
    current_label = image_label(current_image) if current_image else None

    # Idle timers: a worker is idle when nothing runs on it and nothing it could start waits.
    for worker in active:
        idle = (
            metrics is not None
            and worker.running_jobs == 0
            and metrics.startable_tasks == 0
        )
        plan.idle_since[worker.name] = (worker.idle_since or now) if idle else None

    # Finish drains. Only a machine whose worker container has exited is deleted:
    # ARQ exits on SIGTERM only after its running jobs finish.
    for worker in draining:
        if worker.container_running is False:
            plan.actions.append(Action("delete", worker.name, "drained: the worker has exited"))
        elif worker.drain_started and now - worker.drain_started > config.drain_timeout_seconds:
            plan.alert(
                f"drain-slow:{worker.name}",
                f"{worker.name} has been draining for over "
                f"{int((now - worker.drain_started) // 60)} min; it is left running, not killed",
            )

    # A provisioning run that died part-way (the lock is ours, so nobody else is on it).
    for worker in provisioning:
        if now - worker.created_at <= config.provision_timeout_seconds:
            continue
        if worker.container_running:
            plan.actions.append(Action("activate", worker.name, "provisioning finished in an earlier run"))
        elif worker.container_running is False:
            plan.actions.append(Action(
                "delete", worker.name, "provisioning did not finish; no worker ever started on it",
            ))
            plan.alert(f"provision-stuck:{worker.name}", f"{worker.name} never finished provisioning; deleting it")
        else:
            # Whether a worker runs there is unknown, so it is not deleted. It keeps billing.
            plan.alert(
                f"provision-unreachable:{worker.name}",
                f"{worker.name} never finished provisioning and cannot be reached; "
                "it is left alone and still billed: check it and delete it by hand",
            )

    # Drains of active workers. Draining never stops a running job.
    drained: set[str] = set()

    def drain(worker: Worker, reason: str) -> None:
        if worker.name not in drained:
            drained.add(worker.name)
            plan.actions.append(Action("drain", worker.name, reason))

    if spend >= config.daily_spend_cap_eur and active:
        plan.alert(
            "spend-cap",
            f"daily spend cap reached (EUR {spend:.2f} of {config.daily_spend_cap_eur:.2f}); "
            "draining every cloud worker",
        )
        for worker in active:
            drain(worker, "daily spend cap reached")

    if current_label:
        for worker in active:
            if worker.image != current_label:
                drain(worker, f"release changed ({worker.image} -> {current_label})")

    for worker in active:
        since = plan.idle_since.get(worker.name)
        if since is not None and now - since >= config.idle_seconds:
            drain(worker, f"idle for {int((now - since) // 60)} min")

    remaining = [w for w in active if w.name not in drained]
    excess = len(remaining) + len(provisioning) - config.max_extra_workers
    if excess > 0:
        # Fewest running jobs first, then the newest.
        for worker in sorted(remaining, key=lambda w: (w.running_jobs or 0, -w.created_at))[:excess]:
            drain(worker, f"above AUTOSCALER_MAX_EXTRA_WORKERS={config.max_extra_workers}")
        remaining = [w for w in remaining if w.name not in drained]

    # Scale up: at most one new machine per run.
    if metrics is None:
        plan.notes.append("no queue metrics; not scaling up")
        return plan
    # Only tasks that could start if there were a free slot: more machines do not help
    # an account that already uses all of its per-account render slots.
    wait_hit = metrics.startable_tasks > 0 and metrics.oldest_startable_seconds >= config.scale_up_wait_seconds
    depth_hit = metrics.startable_tasks >= config.scale_up_queue_depth
    if not (wait_hit or depth_hit):
        return plan
    trigger = (
        f"oldest startable queued task waited {int(metrics.oldest_startable_seconds // 60)} min"
        if wait_hit else f"{metrics.startable_tasks} startable tasks queued"
    )

    def blocked(key: str, why: str, alert: bool = True) -> Plan:
        plan.notes.append(f"wanted to scale up ({trigger}) but {why}")
        if alert:
            plan.alert(key, f"wanted to scale up ({trigger}) but {why}")
        return plan

    if provisioning:
        return blocked("", "a worker is still being provisioned", alert=False)
    if last_scale_up is not None and now - last_scale_up < config.scale_up_cooldown_seconds:
        return blocked("", "the last scale-up is still in its cooldown", alert=False)
    if backoff_until is not None and now < backoff_until:
        return blocked("", "scale-ups are backed off after a failed scale-up or a cloud API error", alert=False)
    if len(remaining) >= config.max_extra_workers:
        return blocked("max-workers", f"AUTOSCALER_MAX_EXTRA_WORKERS={config.max_extra_workers} are running")
    if not current_image:
        return blocked("no-image", "the current release image could not be determined")
    projected = spend + config.hourly_price_eur * (len(remaining) + 1)
    if spend >= config.daily_spend_cap_eur or projected > config.daily_spend_cap_eur:
        return blocked(
            "spend-cap",
            f"another hour would exceed the daily spend cap "
            f"(EUR {projected:.2f} > {config.daily_spend_cap_eur:.2f})",
        )
    free = metrics.db_max_connections - metrics.db_connections - config.db_connection_reserve
    if free < config.db_connections_per_worker:
        return blocked(
            "db-budget",
            f"PostgreSQL has {metrics.db_connections}/{metrics.db_max_connections} connections in use; "
            f"a worker needs {config.db_connections_per_worker} with {config.db_connection_reserve} kept free",
        )
    taken = {w.name for w in workers}
    name = next_name(config.name_prefix, taken, max(config.max_extra_workers, 1) * 2 + len(draining))
    if name is None:
        return blocked("no-name", "no free worker name")
    plan.actions.append(Action("scale_up", name, trigger))
    return plan
