import { GET } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/backend-api", async () => ({
  ...await vi.importActual<typeof import("@/server/backend-api")>("@/server/backend-api"),
  fetchBackend: vi.fn(),
}));

const SHA = "4932ad4fa73afbfefd678166bf9e7fa01be7785950f4401f9dc3dc5ed1d2ebed";
const context = (path: string[]) => ({ params: Promise.resolve({ path }) });
const request = (path: string, headers: Record<string, string> = {}) =>
  new Request(`https://app.example/api/fx/${path}`, { headers });

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
});

it("requires a session before calling the backend", async () => {
  vi.mocked(getServerSession).mockResolvedValue(null);
  const response = await GET(request("assets"), context(["assets"]));
  expect(response.status).toBe(401);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("proxies the manifest as the signed-in user", async () => {
  const manifest = { version: 1, sfx: [], music: [], emoji: { atlas: `/fx/assets/${SHA}.png`, map: `/fx/assets/${SHA}.json`, cell: 160 } };
  vi.mocked(fetchBackend).mockResolvedValue(new Response(JSON.stringify(manifest), {
    headers: { "Content-Type": "application/json", "Cache-Control": "private, no-cache" },
  }));
  const response = await GET(request("assets"), context(["assets"]));
  expect(fetchBackend).toHaveBeenCalledWith("/fx/assets", expect.objectContaining({ userId: "user-1", method: "GET", cache: "no-store" }));
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("private, no-cache");
  expect(await response.json()).toEqual(manifest);
});

it("streams an asset with its headers preserved and forwards range requests", async () => {
  const chunks = [new Uint8Array([82, 73, 70, 70]), new Uint8Array([1, 2, 3, 4])];
  let pulled = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (pulled < chunks.length) controller.enqueue(chunks[pulled++]);
      else controller.close();
    },
  });
  const upstream = new Response(body, {
    status: 206,
    headers: {
      "Content-Type": "audio/wav", "Content-Length": "8", "Content-Range": "bytes 0-7/9000",
      "Accept-Ranges": "bytes", "Cache-Control": "public, max-age=31536000, immutable",
      ETag: '"abc"', "Last-Modified": "Wed, 11 Nov 2026 10:00:00 GMT", "Set-Cookie": "backend=private",
    },
  });
  vi.mocked(fetchBackend).mockResolvedValue(upstream);
  const incoming = request(`assets/${SHA}.wav`, { Range: "bytes=0-7", "If-Range": '"abc"', Cookie: "session=private" });

  const response = await GET(incoming, context(["assets", `${SHA}.wav`]));

  expect(fetchBackend).toHaveBeenCalledWith(`/fx/assets/${SHA}.wav`, expect.objectContaining({
    userId: "user-1", signal: incoming.signal, extraHeaders: { Range: "bytes=0-7", "If-Range": '"abc"' },
  }));
  // The upstream body is handed through as a stream, not buffered.
  expect(response.body).toBe(upstream.body);
  expect(response.status).toBe(206);
  for (const [name, value] of [
    ["content-type", "audio/wav"], ["content-length", "8"], ["content-range", "bytes 0-7/9000"],
    ["accept-ranges", "bytes"], ["cache-control", "public, max-age=31536000, immutable"], ["etag", '"abc"'],
    ["last-modified", "Wed, 11 Nov 2026 10:00:00 GMT"],
  ]) {
    expect(response.headers.get(name)).toBe(value);
  }
  expect(response.headers.get("set-cookie")).toBeNull();
  expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([82, 73, 70, 70, 1, 2, 3, 4]));
});

it("passes backend 404s through", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(new Response(JSON.stringify({ detail: "Asset not found" }), {
    status: 404, headers: { "Content-Type": "application/json" },
  }));
  const response = await GET(request(`assets/${"0".repeat(64)}.wav`), context(["assets", `${"0".repeat(64)}.wav`]));
  expect(response.status).toBe(404);
});

it.each([
  [["assets", "..", "..", "tasks"]],
  [["..", "tasks"]],
  [["assets", "manifest.json"]],
  [["assets", "sfx", "whoosh_0.wav"]],
  [["assets", `${SHA}.exe`]],
  [["assets", `${SHA.toUpperCase()}.wav`]],
  [["assets", `${SHA}.wav`, "extra"]],
  [["assets", `${SHA}.wav?x=1`]],
  [["other"]],
  [[]],
])("never forwards other paths (%j)", async (path) => {
  const response = await GET(request(path.join("/")), context(path));
  expect(response.status).toBe(404);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("answers 502 when the backend cannot be reached", async () => {
  vi.mocked(fetchBackend).mockRejectedValue(new TypeError("fetch failed"));
  const response = await GET(request("assets"), context(["assets"]));
  expect(response.status).toBe(502);
});
