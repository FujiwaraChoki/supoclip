import { GET, POST } from "./route";
import { sendAffiliateEmail } from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));
vi.mock("@/server/affiliates", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/server/affiliates")>()),
  sendAffiliateEmail: vi.fn(),
}));

const application = {
  slug: "maya",
  platform: "tiktok",
  profile_url: "https://www.tiktok.com/@maya",
  audience_size: "1k-10k",
  accept_terms: true,
};
const apply = (body: unknown) =>
  POST(new Request("http://localhost/api/affiliates", { method: "POST", body: JSON.stringify(body) }));

describe("/api/affiliates", () => {
  const affiliate = { findUnique: vi.fn(), upsert: vi.fn() };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({ affiliate } as never);
    affiliate.upsert.mockImplementation(async ({ create }) => ({ ...create, status: "pending" }));
  });

  it("requires a session", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await GET()).status).toBe(401);
    expect((await apply(application)).status).toBe(401);
  });

  it("reports that a new user can apply", async () => {
    affiliate.findUnique.mockResolvedValue(null);

    const data = await (await GET()).json();

    expect(data.application).toBeNull();
    expect(data.can_apply_at).not.toBeNull();
  });

  it("submits an application and notifies admins", async () => {
    affiliate.findUnique.mockResolvedValue(null);

    const response = await apply(application);

    expect(response.status).toBe(201);
    expect(affiliate.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { user_id: "user-1" },
        create: expect.objectContaining({ user_id: "user-1", slug: "maya", status: "pending" }),
      }),
    );
    expect(sendAffiliateEmail).toHaveBeenCalledWith("user-1", expect.objectContaining({ event: "applied", slug: "maya" }));
  });

  it("rejects invalid applications", async () => {
    const response = await apply({ ...application, accept_terms: false });

    expect(response.status).toBe(400);
    expect(affiliate.upsert).not.toHaveBeenCalled();
  });

  it("rejects a slug another user holds", async () => {
    affiliate.findUnique.mockImplementation(async ({ where }) => (where.slug ? { user_id: "someone-else" } : null));

    const response = await apply(application);

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({ error: "That code is already taken." });
  });

  it("maps a lost slug race to a conflict", async () => {
    affiliate.findUnique.mockResolvedValue(null);
    affiliate.upsert.mockRejectedValue(Object.assign(new Error("unique"), { code: "P2002" }));

    expect((await apply(application)).status).toBe(409);
    expect(sendAffiliateEmail).not.toHaveBeenCalled();
  });

  it("blocks a second application while one is pending", async () => {
    affiliate.findUnique.mockResolvedValue({ status: "pending", reviewed_at: null });

    expect((await apply(application)).status).toBe(409);
  });

  it("allows reapplying a month after a decline", async () => {
    affiliate.findUnique.mockImplementation(async ({ where }) =>
      where.user_id ? { status: "declined", reviewed_at: new Date(Date.now() - 31 * 24 * 60 * 60 * 1000) } : null,
    );

    expect((await apply(application)).status).toBe(201);
  });
});
