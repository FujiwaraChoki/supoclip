import { createProxyResponse, fetchBackend } from "./backend-api";

const incomingHeaders = vi.hoisted(() => ({ current: new Headers() }));
vi.mock("next/headers", () => ({ headers: async () => incomingHeaders.current }));

describe("backend S3 redirects", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns signed redirects to the browser without following them with backend credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 307, headers: {
      Location: "https://s3.test/private-clip?signature=test", "Cache-Control": "private, no-store",
    } }));
    vi.stubGlobal("fetch", fetchMock);
    const response = createProxyResponse(await fetchBackend("/tasks/task/clips/clip/file", { userId: "owner" }));
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ redirect: "manual" }));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://s3.test/private-clip?signature=test");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });
});

describe("backend trace ids", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    incomingHeaders.current = new Headers();
  });

  it("forwards the browser's trace id to the backend", async () => {
    incomingHeaders.current = new Headers({ "x-trace-id": "3f1c9a52-7d1e-4b8a-9c11-0a2b3c4d5e6f" });
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    await fetchBackend("/tasks/", { userId: "owner" });
    expect(fetchMock.mock.calls[0][1].headers["x-trace-id"]).toBe("3f1c9a52-7d1e-4b8a-9c11-0a2b3c4d5e6f");
  });

  it("replaces a missing or malformed trace id with a fresh one", async () => {
    incomingHeaders.current = new Headers({ "x-trace-id": "bad id <script>" });
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    await fetchBackend("/tasks/");
    expect(fetchMock.mock.calls[0][1].headers["x-trace-id"]).toMatch(/^[0-9a-f-]{36}$/);
  });
});
