import { headers } from "next/headers";

import { POST } from "./route";
import { auth } from "@/lib/auth";
import { uploadTooLargeMessage } from "@/lib/video-upload";

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

describe("/api/upload", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllEnvs();
    vi.mocked(headers).mockResolvedValue(new Headers());
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "owner" } } as never);
    vi.stubEnv("BACKEND_AUTH_SECRET", "secret");
  });

  afterEach(() => vi.unstubAllGlobals());

  it("rejects a declared body over the cap before reading it", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await POST(new Request("http://app/api/upload", {
      method: "POST",
      headers: { "content-length": String(4_000_000_000 + 1024 * 1024 + 1) },
      body: "not-read",
    }));
    expect(response.status).toBe(413);
    await expect(response.json()).resolves.toEqual({ detail: uploadTooLargeMessage(4_000_000_000) });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a chunked upload that does not declare its length", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await POST(new Request("http://app/api/upload", { method: "POST" }));
    expect(response.status).toBe(411);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("streams the raw body to the backend without parsing it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      video_path: "upload://stored.mp4",
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("http://app/api/upload", {
      method: "POST",
      headers: {
        "content-length": "10",
        "content-type": "multipart/form-data; boundary=abc",
        "idempotency-key": "retry-key-1",
      },
      body: "file-bytes",
    });
    const formData = vi.spyOn(request, "formData");

    const response = await POST(request);

    expect(response.status).toBe(200);
    expect(formData).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { duplex?: string }];
    expect(url).toMatch(/\/upload$/);
    expect(init.duplex).toBe("half");
    expect(init.body).toBe(request.body);
    expect(init.headers).toEqual(expect.objectContaining({
      "content-type": "multipart/form-data; boundary=abc",
      "content-length": "10",
      "idempotency-key": "retry-key-1",
    }));
  });

  function uploadRequest(signal?: AbortSignal) {
    return new Request("http://app/api/upload", {
      method: "POST",
      headers: {
        "content-length": "10",
        "content-type": "multipart/form-data; boundary=abc",
      },
      body: "file-bytes",
      signal,
    });
  }

  it("answers 502 with JSON when the API cannot be reached", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const response = await POST(uploadRequest());

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      detail: "The upload could not be stored. Please try again.",
    });
    expect(error).toHaveBeenCalledTimes(1);
  });

  it("does not log a server error when the client hung up mid-upload", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const client = new AbortController();
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => {
      client.abort();
      throw new TypeError("terminated");
    }));

    const response = await POST(uploadRequest(client.signal));

    expect(response.status).toBe(499);
    expect(error).not.toHaveBeenCalled();
  });
});
