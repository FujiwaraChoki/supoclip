import { captureApiFailure } from "@/lib/posthog";

import { installTraceFetch } from "./trace-fetch";

vi.mock("@/lib/posthog", () => ({ captureApiFailure: vi.fn() }));

describe("installTraceFetch", () => {
  const originalFetch = window.fetch;
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.mocked(captureApiFailure).mockReset();
    window.fetch = fetchMock;
    installTraceFetch();
  });

  afterEach(() => {
    window.fetch = originalFetch;
  });

  it("tags API route requests with a trace id and keeps existing headers", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await window.fetch("/api/tasks/", { headers: { "Content-Type": "application/json" } });

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("x-trace-id")).toMatch(/^[0-9a-f-]{36}$/);
    expect(headers.get("content-type")).toBe("application/json");
    expect(captureApiFailure).not.toHaveBeenCalled();
  });

  it("leaves auth, analytics, and cross-origin requests untouched", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
    const init = { method: "PUT" };
    await window.fetch("/api/auth/get-session");
    await window.fetch("https://uploads.example.com/part-1", init);

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/auth/get-session", undefined);
    expect(fetchMock).toHaveBeenNthCalledWith(2, "https://uploads.example.com/part-1", init);
  });

  it("reports 5xx responses with the backend trace id", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 502, headers: { "x-trace-id": "backend-trace" } }));
    await window.fetch("/api/tasks/abc/cancel?x=1", { method: "post" });

    expect(captureApiFailure).toHaveBeenCalledWith({
      method: "POST",
      path: "/api/tasks/abc/cancel",
      status: 502,
      traceId: "backend-trace",
    });
  });

  it("does not report client errors", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 404 }));
    await window.fetch("/api/tasks/missing");
    expect(captureApiFailure).not.toHaveBeenCalled();
  });

  it.each([502, 503, 504])("does not report transient gateway errors for background polls (%s)", async (status) => {
    fetchMock.mockResolvedValue(new Response("{}", { status }));
    await window.fetch("/api/announcements");
    await window.fetch("/api/service-status/video-downloads/");
    expect(captureApiFailure).not.toHaveBeenCalled();
  });

  it("still reports other server errors for those polls", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 500 }));
    await window.fetch("/api/announcements");
    expect(captureApiFailure).toHaveBeenCalledWith(expect.objectContaining({
      path: "/api/announcements",
      status: 500,
    }));
  });

  it("does not report network errors for background polls", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(window.fetch("/api/announcements")).rejects.toThrow("Failed to fetch");
    await expect(window.fetch("/api/service-status/video-downloads")).rejects.toThrow("Failed to fetch");
    expect(captureApiFailure).not.toHaveBeenCalled();
  });
});
