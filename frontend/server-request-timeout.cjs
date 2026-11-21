"use strict";

// Preloaded before Next's standalone server.js (`node --require`, see the
// Dockerfile). Node gives an HTTP server 300 seconds to receive a whole request
// and answers 408 after that. Next never changes it, so an upload streamed
// through /api/upload over a slow phone connection failed after 300-330 s even
// though nginx allows an hour. This raises that limit for the servers Next
// creates.
//
// SERVER_REQUEST_TIMEOUT_MS overrides the default (one hour, matching nginx's
// client_body_timeout and proxy_send_timeout for the upload locations); 0
// disables it. Only receiving the request counts: responses such as SSE
// streams were never limited by it. Header timeouts (60 s) are unchanged.
//
// It also keeps idle keep-alive connections open for 65 seconds instead of
// Node's 5. nginx (1.29.7 and later by default) reuses an idle upstream
// connection for up to 60 seconds. When Node closed one after 5 seconds just
// as nginx sent the next request on it, nginx logged "upstream prematurely
// closed connection" and answered 502 to the POST or PATCH it would not retry.
// The side that waits longer must be the server, so nginx always closes
// first. Next's own KEEP_ALIVE_TIMEOUT, applied after the server is created,
// still overrides this.

const http = require("node:http");

const DEFAULT_REQUEST_TIMEOUT_MS = 60 * 60 * 1000;
// Above nginx's 60 s upstream keepalive_timeout.
const DEFAULT_KEEP_ALIVE_TIMEOUT_MS = 65 * 1000;
const PATCHED = Symbol.for("supoclip.requestTimeoutPatched");

function requestTimeoutMs(env = process.env) {
  const raw = (env.SERVER_REQUEST_TIMEOUT_MS ?? "").trim();
  if (raw === "") return DEFAULT_REQUEST_TIMEOUT_MS;
  if (!/^\d+$/.test(raw)) {
    console.warn(
      `Ignoring SERVER_REQUEST_TIMEOUT_MS=${JSON.stringify(raw)}; using ${DEFAULT_REQUEST_TIMEOUT_MS} ms.`,
    );
    return DEFAULT_REQUEST_TIMEOUT_MS;
  }
  return Number(raw);
}

// Wraps createServer so servers that don't pick their own requestTimeout or
// keepAliveTimeout get `timeoutMs` and `keepAliveTimeoutMs`. Accepts every
// createServer signature: (), (listener), (options), (options, listener).
function withRequestTimeout(createServer, timeoutMs, keepAliveTimeoutMs = DEFAULT_KEEP_ALIVE_TIMEOUT_MS) {
  return function createServerWithRequestTimeout(options, listener) {
    if (typeof options === "function") {
      listener = options;
      options = undefined;
    }
    const resolved = { ...(options || {}) };
    if (resolved.requestTimeout === undefined) {
      resolved.requestTimeout = timeoutMs;
    }
    if (resolved.keepAliveTimeout === undefined) {
      resolved.keepAliveTimeout = keepAliveTimeoutMs;
    }
    return createServer.call(this, resolved, listener);
  };
}

function install(env = process.env) {
  if (http.createServer[PATCHED]) return;
  const patched = withRequestTimeout(http.createServer, requestTimeoutMs(env));
  patched[PATCHED] = true;
  http.createServer = patched;
}

module.exports = {
  DEFAULT_KEEP_ALIVE_TIMEOUT_MS,
  DEFAULT_REQUEST_TIMEOUT_MS,
  requestTimeoutMs,
  withRequestTimeout,
  install,
};

// Tests import this without patching their own process.
if (!process.env.VITEST) install();
