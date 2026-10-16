"""Real YouTube downloads. Opt in with YTDL_LIVE_TESTS=1 (needs a non-blocked IP)."""
import base64
import os
import select
import socket
import socketserver
import subprocess
import threading

import pytest

from supoclip_ytdl.config import Route, Settings
from supoclip_ytdl.downloader import Progress, download
from supoclip_ytdl.routing import Router

pytestmark = pytest.mark.skipif(os.getenv("YTDL_LIVE_TESTS") != "1", reason="live network test")

VIDEO = "dQw4w9WgXcQ"


class Proxy(socketserver.ThreadingTCPServer):
    """Minimal authenticating CONNECT proxy that rejects the first session it sees."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), ProxyHandler)
        self.sessions: list[str] = []
        self.hosts: set[str] = set()
        self.bytes_in = 0
        self.lock = threading.Lock()


class ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server: Proxy = self.server
        request = self.rfile.readline().decode()
        headers = {}
        while (line := self.rfile.readline().decode().strip()):
            key, _, value = line.partition(":")
            headers[key.lower()] = value.strip()
        method, target, _ = request.split(" ", 2)
        user = base64.b64decode(headers.get("proxy-authorization", "Basic ").split(" ", 1)[1] or b"").decode()
        session = user.split(":")[0]
        with server.lock:
            first = not server.sessions
            if session not in server.sessions:
                server.sessions.append(session)
            rejected = server.sessions[0] == session
        if method != "CONNECT" or rejected:
            self.wfile.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            return
        host, port = target.rsplit(":", 1)
        server.hosts.add(host)
        upstream = socket.create_connection((host, int(port)), timeout=30)
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        sockets = [self.connection, upstream]
        while True:
            readable, _, _ = select.select(sockets, [], [], 30)
            if not readable:
                break
            for sock in readable:
                data = sock.recv(65536)
                if not data:
                    upstream.close()
                    return
                if sock is upstream:
                    with server.lock:
                        server.bytes_in += len(data)
                    self.connection.sendall(data)
                else:
                    upstream.sendall(data)


@pytest.fixture
def proxy():
    server = Proxy()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def test_download_through_rotating_proxy(tmp_path, proxy):
    port = proxy.server_address[1]
    route = Route("residential", f"http://user-{{session}}:pw@127.0.0.1:{port}", attempts=3)
    settings = Settings(data_dir=tmp_path, routes=(route,), player_clients=(None,))
    router = Router(settings.routes, 3, 600)
    progress = Progress()
    result = download(VIDEO, 360, 10**9, settings, router, progress, tmp_path / "work")

    assert result.attempts == 2, "first session is rejected, second must succeed"
    assert len(proxy.sessions) == 2 and proxy.sessions[0] != proxy.sessions[1]
    assert all(s.startswith("user-") and len(s) == 17 for s in proxy.sessions)
    assert any(h.endswith("googlevideo.com") for h in proxy.hosts), "media must use the proxy too"
    assert result.height == 360
    # Everything YouTube sent went through the proxy and was accounted for.
    assert proxy.bytes_in >= result.size
    assert result.transferred_bytes <= proxy.bytes_in
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(result.path)], capture_output=True, text=True)
    assert set(probe.stdout.split()) == {"video", "audio"}
    stats = router.snapshot()["residential"]
    assert stats["successes"] == 1 and stats["failures"] == 1
