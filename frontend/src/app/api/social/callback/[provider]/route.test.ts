import { GET } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/backend-api", async () => {
  const actual = await vi.importActual<typeof import("@/server/backend-api")>(
    "@/server/backend-api",
  );
  return {
    ...actual,
    fetchBackend: vi.fn(),
  };
});

function callbackRequest(provider: string, query: Record<string, string>) {
  const url = new URL(`http://localhost:3000/api/social/callback/${provider}`);
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, value);
  }
  return {
    request: new Request(url.toString()),
    context: { params: Promise.resolve({ provider }) },
  };
}

describe("/api/social/callback/[provider]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("redirects with an error when the platform denied access", async () => {
    const { request, context } = callbackRequest("youtube", {
      error: "access_denied",
      error_description: "User declined",
    });

    const response = await GET(request, context);

    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") as string);
    expect(location.pathname).toBe("/settings/social");
    expect(location.searchParams.get("error")).toBe("User declined");
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("rejects unknown providers without touching the backend", async () => {
    const { request, context } = callbackRequest("myspace", { code: "abc", state: "xyz" });

    const response = await GET(request, context);

    const location = new URL(response.headers.get("location") as string);
    expect(location.searchParams.get("error")).toBe("Unknown provider");
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("redirects to sign-in guidance when the session is gone", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    const { request, context } = callbackRequest("tiktok", { code: "abc", state: "xyz" });

    const response = await GET(request, context);

    const location = new URL(response.headers.get("location") as string);
    expect(location.searchParams.get("error")).toMatch(/session expired/i);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("forwards code and state to the backend and reports the connected account", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ connection: { username: "creator" } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { request, context } = callbackRequest("instagram", { code: "abc#_", state: "xyz" });

    const response = await GET(request, context);

    expect(fetchBackend).toHaveBeenCalledWith(
      "/social/connections/instagram/callback",
      expect.objectContaining({
        method: "POST",
        userId: "user-1",
        body: JSON.stringify({ code: "abc#_", state: "xyz" }),
      }),
    );
    const location = new URL(response.headers.get("location") as string);
    expect(location.searchParams.get("connected")).toBe("instagram");
    expect(location.searchParams.get("account")).toBe("creator");
  });

  it("surfaces backend validation errors", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ detail: "This sign-in link has expired" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { request, context } = callbackRequest("youtube", { code: "abc", state: "old" });

    const response = await GET(request, context);

    const location = new URL(response.headers.get("location") as string);
    expect(location.searchParams.get("error")).toBe("This sign-in link has expired");
  });
});
