import { PATCH } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));

const patch = (body: unknown, key = "workflows") =>
  PATCH(
    new Request(`http://localhost/api/admin/feature-flags/${key}`, { method: "PATCH", body: JSON.stringify(body) }),
    { params: Promise.resolve({ key }) },
  );

describe("/api/admin/feature-flags/[key]", () => {
  const featureFlag = { updateMany: vi.fn() };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "admin-1", is_admin: true } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({ featureFlag } as never);
    featureFlag.updateMany.mockResolvedValue({ count: 1 });
  });

  it("is admin-only", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-2", is_admin: false } } as never);
    expect((await patch({ web_enabled: true })).status).toBe(403);
    vi.mocked(getServerSession).mockResolvedValue(null as never);
    expect((await patch({ web_enabled: true })).status).toBe(401);
    expect(featureFlag.updateMany).not.toHaveBeenCalled();
  });

  it("switches one platform and records who did it", async () => {
    const response = await patch({ web_enabled: true });

    expect(response.status).toBe(200);
    expect(featureFlag.updateMany).toHaveBeenCalledWith({
      where: { key: "workflows" },
      data: { web_enabled: true, updated_by: "admin-1" },
    });
  });

  it("switches several fields at once", async () => {
    await patch({ web_enabled: true, ios_enabled: true, admins_bypass: false });

    expect(featureFlag.updateMany).toHaveBeenCalledWith({
      where: { key: "workflows" },
      data: { web_enabled: true, ios_enabled: true, admins_bypass: false, updated_by: "admin-1" },
    });
  });

  it.each([{}, { web_enabled: "yes" }, { description: "x" }, { updated_by: "someone" }, null, [true]])(
    "rejects %j",
    async (body) => {
      expect((await patch(body)).status).toBe(400);
      expect(featureFlag.updateMany).not.toHaveBeenCalled();
    },
  );

  it("404s for an unknown flag", async () => {
    featureFlag.updateMany.mockResolvedValue({ count: 0 });
    expect((await patch({ ios_enabled: true }, "nope")).status).toBe(404);
  });
});
