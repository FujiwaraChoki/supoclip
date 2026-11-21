import { createProxyResponse, fetchBackend } from "./backend-api";

const incomingHeaders = vi.hoisted(() => ({ current: new Headers() }));
const workspaceCookie = vi.hoisted(() => ({ current: undefined as string | undefined }));
const membership = vi.hoisted(() => ({ findUnique: vi.fn() }));
vi.mock("next/headers", () => ({
  headers: async () => incomingHeaders.current,
  cookies: async () => ({
    get: (name: string) =>
      name === "supoclip_team_id" && workspaceCookie.current ? { name, value: workspaceCookie.current } : undefined,
  }),
}));
vi.mock("@/server/prisma", () => ({ getPrismaClient: () => ({ teamMember: membership }) }));

describe("active workspace", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    workspaceCookie.current = undefined;
    membership.findUnique.mockReset();
  });

  const teamHeader = async (init: Parameters<typeof fetchBackend>[1]) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    await fetchBackend("/tasks/", init);
    return fetchMock.mock.calls[0][1].headers["x-supoclip-team-id"];
  };

  it("sends the cookie's team while the caller is still a member", async () => {
    workspaceCookie.current = "team-alpha-1";
    membership.findUnique.mockResolvedValue({ team_id: "team-alpha-1" });
    expect(await teamHeader({ userId: "member" })).toBe("team-alpha-1");
    expect(membership.findUnique).toHaveBeenCalledWith(
      expect.objectContaining({ where: { team_id_user_id: { team_id: "team-alpha-1", user_id: "member" } } }),
    );
  });

  it("falls back to the personal workspace when the cookie outlived the membership", async () => {
    // Removed from the team, team deleted, or another account on this browser.
    workspaceCookie.current = "team-alpha-1";
    membership.findUnique.mockResolvedValue(null);
    expect(await teamHeader({ userId: "former-member" })).toBeUndefined();
  });

  it("never turns a failed membership lookup into a personal request", async () => {
    workspaceCookie.current = "team-alpha-1";
    membership.findUnique.mockRejectedValue(new Error("database unavailable"));
    vi.stubGlobal("fetch", vi.fn());
    await expect(fetchBackend("/tasks/", { userId: "member" })).rejects.toThrow("database unavailable");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("uses the workspace a caller names, and none for the personal one", async () => {
    workspaceCookie.current = "team-alpha-1";
    expect(await teamHeader({ userId: "member", teamId: null })).toBeUndefined();
    expect(await teamHeader({ userId: "member", teamId: "team-beta-2" })).toBe("team-beta-2");
    expect(membership.findUnique).not.toHaveBeenCalled();
  });
});

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

describe("client platform", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    incomingHeaders.current = new Headers();
  });

  it.each([
    ["ios", "ios"],
    [" iOS ", "ios"],
    ["web", undefined],
    ["android", undefined],
    [null, undefined],
  ])("forwards x-supoclip-client %j as %j", async (sent, forwarded) => {
    incomingHeaders.current = new Headers(sent === null ? {} : { "x-supoclip-client": sent });
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    await fetchBackend("/tasks/workflows", { userId: "owner" });
    expect(fetchMock.mock.calls[0][1].headers["x-supoclip-client"]).toBe(forwarded);
  });
});
