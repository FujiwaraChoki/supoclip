"""Reproduce nginx 502s on reused upstream connections the frontend is closing.

Run on Linux with nginx (1.29.7+, or any version with --keepalive) and node on
PATH, from the repository root:

    python3 deploy/serving/keepalive_race.py --preload frontend/server-request-timeout.cjs

It starts an unprivileged nginx with the web vhost's proxy settings on an
ephemeral loopback port, a userland TCP relay (like Docker's published port)
and a Node HTTP server started the way the frontend image starts Next
(`node --require PRELOAD`). Each round sends a burst of POSTs, waits about as
long as the server keeps an idle connection, then sends the next burst on the
connections nginx kept, while the server's event loop is busy part of the
time as it is when rendering pages. It prints how many POSTs got a 502 and how many
"upstream prematurely closed connection" errors nginx logged. Pass
`--preload ''` for Node's defaults (5 s keep-alive).
"""
import argparse
import http.client
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SERVER = r"""
const http = require("node:http");
const net = require("node:net");
const [port, relayPort, busyMs] = process.argv.slice(1).map(Number);
const server = http.createServer((req, res) => {
  req.resume();
  req.on("end", () => res.end("ok"));
});
server.listen(port, "127.0.0.1");
// Docker's userland proxy: a second TCP hop that relays each half-close.
net.createServer((client) => {
  const upstream = net.connect(port, "127.0.0.1");
  client.pipe(upstream).pipe(client);
  client.on("error", () => upstream.destroy());
  upstream.on("error", () => client.destroy());
}).listen(relayPort, "127.0.0.1");
// Server rendering blocks the event loop. Node runs due timers before it reads
// sockets, so a request that arrived meanwhile loses to the idle timer.
if (busyMs > 0) {
  setInterval(() => { const end = Date.now() + busyMs; while (Date.now() < end); }, 4 * busyMs);
}
console.log(`keepAliveTimeout=${server.keepAliveTimeout}`);
"""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_listening(port):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nothing listening on {port}")


def post(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", "/ingest/e", body=b"{}", headers={"Content-Type": "application/json"})
        return conn.getresponse().status
    except OSError:
        return 0
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preload", default="frontend/server-request-timeout.cjs")
    parser.add_argument("--rounds", type=int, default=40)
    parser.add_argument("--burst", type=int, default=16)
    parser.add_argument("--gap-min", type=float, default=5.95, help="idle seconds between bursts")
    parser.add_argument("--gap-max", type=float, default=6.10)
    parser.add_argument("--busy-ms", type=int, default=20,
                        help="block the server's event loop this long, 1/4 of the time (0 = idle)")
    parser.add_argument("--keepalive", action="store_true",
                        help="add `keepalive 32;` to the upstream (nginx before 1.29.7)")
    args = parser.parse_args()
    nginx_bin = shutil.which("nginx") or "/usr/sbin/nginx"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        web, relay, front = free_port(), free_port(), free_port()
        node_args = ["node"]
        if args.preload:
            node_args += ["--require", str(Path(args.preload).resolve())]
        server = subprocess.Popen([*node_args, "-e", SERVER, str(web), str(relay), str(args.busy_ms)],
                                  stdout=subprocess.PIPE, text=True)
        nginx = None
        try:
            print(server.stdout.readline().strip(), flush=True)
            wait_listening(relay)
            keepalive = "keepalive 32;" if args.keepalive else ""
            # The web vhost's `location /`, minus TLS and HTTP/2.
            (root / "nginx.conf").write_text(f"""
                pid {root}/nginx.pid; error_log {root}/error.log warn; worker_processes 1;
                events {{ worker_connections 1024; }}
                http {{
                    access_log off;
                    client_body_temp_path {root}/body; proxy_temp_path {root}/proxy;
                    fastcgi_temp_path {root}/fastcgi; uwsgi_temp_path {root}/uwsgi;
                    scgi_temp_path {root}/scgi;
                    upstream supoclip_web {{ server 127.0.0.1:{relay}; {keepalive} }}
                    server {{ listen 127.0.0.1:{front}; location / {{
                        proxy_pass http://supoclip_web;
                        proxy_http_version 1.1;
                        proxy_set_header Upgrade $http_upgrade;
                        proxy_set_header Connection "upgrade";
                        proxy_set_header Host $host;
                        proxy_buffering off;
                        proxy_cache off;
                    }} }}
                }}""")
            nginx = subprocess.Popen([nginx_bin, "-e", str(root / "error.log"), "-c", str(root / "nginx.conf"),
                                      "-g", "daemon off;"], stderr=subprocess.DEVNULL)
            wait_listening(front)
            statuses = {}
            with ThreadPoolExecutor(args.burst) as pool:
                for _ in range(args.rounds):
                    for status in pool.map(lambda _: post(front), range(args.burst)):
                        statuses[status] = statuses.get(status, 0) + 1
                    time.sleep(random.uniform(args.gap_min, args.gap_max))
            log = (root / "error.log").read_text()
            premature = log.count("upstream prematurely closed connection")
            sent = args.rounds * args.burst
            print(f"POSTs: {sent}  statuses: {dict(sorted(statuses.items()))}  "
                  f"502s: {statuses.get(502, 0)}  nginx 'prematurely closed': {premature}", flush=True)
            return 1 if statuses.get(502, 0) or premature else 0
        finally:
            if nginx:
                nginx.terminate()
                nginx.wait()
            server.terminate()
            server.wait()


if __name__ == "__main__":
    sys.exit(main())
