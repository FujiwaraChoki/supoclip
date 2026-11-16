#!/usr/bin/env python3
"""Switch a pair of nginx upstreams after readiness; keep both releases for rollback."""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

http = build_opener(ProxyHandler({}))


def get(port, path):
    with http.open(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
        if response.status != 200 or response.geturl() != f"http://127.0.0.1:{port}{path}":
            raise ValueError("unexpected response")
        return response.read(32768)


def check_frontend_auth(port):
    # The anonymous Better Auth response can succeed without touching Prisma.
    # Select the exact running container bound to the candidate loopback port.
    rows = subprocess.check_output(
        ["docker", "ps", "--format", "{{.ID}} {{.Ports}}"], text=True, timeout=5,
    )
    binding = f"127.0.0.1:{port}->3107/tcp"
    containers = [row.split(" ", 1)[0] for row in rows.splitlines()
                  if binding in row.partition(" ")[2].split(", ")]
    if len(containers) != 1:
        raise ValueError(f"cannot identify the frontend on port {port}")
    with Path(__file__).with_name("check-auth.cjs").open("rb") as probe:
        subprocess.run(["docker", "exec", "-i", containers[0], "node", "-"], stdin=probe,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=8)


def ready(web, api):
    for path in ("/health/db", "/health/redis"):
        if json.loads(get(api, path)).get("status") != "healthy":
            raise ValueError(f"{path} is unhealthy")
    if json.loads(get(web, "/api/auth/get-session")) is not None:
        raise ValueError("unexpected anonymous session response")


def wait_for(check, seconds, description):
    deadline = time.monotonic() + seconds
    while True:
        try:
            if check():
                return
        except (OSError, URLError, ValueError, subprocess.SubprocessError) as exc:
            last_error = type(exc).__name__
        else:
            last_error = "pending"
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{description}: {last_error}")
        time.sleep(0.2)


def config(web, api, monitor):
    return f"""upstream supoclip_web {{ server 127.0.0.1:{web}; }}
upstream supoclip_api {{ server 127.0.0.1:{api}; }}
server {{
    listen 127.0.0.1:{monitor};
    location = /serving {{ return 200 "{web}/{api}"; }}
}}
""".encode()


def replace(path, contents):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(contents)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, path)


def workers(master):
    # Linux /proc also identifies process reuse by start time. Include previous
    # generations still draining after a timeout or a later rollback.
    children = Path(f"/proc/{master}/task/{master}/children").read_text().split()
    result = {}
    for child in children:
        proc = Path("/proc") / child
        try:
            title = (proc / "cmdline").read_bytes()
            if b"nginx: worker process" in title:
                started = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
                result[(child, started)] = b"shutting down" in title
        except FileNotFoundError:
            pass
    return result


def main(frontend_auth=check_frontend_auth):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "switch"])
    parser.add_argument("web", type=int)
    parser.add_argument("api", type=int)
    parser.add_argument("--include", type=Path, default=Path("/etc/nginx/conf.d/supoclip-serving.conf"))
    parser.add_argument("--master-pid", type=Path, default=Path("/run/nginx.pid"))
    parser.add_argument("--nginx-conf", default="/etc/nginx/nginx.conf")
    parser.add_argument("--monitor", type=int, default=18091)
    parser.add_argument("--ready-timeout", type=int, default=120)
    parser.add_argument("--drain-timeout", type=int, default=3600)
    args = parser.parse_args()
    ports = {args.web, args.api, args.monitor}
    if len(ports) != 3 or any(not 0 < p < 65536 for p in ports):
        parser.error("three distinct TCP ports required")
    new = config(args.web, args.api, args.monitor)
    nginx = ["nginx", "-c", args.nginx_conf]
    with args.include.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "init":
            with args.include.open("xb") as output:
                output.write(new)
            return
        old = args.include.read_bytes()
        # Verify the installed include actually controls the running master.
        previous = get(args.monitor, "/serving").decode()
        if not re.fullmatch(r"[0-9]+/[0-9]+", previous):
            raise ValueError("unexpected serving marker")
        if old != config(*map(int, previous.split("/")), args.monitor):
            raise ValueError("running nginx and the on-disk upstreams disagree")
        effective = subprocess.check_output([*nginx, "-T"], stderr=subprocess.DEVNULL)
        if re.search(rb"^\s*worker_shutdown_timeout\s+", effective, re.MULTILINE):
            raise ValueError("nginx has a forced worker shutdown deadline")
        for upstream in (b"supoclip_web", b"supoclip_api"):
            if not re.search(rb"^\s*proxy_pass\s+http://" + upstream + rb"\s*;", effective, re.MULTILINE):
                raise ValueError("install both named upstreams in the serving vhosts first")
        def candidate_ready():
            ready(args.web, args.api)
            frontend_auth(args.web)
            return True
        wait_for(candidate_ready, args.ready_timeout, "candidate readiness")
        master = int(args.master_pid.read_text())
        prior = set(workers(master))
        if not prior:
            raise RuntimeError("cannot identify serving nginx workers")
        if new == old:
            # A repeated switch must still fence a previously timed out drain.
            # Reloading the same endpoints lets prior generations finish and
            # leaves a fresh generation for new work.
            print("Rechecking the drain on the current release", flush=True)
        replace(args.include, new)
        try:
            subprocess.run([*nginx, "-t"], check=True)
            subprocess.run([*nginx, "-s", "reload"], check=True)
            wait_for(lambda: get(args.monitor, "/serving").decode() == f"{args.web}/{args.api}"
                     and bool(set(workers(master)) - prior), 15, "nginx switch")
        except BaseException:
            replace(args.include, old)
            subprocess.run([*nginx, "-s", "reload"], check=True)
            wait_for(lambda: get(args.monitor, "/serving").decode() == previous, 15, "rollback")
            raise
        print("New connections switched. Waiting for old nginx workers to finish.", flush=True)
        def drained():
            current = workers(master)
            return bool(current) and prior.isdisjoint(current) and not any(current.values())

        wait_for(drained, args.drain_timeout,
                 "old nginx workers still serving; both releases retained")
        print("Old nginx connections drained. Both releases retained.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))
