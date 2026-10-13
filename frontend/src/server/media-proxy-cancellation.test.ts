// @vitest-environment node
import { createServer, type Server } from "node:http";
import { http, passthrough } from "msw";

import { GET as getTask } from "@/app/api/tasks/[...path]/route";
import { GET as getShare } from "@/app/api/share/[...path]/route";
import { server as mockServer } from "@/test/setup";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn().mockResolvedValue({ user: { id: "user-1" } }),
}));

const proxies = [
  { name: "task", get: getTask, path: ["task-1", "clips", "clip-1", "file"] },
  { name: "share", get: getShare, path: ["token-1", "clips", "clip-1", "file"] },
];

async function listen(server: Server) {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing test port");
  vi.stubEnv("BACKEND_INTERNAL_URL", `http://127.0.0.1:${address.port}`);
  vi.stubEnv("BACKEND_AUTH_SECRET", "proxy-cancellation-test-secret");
}

async function close(server: Server) {
  server.closeAllConnections();
  await new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
}

describe("media proxy disconnect cleanup", () => {
  beforeEach(() => {
    // Exercise a real backend socket instead of mocking fetch or its signal.
    mockServer.use(http.all(/http:\/\/127\.0\.0\.1/, () => passthrough()));
  });

  afterEach(() => vi.unstubAllEnvs());

  it.each(proxies)("$name closes the backend before response headers arrive", async ({ get, path }) => {
    let received!: () => void;
    const requestReceived = new Promise<void>((resolve) => { received = resolve; });
    let disconnected = false;
    const backend = createServer((_request, response) => {
      response.on("close", () => { disconnected = true; });
      received();
      // Leave headers pending: the browser has already gone when they arrive.
    });
    const controller = new AbortController();
    await listen(backend);
    try {
      const pending = get(new Request("http://localhost/api/media", {
        signal: controller.signal,
      }), { params: Promise.resolve({ path }) });
      // Attach the rejection assertion before aborting to avoid unhandled errors.
      const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
      await requestReceived;
      controller.abort();
      await rejected;
      await vi.waitFor(() => expect(disconnected).toBe(true), { timeout: 1000 });
    } finally {
      controller.abort();
      await close(backend);
    }
  });

  it.each(proxies)("$name closes an unread backend stream after headers arrive", async ({ get, path }) => {
    let disconnected = false;
    const backend = createServer((_request, response) => {
      response.writeHead(206, {
        "Content-Type": "video/mp4",
        "Content-Range": "bytes 0-65535/131072",
      });
      response.write(Buffer.alloc(1024));
      response.on("close", () => { disconnected = true; });
      // Keep the body open and unread, as in the production leak.
    });
    const controller = new AbortController();
    await listen(backend);
    try {
      const response = await get(new Request("http://localhost/api/media", {
        signal: controller.signal,
        headers: { Range: "bytes=0-65535" },
      }), { params: Promise.resolve({ path }) });
      expect(response.status).toBe(206);
      expect(response.headers.get("content-range")).toBe("bytes 0-65535/131072");
      controller.abort();
      await vi.waitFor(() => expect(disconnected).toBe(true), { timeout: 1000 });
      // Node versions either abort the body or mark it unusable after cleanup.
      await expect(response.arrayBuffer()).rejects.toThrow();
    } finally {
      controller.abort();
      await close(backend);
    }
  });
});
