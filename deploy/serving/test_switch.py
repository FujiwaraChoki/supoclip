"""Run on Linux with nginx on PATH: python3 deploy/serving/test_switch.py."""
import hashlib
import http.client
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

SWITCH = Path(__file__).with_name("switch.py")
CHUNK = b"abc12345" * 8192


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class API(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def response(self, body):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/download":
            self.send_response(200)
            self.send_header("Content-Length", str(2 * len(CHUNK)))
            self.end_headers()
            self.wfile.write(CHUNK)
            self.server.download_started.set()
            self.server.release.wait(15)
            self.wfile.write(CHUNK)
        elif self.path.startswith("/health/"):
            self.response(json.dumps({"status": self.server.health}).encode())
        else:
            self.response(self.server.label)

    def do_POST(self):
        count = int(self.headers["Content-Length"])
        body = self.rfile.read(len(CHUNK))
        self.server.upload_started.set()
        body += self.rfile.read(count - len(body))
        self.response(self.server.label + hashlib.sha256(body).digest())


class Frontend(API):
    def forward(self):
        if self.path == "/api/auth/get-session":
            return self.response(b"null")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.api, timeout=15)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            conn.putrequest(self.command, self.path)
            conn.putheader("Content-Length", str(size))
            conn.endheaders()
            while size:
                block = self.rfile.read(min(size, len(CHUNK)))
                if not block:
                    raise ConnectionError("short upload")
                conn.send(block)
                size -= len(block)
            response = conn.getresponse()
            self.send_response(response.status)
            self.send_header("Content-Length", response.getheader("Content-Length"))
            self.end_headers()
            while block := response.read(len(CHUNK)):
                self.wfile.write(block)
        finally:
            conn.close()

    do_GET = do_POST = forward


@unittest.skipUnless(sys.platform == "linux" and shutil.which("nginx"), "requires Linux nginx")
class ServingSwitch(unittest.TestCase):
    def test_readiness_drain_and_rollback(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            def serve(handler):
                srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                stack.callback(srv.server_close)
                stack.callback(srv.shutdown)
                return srv
            def pair(label):
                api = serve(API)
                api.label = label
                api.health = "healthy"
                api.upload_started = threading.Event()
                api.download_started = threading.Event()
                api.release = threading.Event()
                web = serve(Frontend)
                web.api = api.server_port
                return web, api
            old, old_api = pair(b"old")
            new, new_api = pair(b"new")
            front, public_api, monitor = (free_port() for _ in range(3))
            conf, include, pid = (root / n for n in ("nginx.conf", "upstreams.conf", "master.pid"))
            def switch(action, web, api, *args):
                # These HTTP fixtures have no Prisma container. The Docker/image
                # rehearsal covers that probe; this gate exercises nginx itself.
                invoke = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
                          "import switch; switch.main(frontend_auth=lambda port: None)")
                return subprocess.run([sys.executable, "-c", invoke, str(SWITCH.parent), action, str(web.server_port),
                                       str(api.server_port), "--include", str(include), "--master-pid",
                                       str(pid), "--nginx-conf", str(conf), "--monitor", str(monitor),
                                       "--ready-timeout", "1", "--drain-timeout", "2", *args],
                                      capture_output=True, text=True, timeout=25)
            self.assertEqual(switch("init", old, old_api).returncode, 0)
            conf.write_text(f"""
                pid {pid}; error_log {root}/nginx.log; worker_processes 1;
                events {{ worker_connections 128; }}
                http {{
                    access_log off;
                    client_body_temp_path {root}/body; proxy_temp_path {root}/proxy;
                    fastcgi_temp_path {root}/fastcgi; uwsgi_temp_path {root}/uwsgi;
                    scgi_temp_path {root}/scgi;
                    include {include};
                    proxy_http_version 1.1; proxy_request_buffering off; proxy_buffering off;
                    server {{ listen 127.0.0.1:{front}; location / {{
                        proxy_pass http://supoclip_web;
                    }} }}
                    server {{ listen 127.0.0.1:{public_api}; location / {{
                        proxy_pass http://supoclip_api;
                    }} }}
                }}
            """)
            nginx = subprocess.Popen(["nginx", "-c", str(conf), "-g", "daemon off;"])
            stack.callback(nginx.wait, 8)
            stack.callback(nginx.terminate)
            def read(port, path="/"):
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as res:
                    return res.read()
            for _ in range(100):
                if pid.exists():
                    break
                time.sleep(.05)
            self.assertEqual(read(front), b"old")
            new_api.health = "unhealthy"
            fail = switch("switch", new, new_api)
            self.assertNotEqual(fail.returncode, 0)
            self.assertIn("candidate readiness", fail.stderr)
            self.assertEqual(read(front), b"old")
            self.assertEqual(read(public_api), b"old")
            new_api.health = "healthy"
            conn = http.client.HTTPConnection("127.0.0.1", front, timeout=15)
            stack.callback(conn.close)
            conn.putrequest("POST", "/upload")
            conn.putheader("Content-Length", str(2 * len(CHUNK)))
            conn.endheaders()
            conn.send(CHUNK)
            self.assertTrue(old_api.upload_started.wait(5))
            with ThreadPoolExecutor() as pool:
                download = pool.submit(read, front, "/download")
                try:
                    self.assertTrue(old_api.download_started.wait(5))
                    timed_out = switch("switch", new, new_api)
                    self.assertNotEqual(timed_out.returncode, 0)
                    self.assertIn("both releases retained", timed_out.stderr)
                    self.assertEqual(read(front), b"new")
                    self.assertEqual(read(public_api), b"new")
                    conn.send(CHUNK)
                    self.assertEqual(conn.getresponse().read(), b"old" + hashlib.sha256(CHUNK * 2).digest())
                finally:
                    old_api.release.set()
                self.assertEqual(download.result(), CHUNK * 2)
            result = switch("switch", new, new_api)
            self.assertEqual(result.returncode, 0, result.stderr)
            rollback = switch("switch", old, old_api)
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertEqual(read(front), b"old")
            self.assertEqual(read(public_api), b"old")


if __name__ == "__main__":
    unittest.main()
