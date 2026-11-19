"""Run with Python 3.11+: python3 deploy/autoscaler/test_autoscaler.py

All values are synthetic. Nothing here talks to a cloud API, a database or Redis.
"""
import fcntl
import hashlib
import hmac
import io
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import autoscaler
import decide
import probe
from decide import ACTIVE, DRAINING, PROVISIONING, Config, Metrics, Worker

NOW = 1_800_000_000.0
IMAGE = "supoclip-release:abc123-backend"
LABEL = decide.image_label(IMAGE)
ON = Config(enabled=True)


def metrics(queued=0, oldest=0.0, db=40, db_max=200, running=None, startable=None):
    # By default every queued task's account has a free slot.
    startable = queued if startable is None else startable
    return Metrics(queued_tasks=queued, oldest_queued_seconds=oldest, queue_depth=queued,
                   db_connections=db, db_max_connections=db_max, running_by_host=running or {},
                   startable_tasks=startable, oldest_startable_seconds=oldest if startable else 0.0)


def worker(name="supoclip-render-auto-1", state=ACTIVE, image=LABEL, running=0, **kw):
    return Worker(name=name, state=state, image=image, created_at=NOW - 7200, running_jobs=running, **kw)


def run(config=ON, m=None, workers=(), image=IMAGE, spend=0.0, **kw):
    return decide.decide(config, NOW, metrics() if m is None else m, list(workers),
                         current_image=image, spend=spend, **kw)


class ScaleUp(unittest.TestCase):
    def test_disabled_does_nothing_at_all(self):
        plan = run(Config(enabled=False), metrics(queued=99, oldest=99999), [worker(running=0, idle_since=0)])
        self.assertEqual(plan.actions, [])

    def test_oldest_wait_over_the_threshold_adds_one_worker(self):
        plan = run(m=metrics(queued=3, oldest=601))
        self.assertEqual(plan.kinds(), ["scale_up"])
        self.assertEqual(plan.actions[0].name, "supoclip-render-auto-1")

    def test_short_waits_and_a_small_queue_do_not(self):
        self.assertEqual(run(m=metrics(queued=3, oldest=599)).actions, [])

    def test_a_deep_queue_adds_a_worker_even_if_nothing_waited_long(self):
        self.assertEqual(run(m=metrics(queued=20, oldest=60)).kinds(), ["scale_up"])

    def test_tasks_held_by_the_per_account_limit_do_not_add_workers(self):
        # One account has 30 tasks queued but already uses all of its slots.
        self.assertEqual(run(m=metrics(queued=30, oldest=3600, startable=0)).actions, [])

    def test_the_next_free_name_is_used(self):
        plan = run(m=metrics(queued=30, oldest=900), workers=[worker(running=3)])
        self.assertEqual(plan.actions[-1].name, "supoclip-render-auto-2")

    def test_max_extra_workers_blocks_and_alerts(self):
        workers = [worker(running=5), worker("supoclip-render-auto-2", running=5)]
        plan = run(m=metrics(queued=30, oldest=900), workers=workers)
        self.assertEqual(plan.kinds(), ["alert"])
        self.assertEqual(plan.actions[0].key, "max-workers")

    def test_not_enough_database_connections_blocks_and_alerts(self):
        # 200 max - 160 used - 20 reserve = 20 free < 25 needed.
        plan = run(m=metrics(queued=30, oldest=900, db=160))
        self.assertEqual([a.key for a in plan.actions], ["db-budget"])
        self.assertEqual(run(m=metrics(queued=30, oldest=900, db=155)).kinds(), ["scale_up"])

    def test_spend_cap_blocks_a_new_worker(self):
        config = replace(ON, daily_spend_cap_eur=10.0, hourly_price_eur=0.8)
        plan = run(config, metrics(queued=30, oldest=900), spend=9.5)
        self.assertEqual([a.key for a in plan.actions], ["spend-cap"])
        self.assertEqual(run(config, metrics(queued=30, oldest=900), spend=9.0).kinds(), ["scale_up"])

    def test_one_at_a_time_cooldown_and_backoff_block_quietly(self):
        busy = metrics(queued=30, oldest=900)
        provisioning = replace(worker(state=PROVISIONING), created_at=NOW - 60)
        self.assertEqual(run(m=busy, workers=[provisioning]).actions, [])
        self.assertEqual(run(m=busy, last_scale_up=NOW - 300).actions, [])
        self.assertEqual(run(m=busy, backoff_until=NOW + 60).actions, [])
        self.assertEqual(run(m=busy, last_scale_up=NOW - 601).kinds(), ["scale_up"])

    def test_unknown_release_image_blocks(self):
        plan = run(m=metrics(queued=30, oldest=900), image=None)
        self.assertEqual([a.key for a in plan.actions], ["no-image"])

    def test_no_metrics_means_no_scale_up_and_no_idle_drain(self):
        plan = decide.decide(ON, NOW, None, [worker(running=0, idle_since=NOW - 7200)],
                             current_image=IMAGE, spend=0)
        self.assertEqual(plan.actions, [])
        self.assertEqual(plan.idle_since, {"supoclip-render-auto-1": None})


class ScaleDown(unittest.TestCase):
    def test_idle_timer_starts_then_drains_after_the_idle_period(self):
        plan = run(workers=[worker(running=0)])
        self.assertEqual(plan.idle_since, {"supoclip-render-auto-1": NOW})
        self.assertEqual(plan.actions, [])
        plan = run(workers=[worker(running=0, idle_since=NOW - 1799)])
        self.assertEqual(plan.actions, [])
        plan = run(workers=[worker(running=0, idle_since=NOW - 1800)])
        self.assertEqual(plan.kinds(), ["drain"])

    def test_a_running_job_or_a_queued_task_resets_the_timer(self):
        self.assertEqual(run(workers=[worker(running=1, idle_since=NOW - 9999)]).idle_since,
                         {"supoclip-render-auto-1": None})
        plan = run(m=metrics(queued=1, oldest=30), workers=[worker(running=0, idle_since=NOW - 9999)])
        self.assertEqual(plan.idle_since, {"supoclip-render-auto-1": None})
        self.assertEqual(plan.actions, [])

    def test_a_queue_no_worker_could_start_does_not_keep_a_worker_busy(self):
        plan = run(m=metrics(queued=5, oldest=3600, startable=0), workers=[worker(running=0, idle_since=NOW - 1800)])
        self.assertEqual(plan.kinds(), ["drain"])

    def test_unknown_running_jobs_never_count_as_idle(self):
        plan = run(workers=[worker(running=None, idle_since=NOW - 9999)])
        self.assertEqual(plan.actions, [])

    def test_a_new_release_drains_workers_on_the_old_one(self):
        plan = run(workers=[worker(image="supoclip-release_old-backend", running=4)])
        self.assertEqual(plan.kinds(), ["drain"])
        self.assertIn("release changed", plan.actions[0].reason)

    def test_hitting_the_spend_cap_drains_everything_and_alerts(self):
        plan = run(replace(ON, daily_spend_cap_eur=10), workers=[worker(running=3)], spend=10.0)
        self.assertEqual(sorted(plan.kinds()), ["alert", "drain"])

    def test_lowering_the_maximum_drains_the_least_busy_first(self):
        workers = [worker("supoclip-render-auto-1", running=5), worker("supoclip-render-auto-2", running=1)]
        plan = run(replace(ON, max_extra_workers=1), workers=workers)
        self.assertEqual([(a.kind, a.name) for a in plan.actions], [("drain", "supoclip-render-auto-2")])

    def test_a_drained_machine_is_deleted_only_once_its_worker_has_exited(self):
        self.assertEqual(run(workers=[worker(state=DRAINING, container_running=False)]).kinds(), ["delete"])
        self.assertEqual(run(workers=[worker(state=DRAINING, container_running=True, drain_started=NOW)]).actions, [])
        self.assertEqual(run(workers=[worker(state=DRAINING, container_running=None, drain_started=NOW)]).actions, [])

    def test_a_slow_drain_is_reported_not_forced(self):
        plan = run(workers=[worker(state=DRAINING, container_running=True, drain_started=NOW - 5 * 3600)])
        self.assertEqual(plan.kinds(), ["alert"])

    def test_a_provisioning_run_that_died_is_cleaned_up(self):
        stuck = replace(worker(state=PROVISIONING), created_at=NOW - 3600)
        self.assertEqual(run(workers=[replace(stuck, container_running=False)]).kinds(), ["delete", "alert"])
        self.assertEqual(run(workers=[replace(stuck, container_running=True)]).kinds(), ["activate"])
        # Unreachable: whether a worker runs there is unknown, so it is reported and left alone.
        plan = run(workers=[replace(stuck, container_running=None)])
        self.assertEqual(plan.kinds(), ["alert"])
        self.assertEqual(plan.actions[0].key, f"provision-unreachable:{stuck.name}")


class Helpers(unittest.TestCase):
    def test_spend_counts_started_hours_of_today_only(self):
        day = NOW - 10 * 3600
        records = [
            {"start": day - 3600, "end": day + 1800, "hourly_eur": 1.0},  # 0.5 h today -> 1 h
            {"start": day + 3600, "end": None, "hourly_eur": 0.5},  # 9 h so far
            {"start": day - 7200, "end": day - 3600, "hourly_eur": 9.0},  # yesterday
        ]
        self.assertEqual(decide.spend_today(records, day, NOW), 1.0 + 4.5)

    def test_image_label_is_a_valid_label_value(self):
        self.assertEqual(decide.image_label("registry.example/supoclip:abc-backend"),
                         "registry.example_supoclip_abc-backend")
        self.assertLessEqual(len(decide.image_label("x" * 200)), 63)

    def test_token_from_a_bare_file_or_a_cli_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "token"
            bare.write_text("synthetic-token\n")
            self.assertEqual(autoscaler.read_token(str(bare)), "synthetic-token")
            cli = Path(tmp) / "cli.toml"
            cli.write_text('active_context = "b"\n[[contexts]]\nname = "a"\ntoken = "ta"\n'
                           '[[contexts]]\nname = "b"\ntoken = "tb"\n')
            self.assertEqual(autoscaler.read_token(str(cli)), "tb")

    def test_compose_env_overrides_template_values(self):
        settings = autoscaler.load_settings({"AUTOSCALER_WORKER_CONCURRENCY": "6"})
        out = autoscaler.compose_env(settings, "w-1", IMAGE,
                                     "# comment\nWORKER_DATABASE_URL=postgresql://example\nWORKER_IMAGE=old\n").decode()
        self.assertIn("WORKER_DATABASE_URL=postgresql://example\n", out)
        self.assertIn(f"WORKER_IMAGE={IMAGE}\n", out)
        self.assertNotIn("WORKER_IMAGE=old", out)
        self.assertIn("VIDEO_WORKER_CONCURRENCY=6\n", out)
        # Cloud workers are overflow: they poll the queue less often than an always-on host.
        self.assertIn("VIDEO_WORKER_POLL_DELAY=5.0\n", out)
        self.assertIn("WORKER_DB_POOL_SIZE=6\n", out)
        self.assertIn("RENDER_HOST_NAME=w-1\n", out)

    def test_defaults(self):
        settings = autoscaler.load_settings({})
        self.assertFalse(settings.policy.enabled)
        self.assertEqual(settings.policy.max_extra_workers, 2)
        self.assertEqual(settings.server_type, "ccx53")
        self.assertEqual(settings.policy.idle_seconds, 1800)
        self.assertEqual(settings.policy.scale_up_wait_seconds, 600)

    def test_image_follows_the_reference_container_or_the_deploy_file(self):
        def runner(cmd, **kw):
            self.assertEqual(cmd[:2], ["docker", "inspect"])
            return subprocess.CompletedProcess(cmd, 0, stdout=IMAGE + "\n")
        self.assertEqual(autoscaler.resolve_image("container:backend", runner=runner), IMAGE)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image"
            path.write_text("supoclip-release:def456-backend\n")
            present = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="sha256:synthetic\n")
            absent = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="")
            self.assertEqual(autoscaler.resolve_image(f"file:{path}", runner=present),
                             "supoclip-release:def456-backend")
            self.assertIsNone(autoscaler.resolve_image(f"file:{path}.missing", runner=present))
            # A file naming an image this host does not have is no image at all:
            # it must not make every worker look like it runs an old release.
            self.assertIsNone(autoscaler.resolve_image(f"file:{path}", runner=absent))


class Probe(unittest.TestCase):
    """Which queued tasks another worker could start (probe.summarize)."""

    def task(self, task, user="user-a", age=900.0):
        return {"task": task, "user": user, "age": age}

    def job(self, waited=900.0, running=False, held=False):
        return {"waited": waited, "running": running, "held": held}

    def test_a_waiting_job_of_an_account_with_a_free_slot_is_startable(self):
        out = probe.summarize([self.task("t1")], {}, {"t1": self.job(waited=700)}, 2)
        self.assertEqual((out["queued_tasks"], out["startable_tasks"], out["oldest_startable_seconds"]), (1, 1, 700))

    def test_a_queued_task_whose_job_is_gone_is_not_startable(self):
        # No job id at all, and a job id that is no longer on the queue.
        queued = [self.task("orphan-1", age=90000), self.task("orphan-2", user="user-b", age=80000)]
        out = probe.summarize(queued, {}, {"orphan-2": self.job(waited=None)}, 2)
        self.assertEqual(out["queued_tasks"], 2)
        self.assertEqual(out["oldest_queued_seconds"], 90000)
        self.assertEqual((out["startable_tasks"], out["oldest_startable_seconds"]), (0, 0.0))

    def test_running_cancelled_and_upload_waiting_jobs_are_not_startable(self):
        queued = [self.task("picked"), self.task("held", user="user-b")]
        jobs = {"picked": self.job(running=True), "held": self.job(held=True)}
        self.assertEqual(probe.summarize(queued, {}, jobs, 2)["startable_tasks"], 0)

    def test_an_account_only_counts_up_to_its_free_slots_oldest_first(self):
        queued = [self.task(f"t{i}") for i in range(5)]
        jobs = {f"t{i}": self.job(waited=1000 - i * 100) for i in range(5)}
        out = probe.summarize(queued, {"user-a": 1}, jobs, 2)
        self.assertEqual((out["startable_tasks"], out["oldest_startable_seconds"]), (1, 1000))
        self.assertEqual(probe.summarize(queued, {"user-a": 2}, jobs, 2)["startable_tasks"], 0)

    def test_the_wait_is_the_jobs_not_the_tasks(self):
        # A task created days ago and resumed a minute ago; a deferred job is not due yet.
        queued = [self.task("resumed", age=400000), self.task("deferred", user="user-b", age=50)]
        jobs = {"resumed": self.job(waited=60), "deferred": self.job(waited=-45)}
        out = probe.summarize(queued, {}, jobs, 2)
        self.assertEqual((out["startable_tasks"], out["oldest_startable_seconds"]), (2, 60))


# --------------------------------------------------------------------------- I/O with fakes


def server(name, state=ACTIVE, ip="192.0.2.10", managed=True, image=LABEL, sid=1):
    labels = {"state": state, "image": image}
    if managed:
        labels[autoscaler.MANAGED_LABEL] = autoscaler.MANAGED_VALUE
    return {"id": sid, "name": name, "labels": labels, "created": "2027-01-15T06:00:00+00:00",
            "public_net": {"ipv4": {"ip": ip}},
            "server_type": {"prices": [{"location": "hel1", "price_hourly": {"gross": "0.7847"}}]}}


class FakeCloud:
    def __init__(self, servers=(), create_error=None):
        self.servers = list(servers)
        self.calls = []
        self.create_error = create_error

    def managed_servers(self):
        return [s for s in self.servers if s["labels"].get(autoscaler.MANAGED_LABEL) == autoscaler.MANAGED_VALUE]

    def newest_snapshot(self, selector):
        return {"id": 42, "created": "2027-01-01T00:00:00+00:00"}

    def find_id(self, kind, name):
        return 7

    def create_server(self, body):
        self.calls.append(("create", body["name"]))
        if self.create_error:
            raise self.create_error
        created = server(body["name"], state=PROVISIONING, ip="192.0.2.20", sid=99)
        created["labels"] = dict(body["labels"])
        self.servers.append(created)
        self.last_body = body
        return created

    def set_labels(self, srv, **labels):
        self.calls.append(("labels", srv["name"], labels))
        srv["labels"] = {**srv["labels"], **labels}

    def delete_server(self, srv):
        self.calls.append(("delete", srv["name"]))
        self.servers.remove(srv)


class FakeHost:
    """Plays the local docker/systemctl commands and the remote ssh commands."""

    def __init__(self, metrics_out, container_running=None, fail_on=None):
        self.metrics_out = metrics_out
        self.running = container_running or {}
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, cmd, input=None, **kw):
        self.calls.append((list(cmd), input))
        text = kw.get("text")

        def out(rc=0, stdout=b"", stderr=b""):
            if text:
                stdout, stderr = stdout.decode(), stderr.decode()
            return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)

        if cmd[0] == "ssh":
            ip = cmd[-2].split("@")[1]
            remote = cmd[-1]
            if self.fail_on and self.fail_on in remote:
                return out(1, stderr=b"synthetic failure")
            if "docker ps -q" in remote:
                return out(stdout=b"c0ffee\n" if self.running.get(ip) else b"")
            return out(stdout=b"ok\n")
        if cmd[:2] == ["docker", "exec"]:
            return out(stdout=json.dumps(self.metrics_out).encode())
        if cmd[:2] == ["docker", "inspect"]:
            return out(stdout=(IMAGE + "\n").encode())
        if cmd[0] == "sudo":
            return out()
        raise AssertionError(f"unexpected command {cmd}")

    def remote_commands(self):
        return [cmd[-1] for cmd, _ in self.calls if cmd[0] == "ssh"]


class RecordingAlerts:
    def __init__(self, delivered=True):
        self.sent = []
        self.delivered = delivered

    def send(self, event, message, title=None):
        self.sent.append((event, message))
        return self.delivered


class Scaler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for name, body in [("worker.env", "SECRET_SETTING=synthetic-worker-secret\n"),
                           ("compose.env", "WORKER_DATABASE_URL=postgresql://u:synthetic-db-secret@127.0.0.1:15432/db\n"),
                           ("key", "")]:
            (self.dir / name).write_text(body)
        env = {
            "AUTOSCALER_ENABLED": "true", "AUTOSCALER_STATE_DIR": str(self.dir / "state"),
            "AUTOSCALER_WORKER_ENV_FILE": str(self.dir / "worker.env"),
            "AUTOSCALER_COMPOSE_ENV_FILE": str(self.dir / "compose.env"),
            "AUTOSCALER_SSH_KEY": str(self.dir / "key"),
        }
        self.settings = autoscaler.load_settings(env)
        self.clock = [NOW]

    def tearDown(self):
        self.tmp.cleanup()

    def scaler(self, cloud, host, settings=None):
        settings = settings or self.settings
        self.alerts = RecordingAlerts()
        return autoscaler.Autoscaler(
            settings, cloud, remote=autoscaler.Remote(settings, runner=host), alerts=self.alerts,
            runner=host, clock=lambda: self.clock[0], sleep=lambda s: self.clock.__setitem__(0, self.clock[0] + s),
        )

    def busy(self, **running):
        return {"queued_tasks": 30, "oldest_queued_seconds": 1500, "queue_depth": 42,
                "startable_tasks": 30, "oldest_startable_seconds": 1500,
                "db_connections": 50, "db_max_connections": 200, "running_by_host": running}

    def test_scale_up_creates_provisions_and_activates_without_secrets_on_any_command_line(self):
        cloud = FakeCloud()
        host = FakeHost(self.busy(), container_running={"192.0.2.20": True})
        plan = self.scaler(cloud, host).run()

        self.assertEqual(plan.kinds(), ["scale_up"])
        name = "supoclip-render-auto-1"
        self.assertEqual(cloud.calls[0], ("create", name))
        self.assertEqual(cloud.calls[-1], ("labels", name, {"state": ACTIVE}))
        self.assertEqual(cloud.last_body["labels"]["state"], PROVISIONING)
        self.assertEqual(cloud.last_body["labels"][autoscaler.MANAGED_LABEL], autoscaler.MANAGED_VALUE)
        self.assertIn("ed25519_private", cloud.last_body["user_data"])
        known = (self.dir / "state" / "known_hosts").read_text()
        self.assertTrue(known.startswith("192.0.2.20 ssh-ed25519 "))

        remote = host.remote_commands()
        validate = next(i for i, c in enumerate(remote) if " run --rm --no-deps" in c)
        up = next(i for i, c in enumerate(remote) if " up -d " in c)
        self.assertLess(validate, up, "connectivity must be checked before the worker starts")
        for cmd, stdin in host.calls:
            joined = " ".join(cmd)
            self.assertNotIn("synthetic-db-secret", joined)
            self.assertNotIn("synthetic-worker-secret", joined)
        stdins = b"".join(stdin or b"" for _, stdin in host.calls)
        self.assertIn(b"synthetic-worker-secret", stdins)
        self.assertIn(b"synthetic-db-secret", stdins)
        self.assertIn(["sudo", "-n", "systemctl", "restart", f"supoclip-render-tunnel@{name}.service"],
                      [cmd for cmd, _ in host.calls])
        self.assertEqual([e for e, _ in self.alerts.sent], ["scale_up", "scale_up"])

    def test_a_failed_connectivity_check_deletes_the_new_server_and_never_starts_a_worker(self):
        cloud = FakeCloud()
        host = FakeHost(self.busy(), fail_on="run --rm --no-deps")
        self.scaler(cloud, host).run()

        self.assertIn(("delete", "supoclip-render-auto-1"), cloud.calls)
        self.assertFalse(any(" up -d " in c for c in host.remote_commands()))
        self.assertEqual(self.alerts.sent[-1][0], "failure")

    def test_a_failed_provision_backs_off_instead_of_creating_a_server_every_cooldown(self):
        cloud = FakeCloud()
        host = FakeHost(self.busy(), fail_on="run --rm --no-deps")
        self.scaler(cloud, host).run()
        self.assertIn("scale-ups paused", self.alerts.sent[-1][1])

        # Past the cooldown, still inside the back-off: each attempt would bill a started hour.
        self.clock[0] += 900
        plan = self.scaler(cloud, host).run()
        self.assertEqual(plan.kinds(), [])
        self.assertEqual(sum(1 for c in cloud.calls if c[0] == "create"), 1)

        self.clock[0] += 1800
        self.scaler(cloud, host).run()
        self.assertEqual(sum(1 for c in cloud.calls if c[0] == "create"), 2)

    def test_an_alert_the_gateway_did_not_file_is_sent_again_on_the_next_run(self):
        cloud = FakeCloud([server("supoclip-render-auto-1"), server("supoclip-render-auto-2", sid=2)])
        host = FakeHost(self.busy(**{"supoclip-render-auto-1": 5, "supoclip-render-auto-2": 5}))
        scaler = self.scaler(cloud, host)
        scaler.alerts = undelivered = RecordingAlerts(delivered=False)
        scaler.run()
        self.assertEqual([e for e, _ in undelivered.sent], ["blocked"])

        self.clock[0] += 120
        self.scaler(cloud, host).run()
        self.assertEqual([e for e, _ in self.alerts.sent], ["blocked"])
        self.clock[0] += 120
        self.scaler(cloud, host).run()
        self.assertEqual(self.alerts.sent, [], "a filed alert is not repeated within the hour")

    def test_a_machine_counts_as_running_while_any_container_is_up(self):
        host = FakeHost(self.busy())
        autoscaler.Remote(self.settings, runner=host).container_running("192.0.2.30")
        # No label filter: a worker started outside compose must not look like it has exited.
        self.assertEqual(host.remote_commands(), ["sudo docker ps -q"])

    def test_a_worker_that_fails_after_starting_is_drained_not_deleted(self):
        cloud = FakeCloud()
        host = FakeHost(self.busy(), container_running={"192.0.2.20": False})
        self.scaler(cloud, host).run()

        self.assertNotIn(("delete", "supoclip-render-auto-1"), cloud.calls)
        self.assertIn(("labels", "supoclip-render-auto-1", {"state": DRAINING, "drain-started": str(int(NOW + 20))}),
                      cloud.calls)

    def test_a_cloud_limit_error_alerts_once_and_backs_off(self):
        error = autoscaler.CloudError(403, "resource_limit_exceeded", "dedicated core limit exceeded")
        cloud = FakeCloud(create_error=error)
        host = FakeHost(self.busy())
        self.scaler(cloud, host).run()
        self.assertEqual(self.alerts.sent[-1][0], "failure")
        self.assertIn("dedicated core limit", self.alerts.sent[-1][1])

        self.clock[0] += 120
        scaler = self.scaler(cloud, host)
        plan = scaler.run()
        self.assertEqual(plan.kinds(), [])
        self.assertEqual(self.alerts.sent, [])
        self.assertEqual(sum(1 for c in cloud.calls if c[0] == "create"), 1)

    def test_drain_sends_sigterm_and_never_stops_or_removes_the_container(self):
        srv = server("supoclip-render-auto-1")
        cloud = FakeCloud([srv])
        idle = {"queued_tasks": 0, "oldest_queued_seconds": 0, "queue_depth": 0,
                "startable_tasks": 0, "oldest_startable_seconds": 0,
                "db_connections": 50, "db_max_connections": 200, "running_by_host": {srv["name"]: 0}}
        host = FakeHost(idle)
        state = autoscaler.State(self.dir / "state" / "state.json")
        state.worker(srv["name"])["idle_since"] = NOW - 1900
        state.save()
        self.scaler(cloud, host).run()

        drain = [c for c in host.remote_commands() if "kill" in c]
        self.assertEqual(len(drain), 1)
        self.assertIn("docker update --restart=no", drain[0])
        self.assertIn("docker kill -s TERM", drain[0])
        for command in host.remote_commands():
            self.assertNotIn("docker stop", command)
            self.assertNotIn("docker rm", command)
            self.assertNotIn("compose down", command)
        self.assertEqual(srv["labels"]["state"], DRAINING)
        self.assertEqual(cloud.calls[-1][0], "labels")

    def test_a_draining_server_is_deleted_after_its_worker_exits_and_its_tunnel_stopped(self):
        srv = server("supoclip-render-auto-1", state=DRAINING, ip="192.0.2.30")
        cloud = FakeCloud([srv])
        host = FakeHost(self.busy(), container_running={"192.0.2.30": False})
        self.scaler(cloud, replace_metrics(host, queued=0)).run()
        self.assertIn(("delete", srv["name"]), cloud.calls)
        self.assertIn(["sudo", "-n", "systemctl", "stop", f"supoclip-render-tunnel@{srv['name']}.service"],
                      [cmd for cmd, _ in host.calls])

    def test_a_server_with_a_running_worker_is_never_deleted(self):
        srv = server("supoclip-render-auto-1", state=DRAINING, ip="192.0.2.30")
        host = FakeHost(self.busy(), container_running={"192.0.2.30": True})
        scaler = self.scaler(FakeCloud([srv]), host)
        with self.assertRaises(autoscaler.AutoscalerError):
            scaler.delete(srv, "test", NOW)

    def test_unmanaged_servers_are_invisible_and_cannot_be_deleted(self):
        other = server("hand-built-worker", managed=False)
        cloud = FakeCloud([other])
        host = FakeHost({**self.busy(), "startable_tasks": 0, "oldest_startable_seconds": 0})
        self.scaler(cloud, host).run()
        self.assertEqual(cloud.calls, [])
        with self.assertRaises(autoscaler.AutoscalerError):
            autoscaler.Cloud("synthetic-token", opener=None).delete_server(other)

    def test_dry_run_changes_nothing_and_sends_no_alerts(self):
        srv = server("supoclip-render-auto-1", image="supoclip-release_old")
        cloud = FakeCloud([srv])
        host = FakeHost(self.busy())
        settings = replace(self.settings, policy=replace(self.settings.policy, enabled=False, dry_run=True))
        scaler = self.scaler(cloud, host, settings)
        real = autoscaler.Alerts("https://alerts.example/hook", "synthetic", dry_run=True,
                                 opener=FailingOpener())
        scaler.alerts = real
        plan = scaler.run()

        # Would drain the worker on the old release and add one on the current release.
        self.assertEqual(plan.kinds(), ["drain", "scale_up"])
        self.assertEqual(cloud.calls, [])
        self.assertFalse(any("kill" in c for c in host.remote_commands()))
        self.assertTrue((self.dir / "state" / "state.dry-run.json").exists())
        self.assertFalse((self.dir / "state" / "state.json").exists())

    def test_disabled_reads_nothing(self):
        cloud = FakeCloud([server("supoclip-render-auto-1")])
        host = FakeHost(self.busy())
        settings = replace(self.settings, policy=replace(self.settings.policy, enabled=False))
        self.scaler(cloud, host, settings).run()
        self.assertEqual(host.calls, [])


def replace_metrics(host, **changes):
    host.metrics_out = {**host.metrics_out, "queued_tasks": changes.get("queued", 0),
                        "startable_tasks": changes.get("queued", 0), "oldest_startable_seconds": 0}
    return host


class FailingOpener:
    def open(self, *a, **kw):
        raise AssertionError("no request may be sent")


class Network(unittest.TestCase):
    def alerts(self, answers):
        sent, slept = [], []

        class Opener:
            def open(self, request, timeout):
                sent.append(request)
                answer = answers[min(len(sent), len(answers)) - 1]
                if isinstance(answer, Exception):
                    raise answer
                return _Response(*answer)

        alerts = autoscaler.Alerts("https://alerts.example/hook", "synthetic-secret", dry_run=False,
                                   opener=Opener(), sleep=slept.append)
        return alerts, sent, slept

    def test_alerts_are_signed_ops_alerts(self):
        alerts, sent, _ = self.alerts([(200, b'{"status": "accepted"}')])
        self.assertIs(alerts.send("failure", "scale_up w-1 failed"), True)
        request = sent[0]
        timestamp = request.get_header("X-webhook-timestamp")
        expected = hmac.new(b"synthetic-secret", timestamp.encode() + b"." + request.data, hashlib.sha256).hexdigest()
        self.assertEqual(request.get_header("X-webhook-signature-v2"), expected)
        body = json.loads(request.data)
        self.assertEqual(sorted(body), ["created_at", "event_id", "level", "message", "source", "title",
                                        "type", "version"])
        self.assertEqual((body["type"], body["version"], body["source"], body["level"]),
                         ("supoclip.ops_alert", 1, "autoscaler", "error"))
        self.assertEqual(body["message"], "scale_up w-1 failed")
        self.assertEqual(len(body["event_id"]), 36)

    def test_an_ignored_or_failed_alert_is_retried_with_the_same_event_id(self):
        gone = HTTPError("https://alerts.example/hook", 502, "Bad Gateway", {}, io.BytesIO(b""))
        alerts, sent, slept = self.alerts([(200, b'{"status": "ignored"}'), gone, (202, b"")])
        self.assertIs(alerts.send("scale_up", "creating a worker"), True)
        self.assertEqual(len(sent), 3)
        self.assertEqual(len({json.loads(r.data)["event_id"] for r in sent}), 1)
        self.assertEqual(slept, [2, 6])

        alerts, sent, _ = self.alerts([(200, b'{"status": "ignored"}')])
        self.assertIs(alerts.send("scale_up", "creating a worker"), False)
        self.assertEqual(len(sent), 4)

    def test_no_alert_is_sent_without_a_route_or_in_a_dry_run(self):
        for alerts in (autoscaler.Alerts("", "", dry_run=False, opener=FailingOpener()),
                       autoscaler.Alerts("https://alerts.example/hook", "synthetic", dry_run=True,
                                         opener=FailingOpener())):
            self.assertIsNone(alerts.send("failure", "nothing leaves this host"))

    def test_cloud_errors_carry_the_api_code_and_never_the_token(self):
        class Opener:
            def open(self, request, timeout):
                body = json.dumps({"error": {"code": "resource_limit_exceeded",
                                             "message": "dedicated core limit exceeded"}}).encode()
                raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))

        cloud = autoscaler.Cloud("synthetic-token", opener=Opener())
        with self.assertRaises(autoscaler.CloudError) as caught:
            cloud.create_server({"name": "w"})
        self.assertEqual(caught.exception.code, "resource_limit_exceeded")
        self.assertNotIn("synthetic-token", str(caught.exception))

    def test_only_one_run_at_a_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOSCALER_ENABLED": "true", "AUTOSCALER_STATE_DIR": tmp}
            with open(Path(tmp) / "lock", "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                original = autoscaler.os.environ
                autoscaler.os.environ = env
                try:
                    with self.assertLogs("render-autoscaler", "INFO") as logs:
                        self.assertEqual(autoscaler.main([]), 0)
                finally:
                    autoscaler.os.environ = original
            self.assertIn("another run holds the lock", "\n".join(logs.output))


class _Response:
    def __init__(self, status=202, body=b""):
        self.status, self.body = status, body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    unittest.main()
