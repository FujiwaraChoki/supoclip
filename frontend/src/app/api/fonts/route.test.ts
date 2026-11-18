import { headers } from "next/headers";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

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

  it("retries once when the connection drops while the body is read", async () => {
    const truncated = fontsResponse();
    vi.spyOn(truncated, "text").mockRejectedValue(connectionReset());
    const fetchMock = vi.fn().mockResolvedValueOnce(truncated).mockResolvedValueOnce(fontsResponse());
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
