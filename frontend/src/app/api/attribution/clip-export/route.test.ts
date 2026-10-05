import { POST } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

describe("POST /api/attribution/clip-export", () => {
  const updateMany = vi.fn();
  const findUnique = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getPrismaClient).mockReturnValue({ userAcquisition: { updateMany, findUnique } } as never);
  });

  it("returns 401 without a session", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await POST()).status).toBe(401);
  });

  it("reports a repeat or unattributed export as not first", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    updateMany.mockResolvedValue({ count: 0 });

    await expect((await POST()).json()).resolves.toEqual({ first_export: false });
    expect(updateMany).toHaveBeenCalledWith({
      where: { user_id: "user-1", first_clip_exported_at: null },
      data: { first_clip_exported_at: expect.any(Date) },
    });
    expect(findUnique).not.toHaveBeenCalled();
  });

  it("returns stored attribution and time to activation on the first export", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1", createdAt: new Date(Date.now() - 3 * 36e5) },
    } as never);
    updateMany.mockResolvedValue({ count: 1 });
    findUnique.mockResolvedValue({ utm_source: "reddit", utm_campaign: "launch" });

    await expect((await POST()).json()).resolves.toEqual({
      first_export: true,
      attribution: { utm_source: "reddit", utm_campaign: "launch" },
      hours_since_signup: 3,
    });
  });
});
