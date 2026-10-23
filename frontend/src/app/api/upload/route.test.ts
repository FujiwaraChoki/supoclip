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
});
