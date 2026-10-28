import { GET } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/backend-api", async () => ({
  ...await vi.importActual<typeof import("@/server/backend-api")>("@/server/backend-api"),
  fetchBackend: vi.fn(),
}));

const path = ["task", "clips", "clip", "editor", "media", "clean.mp4"];
const signed = "https://storage.example/editor/clean.mp4?signature=private";
const context = (value = path) => ({ params: Promise.resolve({ path: value }) });

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getServerSession).mockResolvedValue({ user: { id: "owner" } } as never);
});
afterEach(() => vi.unstubAllGlobals());

it("streams signed editor media with ranges on the authenticated origin, without forwarding credentials", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(new Response(null, { status: 307, headers: { Location: signed } }));
  const bytes = new Uint8Array([0, 0, 0, 32, 102, 116, 121, 112]);
  const storageFetch = vi.fn().mockResolvedValue(new Response(bytes, { status: 206, headers: {
    "Content-Type": "video/mp4", "Content-Range": "bytes 0-7/1000", "Content-Length": "8",
    "Cache-Control": "public, max-age=86400", "Accept-Ranges": "bytes",
  } }));
  vi.stubGlobal("fetch", storageFetch);
  const request = new Request("https://app.example/api/tasks/" + path.join("/"), { headers: {
    Range: "bytes=0-7", "If-Range": '"etag"', Accept: "video/*",
    Cookie: "private-session", Authorization: "Bearer private", "x-supoclip-signature": "private",
  } });
  expect(request.headers.get("range")).toBe("bytes=0-7");
  const response = await GET(request, context());
  expect(response.status).toBe(206);
  expect(response.headers.get("content-range")).toBe("bytes 0-7/1000");
  expect(response.headers.get("content-length")).toBe("8");
  expect(response.headers.get("content-type")).toBe("video/mp4");
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(new Uint8Array(await response.arrayBuffer())).toEqual(bytes);
  expect(storageFetch).toHaveBeenCalledOnce();
  expect(storageFetch).toHaveBeenCalledWith(signed, expect.objectContaining({ credentials: "omit", signal: request.signal, cache: "no-store" }));
  const forwarded = storageFetch.mock.calls[0][1].headers;
  expect(forwarded).toEqual({ accept: "video/*", "if-range": '"etag"', range: "bytes=0-7" });
});

it("requires authentication before resolving private editor media", async () => {
  vi.mocked(getServerSession).mockResolvedValue(null);
  expect((await GET(new Request("https://app.example/"), context())).status).toBe(401);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("preserves redirects for ordinary clip downloads", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(new Response(null, { status: 307, headers: { Location: signed } }));
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const response = await GET(new Request("https://app.example/"), context(["task", "clips", "clip", "file"]));
  expect(response.status).toBe(307);
  expect(response.headers.get("location")).toBe(signed);
  expect(fetch).not.toHaveBeenCalled();
});

it("keeps local media responses unchanged", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(new Response("local", { status: 206 }));
  const response = await GET(new Request("https://app.example/"), context());
  expect(response.status).toBe(206);
  expect(await response.text()).toBe("local");
});

it("rejects a malformed storage redirect", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(new Response(null, { status: 307, headers: { Location: "file:///private" } }));
  expect((await GET(new Request("https://app.example/"), context())).status).toBe(502);
});
