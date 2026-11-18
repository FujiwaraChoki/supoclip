// @vitest-environment node
import { once } from "node:events";
import { createServer, type RequestListener, type Server } from "node:http";

import { headers } from "next/headers";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

import { server as msw } from "@/test/setup";

import { GET } from "./route";

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/backend-auth", () => ({
  buildBackendAuthHeaders: vi.fn(),
}));

function connectionReset() {
  return new TypeError("fetch failed", {
    cause: Object.assign(new Error("read ECONNRESET"), { code: "ECONNRESET" }),
  });
}

function fontsResponse() {
  return new Response(JSON.stringify({ fonts: [{ name: "Inter", format: "ttf" }] }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("GET /api/fonts", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(headers).mockResolvedValue(new Headers() as never);
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(buildBackendAuthHeaders).mockReturnValue({ "x-supoclip-user-id": "user-1" });
    process.env.BACKEND_INTERNAL_URL = "http://backend:8000";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns 401 without an authenticated session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes the backend's font list through unchanged", async () => {
    const fetchMock = vi.fn().mockResolvedValue(fontsResponse());
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend:8000/fonts",
      expect.objectContaining({
        cache: "no-store",
        headers: { "x-supoclip-user-id": "user-1", "x-trace-id": expect.any(String) },
        signal: expect.any(AbortSignal),
      }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(response.headers.get("cache-control")).toBeNull();
    await expect(response.json()).resolves.toEqual({ fonts: [{ name: "Inter", format: "ttf" }] });
    expect(console.error).not.toHaveBeenCalled();
  });

  it("retries once when the backend resets the connection", async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(connectionReset()).mockResolvedValueOnce(fontsResponse());
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ fonts: [{ name: "Inter", format: "ttf" }] });
  });

  it("answers 503 when the backend keeps resetting the connection", async () => {
    const fetchMock = vi.fn().mockRejectedValue(connectionReset());
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({ error: "Fonts are temporarily unavailable" });
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(console.error).mock.calls[0])).toContain("ECONNRESET");
    expect(String(vi.mocked(console.error).mock.calls[0])).not.toContain("user-1");
  });

  it("answers 503 when the backend times out", async () => {
    const timeout = new DOMException("The operation was aborted due to timeout", "TimeoutError");
    const fetchMock = vi.fn().mockRejectedValue(timeout);
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(console.error).toHaveBeenCalledTimes(1);
  });
});

// A real undici fetch against a local server, so the errors are the ones Node actually throws.
describe("GET /api/fonts against a real backend connection", () => {
  let upstream: Server | undefined;

  async function startBackend(handler: RequestListener) {
    upstream = createServer(handler);
    upstream.listen(0, "127.0.0.1");
    await once(upstream, "listening");
    const { port } = upstream.address() as { port: number };
    process.env.BACKEND_INTERNAL_URL = `http://127.0.0.1:${port}`;
  }

  // Sends the headers and part of the body, then drops the connection.
  function cutOffBody(response: Parameters<RequestListener>[1]) {
    response.writeHead(200, { "Content-Type": "application/json", "Content-Length": "100" });
    response.flushHeaders();
    response.write('{"fonts":[');
    setTimeout(() => response.destroy(), 20);
  }

  beforeEach(() => {
    vi.clearAllMocks();
    msw.close(); // let fetch reach the local server
    vi.mocked(headers).mockResolvedValue(new Headers() as never);
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(buildBackendAuthHeaders).mockReturnValue({ "x-supoclip-user-id": "user-1" });
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(async () => {
    if (upstream) {
      upstream.closeAllConnections();
      await new Promise<void>((resolve) => upstream!.close(() => resolve()));
      upstream = undefined;
    }
    msw.listen({ onUnhandledRequest: "error" });
  });

  it("retries once when the connection drops while the body is read", async () => {
    const methods: string[] = [];
    await startBackend((request, response) => {
      methods.push(request.method ?? "");
      if (methods.length === 1) return cutOffBody(response);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ fonts: [{ name: "Inter", format: "ttf" }] }));
    });

    const response = await GET();

    expect(methods).toEqual(["GET", "GET"]);
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ fonts: [{ name: "Inter", format: "ttf" }] });
    expect(console.error).not.toHaveBeenCalled();
  });

  it("answers 503 when the body is cut off on both attempts", async () => {
    let calls = 0;
    await startBackend((_request, response) => {
      calls += 1;
      cutOffBody(response);
    });

    const response = await GET();

    expect(calls).toBe(2);
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({ error: "Fonts are temporarily unavailable" });
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(console.error).mock.calls[0])).toContain("UND_ERR_SOCKET");
  });
});
