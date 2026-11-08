// @vitest-environment node
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import http from "node:http";
import net from "node:net";
import path from "node:path";

const require = createRequire(import.meta.url);
const preloadPath = path.resolve(__dirname, "../../server-request-timeout.cjs");
const { DEFAULT_REQUEST_TIMEOUT_MS, requestTimeoutMs, withRequestTimeout } = require(preloadPath) as {
  DEFAULT_REQUEST_TIMEOUT_MS: number;
  requestTimeoutMs: (env: Record<string, string | undefined>) => number;
  withRequestTimeout: (createServer: typeof http.createServer, timeoutMs: number) => typeof http.createServer;
};

// Runs `script` in a fresh Node, preloaded the way the Dockerfile starts Next.
function nodeWithPreload(script: string, env: Record<string, string> = {}) {
  const childEnv: NodeJS.ProcessEnv = { ...process.env, ...env };
  delete childEnv.VITEST;
  return execFileSync(process.execPath, ["--require", preloadPath, "-e", script], {
    env: childEnv,
    encoding: "utf8",
  }).trim();
}

const echoLength: http.RequestListener = (req, res) => {
  let received = 0;
  req.on("data", (chunk: Buffer) => { received += chunk.length; });
  req.on("end", () => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ received }));
  });
};

// A client that streams `chunks` bytes one per `intervalMs`, like a phone
// uploading over a slow link, and resolves with the status line it gets back.
function slowUpload(port: number, chunks: number, intervalMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, "127.0.0.1");
    let response = "";
    let sent = 0;
    let timer: NodeJS.Timeout | undefined;
    socket.on("connect", () => {
      socket.write(
        `POST /api/upload HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/octet-stream\r\n` +
        `Content-Length: ${chunks}\r\nConnection: close\r\n\r\n`,
      );
      timer = setInterval(() => {
        if (sent >= chunks || socket.destroyed) return clearInterval(timer);
        socket.write("x");
        sent += 1;
      }, intervalMs);
    });
    socket.on("data", (data) => { response += data.toString(); });
    socket.on("error", (error: NodeJS.ErrnoException) => {
      // The server may close the socket while we are still writing.
      if (error.code !== "EPIPE" && error.code !== "ECONNRESET") reject(error);
    });
    socket.on("close", () => {
      clearInterval(timer);
      resolve(response.split("\r\n")[0] ?? "");
    });
  });
}

async function listen(server: http.Server): Promise<number> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return (server.address() as net.AddressInfo).port;
}

describe("server-request-timeout preload", () => {
  it("defaults to one hour and reads SERVER_REQUEST_TIMEOUT_MS", () => {
    expect(DEFAULT_REQUEST_TIMEOUT_MS).toBe(3_600_000);
    expect(requestTimeoutMs({})).toBe(3_600_000);
    expect(requestTimeoutMs({ SERVER_REQUEST_TIMEOUT_MS: " " })).toBe(3_600_000);
    expect(requestTimeoutMs({ SERVER_REQUEST_TIMEOUT_MS: "7200000" })).toBe(7_200_000);
    expect(requestTimeoutMs({ SERVER_REQUEST_TIMEOUT_MS: "0" })).toBe(0);
  });

  it("ignores an invalid override", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(requestTimeoutMs({ SERVER_REQUEST_TIMEOUT_MS: "1h" })).toBe(3_600_000);
    expect(requestTimeoutMs({ SERVER_REQUEST_TIMEOUT_MS: "-5" })).toBe(3_600_000);
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });

  it("applies the timeout for every createServer signature and keeps an explicit one", () => {
    const create = withRequestTimeout(http.createServer, 1_234_000);
    const listener: http.RequestListener = () => {};
    const servers = [
      create(),
      create(listener),
      create({ keepAlive: true }),
      create({ keepAlive: true }, listener),
    ];
    for (const server of servers) {
      expect(server.requestTimeout).toBe(1_234_000);
      // Node's 60 s header limit is untouched.
      expect(server.headersTimeout).toBe(60_000);
    }
    expect(servers[1].listenerCount("request")).toBe(1);
    expect(servers[3].listenerCount("request")).toBe(1);
    expect(create({ requestTimeout: 5_000 }).requestTimeout).toBe(5_000);
  });

  it("raises Node's 300 s default for servers created after it is preloaded", () => {
    // Without the preload this is what Next's standalone server got.
    expect(http.createServer().requestTimeout).toBe(300_000);
    // Next creates its server with `_http.default.createServer(listener)`,
    // which reads the property off the module at call time.
    const script =
      "const http = require('http');" +
      "const interop = { default: http };" +
      "console.log(interop.default.createServer(() => {}).requestTimeout)";
    expect(nodeWithPreload(script)).toBe("3600000");
    expect(nodeWithPreload(script, { SERVER_REQUEST_TIMEOUT_MS: "0" })).toBe("0");
    // Loading it twice does not wrap twice.
    expect(nodeWithPreload(
      `require(${JSON.stringify(preloadPath)}); ${script}`,
      { SERVER_REQUEST_TIMEOUT_MS: "4000000" },
    )).toBe("4000000");
  });

  // The production failure, scaled from 300 s to 1 s: a body that takes
  // longer than requestTimeout to arrive gets a 408 mid-upload.
  it("lets a slow upload finish that Node's limit would cut off with a 408", async () => {
    const uploadMs = 1_600;
    const chunks = 16;
    const interval = uploadMs / chunks;

    const limited = http.createServer(
      { requestTimeout: 1_000, connectionsCheckingInterval: 50 },
      echoLength,
    );
    const raised = withRequestTimeout(http.createServer, 10_000)(
      { connectionsCheckingInterval: 50 },
      echoLength,
    );
    try {
      const [limitedStatus, raisedStatus] = await Promise.all([
        listen(limited).then((port) => slowUpload(port, chunks, interval)),
        listen(raised).then((port) => slowUpload(port, chunks, interval)),
      ]);
      expect(limitedStatus).toBe("HTTP/1.1 408 Request Timeout");
      expect(raisedStatus).toBe("HTTP/1.1 200 OK");
    } finally {
      limited.close();
      raised.close();
    }
  }, 10_000);
});
