import { GET, POST } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { getSignupAttribution, saveSignupAttribution } from "@/server/user-acquisition";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

vi.mock("@/server/user-acquisition", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/server/user-acquisition")>()),
  saveSignupAttribution: vi.fn(),
  getSignupAttribution: vi.fn(),
}));

const capturedAt = "2026-10-05T10:00:00.000Z";
const request = (body: unknown) =>
  new Request("http://localhost/api/attribution", { method: "POST", body: JSON.stringify(body) });

const frozen = { utm_source: "reddit", landing_path: "/demo", captured_at: capturedAt };

describe("GET /api/attribution", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getPrismaClient).mockReturnValue({ $queryRawUnsafe: vi.fn() } as never);
  });

  it("returns 401 without a session", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await GET()).status).toBe(401);
  });

  it("returns only the signed-in account's frozen attribution", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    vi.mocked(getSignupAttribution).mockResolvedValue(frozen);

    await expect((await GET()).json()).resolves.toEqual({ attribution: frozen, confirmed: true });
    expect(getSignupAttribution).toHaveBeenCalledWith(expect.any(Function), "user-1");
  });

  it("marks an empty answer for a new account as unconfirmed, so the browser retries", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    vi.mocked(getSignupAttribution).mockResolvedValue(null);

    await expect((await GET()).json()).resolves.toEqual({ attribution: null, confirmed: false });
  });

  it("confirms an empty answer once the account is past the sign-up window", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1", createdAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000) },
    } as never);
    vi.mocked(getSignupAttribution).mockResolvedValue(null);

    await expect((await GET()).json()).resolves.toEqual({ attribution: null, confirmed: true });
  });
});

describe("POST /api/attribution", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getPrismaClient).mockReturnValue({ $queryRawUnsafe: vi.fn() } as never);
  });

  it("returns 401 without a session", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await POST(request({ captured_at: capturedAt }))).status).toBe(401);
  });

  it("rejects invalid payloads", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    expect((await POST(request({ utm_source: "x" }))).status).toBe(400);
  });

  it("does not attribute accounts older than the sign-up window", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1", createdAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000) },
    } as never);
    vi.mocked(getSignupAttribution).mockResolvedValue(null);

    const response = await POST(request({ captured_at: capturedAt, utm_source: "reddit" }));

    await expect(response.json()).resolves.toEqual({ stored: false, reason: "existing_user", attribution: null, confirmed: true });
    expect(saveSignupAttribution).not.toHaveBeenCalled();
  });

  it("stores sanitized first-touch data for new accounts", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    vi.mocked(saveSignupAttribution).mockResolvedValue(true);
    vi.mocked(getSignupAttribution).mockResolvedValue(frozen);

    const response = await POST(
      request({ captured_at: capturedAt, utm_source: "Reddit", utm_campaign: "launch", landing_path: "/demo", is_admin: true }),
    );

    await expect(response.json()).resolves.toEqual({ stored: true, attribution: frozen, confirmed: true });
    expect(saveSignupAttribution).toHaveBeenCalledWith(expect.any(Function), "user-1", {
      utm_source: "reddit",
      utm_campaign: "launch",
      landing_path: "/demo",
      captured_at: capturedAt,
    });
  });

  it("answers a repeat sync with the value frozen by the first one", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    vi.mocked(saveSignupAttribution).mockResolvedValue(false);
    vi.mocked(getSignupAttribution).mockResolvedValue(frozen);

    const response = await POST(request({ captured_at: capturedAt, utm_source: "newsletter" }));

    await expect(response.json()).resolves.toEqual({ stored: false, attribution: frozen, confirmed: true });
  });

  it("strips share tokens from the landing path before storing", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    vi.mocked(saveSignupAttribution).mockResolvedValue(true);

    await POST(request({ captured_at: capturedAt, landing_path: "/share/kQ3v_Zr8-Xb1yTq0pLmN4sHcW7aEoU2fDgJiVnRtYxM" }));

    expect(vi.mocked(saveSignupAttribution).mock.calls[0][2]).toEqual({ captured_at: capturedAt, landing_path: "/share/:token" });
  });
});
