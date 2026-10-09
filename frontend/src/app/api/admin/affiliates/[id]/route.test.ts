import { POST } from "./route";
import { getStripeClient } from "@/lib/stripe";
import { sendAffiliateEmail } from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));
vi.mock("@/lib/stripe", () => ({ getStripeClient: vi.fn() }));
vi.mock("@/server/affiliates", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/server/affiliates")>()),
  sendAffiliateEmail: vi.fn(),
}));

const review = (body: unknown) =>
  POST(new Request("http://localhost/api/admin/affiliates/aff-1", { method: "POST", body: JSON.stringify(body) }), {
    params: Promise.resolve({ id: "aff-1" }),
  });

describe("/api/admin/affiliates/[id]", () => {
  const affiliate = { findUnique: vi.fn(), updateMany: vi.fn() };
  const user = { updateMany: vi.fn() };
  const promotionCodes = { create: vi.fn(), update: vi.fn() };

  const pending = { id: "aff-1", user_id: "creator-1", slug: "maya", status: "pending", stripe_promotion_code_id: null };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "admin-1", is_admin: true } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({ affiliate, user } as never);
    vi.mocked(getStripeClient).mockReturnValue({ promotionCodes } as never);
    affiliate.findUnique.mockResolvedValue(pending);
    affiliate.updateMany.mockResolvedValue({ count: 1 });
    promotionCodes.create.mockResolvedValue({ id: "promo_1" });
  });

  it("is admin-only", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", is_admin: false } } as never);

    expect((await review({ action: "approve" })).status).toBe(403);
    expect(affiliate.updateMany).not.toHaveBeenCalled();
  });

  it("approves: creates the Stripe code, grants Pro and emails the creator", async () => {
    const response = await review({ action: "approve" });

    expect(response.status).toBe(200);
    expect(promotionCodes.create).toHaveBeenCalledWith({
      promotion: { type: "coupon", coupon: "creator-20-forever" },
      code: "MAYA",
      metadata: { affiliate_id: "aff-1", user_id: "creator-1" },
    });
    expect(affiliate.updateMany).toHaveBeenCalledWith({
      where: { id: "aff-1", status: "pending" },
      data: expect.objectContaining({ status: "approved", stripe_promotion_code_id: "promo_1", reviewed_by: "admin-1" }),
    });
    expect(user.updateMany).toHaveBeenCalledWith(
      expect.objectContaining({ data: { plan: "pro", subscription_status: "active", subscription_provider: "affiliate" } }),
    );
    expect(sendAffiliateEmail).toHaveBeenCalledWith("creator-1", { event: "approved", slug: "maya" });
  });

  it("deactivates the new code when another admin reviewed first", async () => {
    affiliate.updateMany.mockResolvedValue({ count: 0 });

    const response = await review({ action: "approve" });

    expect(response.status).toBe(409);
    expect(promotionCodes.update).toHaveBeenCalledWith("promo_1", { active: false });
    expect(user.updateMany).not.toHaveBeenCalled();
    expect(sendAffiliateEmail).not.toHaveBeenCalled();
  });

  it("reports Stripe's reason when the code can't be created", async () => {
    promotionCodes.create.mockRejectedValue(new Error("An active promotion code with `code: MAYA` already exists."));

    const response = await review({ action: "approve" });

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: "Stripe rejected the promotion code: An active promotion code with `code: MAYA` already exists.",
    });
    expect(affiliate.updateMany).not.toHaveBeenCalled();
  });

  it("declines: frees the slug and emails the reason", async () => {
    const response = await review({ action: "decline", reason: "  Profile is private  " });

    expect(response.status).toBe(200);
    expect(affiliate.updateMany).toHaveBeenCalledWith({
      where: { id: "aff-1", status: "pending" },
      data: expect.objectContaining({ status: "declined", slug: null, decline_reason: "Profile is private" }),
    });
    expect(sendAffiliateEmail).toHaveBeenCalledWith("creator-1", {
      event: "declined",
      slug: "maya",
      decline_reason: "Profile is private",
    });
    expect(promotionCodes.create).not.toHaveBeenCalled();
  });

  it("revokes: deactivates the code and removes the free Pro", async () => {
    affiliate.findUnique.mockResolvedValue({ ...pending, status: "approved", stripe_promotion_code_id: "promo_1" });

    const response = await review({ action: "revoke" });

    expect(response.status).toBe(200);
    expect(promotionCodes.update).toHaveBeenCalledWith("promo_1", { active: false });
    expect(user.updateMany).toHaveBeenCalledWith({
      where: { id: "creator-1", subscription_provider: "affiliate" },
      data: { plan: "free", subscription_status: "inactive", subscription_provider: null },
    });
  });

  it("rejects unknown actions", async () => {
    expect((await review({ action: "promote" })).status).toBe(400);
  });
});
