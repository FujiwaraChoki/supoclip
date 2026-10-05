import { POST } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { recordFirstClipExport } from "@/server/user-acquisition";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

vi.mock("@/server/user-acquisition", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/server/user-acquisition")>()),
  recordFirstClipExport: vi.fn(),
}));

describe("POST /api/attribution/clip-export", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getPrismaClient).mockReturnValue({ $queryRawUnsafe: vi.fn() } as never);
  });

  it("returns 401 without a session", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await POST()).status).toBe(401);
  });

  it("reports a repeat or untracked export as not first", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    vi.mocked(recordFirstClipExport).mockResolvedValue(null);

    await expect((await POST()).json()).resolves.toEqual({ first_export: false });
  });

  it("lets new accounts record an export before attribution syncs, but not older ones", async () => {
    vi.mocked(recordFirstClipExport).mockResolvedValue(null);

    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    await POST();
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1", createdAt: new Date(Date.now() - 3 * 24 * 36e5) },
    } as never);
    await POST();

    expect(vi.mocked(recordFirstClipExport).mock.calls.map((call) => call[3])).toEqual([
      { allowInsert: true },
      { allowInsert: false },
    ]);
  });

  it("returns stored attribution and time to activation on the first export", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1", createdAt: new Date(Date.now() - 3 * 36e5) },
    } as never);
    vi.mocked(recordFirstClipExport).mockResolvedValue({ utm_source: "reddit", utm_campaign: "launch" });

    await expect((await POST()).json()).resolves.toEqual({
      first_export: true,
      attribution: { utm_source: "reddit", utm_campaign: "launch" },
      hours_since_signup: 3,
    });
  });
});
