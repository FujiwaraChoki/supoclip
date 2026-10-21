import { PATCH } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));

const patch = (body: unknown) =>
  PATCH(
    new Request("http://localhost/api/admin/users/user-1/legacy-hd", { method: "PATCH", body: JSON.stringify(body) }),
    { params: Promise.resolve({ id: "user-1" }) },
  );

describe("/api/admin/users/[id]/legacy-hd", () => {
  const user = { updateMany: vi.fn() };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "admin-1", is_admin: true } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({ user } as never);
    user.updateMany.mockResolvedValue({ count: 1 });
  });

  it("is admin-only", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-2", is_admin: false } } as never);

    expect((await patch({ legacy_hd: true })).status).toBe(403);
    expect(user.updateMany).not.toHaveBeenCalled();
  });

  it.each([true, false])("sets legacy_hd to %s", async (legacyHd) => {
    const response = await patch({ legacy_hd: legacyHd });

    expect(response.status).toBe(200);
    expect(user.updateMany).toHaveBeenCalledWith({ where: { id: "user-1" }, data: { legacy_hd: legacyHd } });
  });

  it("rejects anything but a boolean", async () => {
    expect((await patch({ legacy_hd: "yes" })).status).toBe(400);
    expect(user.updateMany).not.toHaveBeenCalled();
  });

  it("404s for an unknown user", async () => {
    user.updateMany.mockResolvedValue({ count: 0 });

    expect((await patch({ legacy_hd: true })).status).toBe(404);
  });
});
