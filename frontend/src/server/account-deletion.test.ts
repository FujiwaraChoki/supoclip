import { ACTIVE_STRIPE_SUBSCRIPTION_MESSAGE, prepareAccountDeletion } from "./account-deletion";
import { getPrismaClient } from "@/server/prisma";
import { getStripeClient } from "@/lib/stripe";
import { fetchBackend } from "@/server/backend-api";

vi.mock("@/lib/monetization", () => ({
  monetizationEnabled: true,
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

vi.mock("@/lib/stripe", () => ({
  getStripeClient: vi.fn(),
}));

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(),
}));

function mockPrisma(user: Record<string, unknown> | null, affiliate: Record<string, unknown> | null = null) {
  const deleteMany = vi.fn().mockResolvedValue({ count: 1 });
  vi.mocked(getPrismaClient).mockReturnValue({
    user: { findUnique: vi.fn().mockResolvedValue(user) },
    affiliate: { findUnique: vi.fn().mockResolvedValue(affiliate) },
    source: { deleteMany },
  } as never);
  return { deleteMany };
}

const stripeUser = {
  subscription_provider: "stripe",
  subscription_status: "active",
  stripe_subscription_id: "sub_123",
  subscription_cancel_at: null,
};

describe("prepareAccountDeletion", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(fetchBackend).mockResolvedValue(new Response(null, { status: 200 }));
  });

  it.each(["active", "trialing", "past_due"])("blocks a %s Stripe subscription", async (status) => {
    const { deleteMany } = mockPrisma({ ...stripeUser, subscription_status: status });

    await expect(prepareAccountDeletion("user-1")).rejects.toMatchObject({
      message: ACTIVE_STRIPE_SUBSCRIPTION_MESSAGE,
    });
    expect(deleteMany).not.toHaveBeenCalled();
  });

  it("allows a Stripe subscription already scheduled to cancel", async () => {
    const { deleteMany } = mockPrisma({ ...stripeUser, subscription_cancel_at: new Date() });

    await expect(prepareAccountDeletion("user-1")).resolves.toBeUndefined();
    expect(deleteMany).toHaveBeenCalled();
  });

  it("allows App Store subscribers, whose billing we cannot cancel", async () => {
    const { deleteMany } = mockPrisma({
      ...stripeUser,
      subscription_provider: "apple",
      stripe_subscription_id: null,
    });

    await expect(prepareAccountDeletion("user-1")).resolves.toBeUndefined();
    expect(deleteMany).toHaveBeenCalled();
  });

  it("deletes only sources not shared with another user's task", async () => {
    const { deleteMany } = mockPrisma({ ...stripeUser, subscription_status: "canceled" });

    await prepareAccountDeletion("user-1");

    expect(deleteMany).toHaveBeenCalledWith({
      where: {
        tasks: {
          some: { user_id: "user-1" },
          none: { user_id: { not: "user-1" } },
        },
      },
    });
  });

  it("frees an approved creator's code in Stripe before the account is deleted", async () => {
    const update = vi.fn().mockResolvedValue({});
    vi.mocked(getStripeClient).mockReturnValue({ promotionCodes: { update } } as never);
    const { deleteMany } = mockPrisma(null, { stripe_promotion_code_id: "promo_1" });

    await expect(prepareAccountDeletion("user-1")).resolves.toBeUndefined();
    expect(update).toHaveBeenCalledWith("promo_1", { active: false });
    expect(deleteMany).toHaveBeenCalled();
  });

  it("still deletes the account when Stripe can't deactivate the code", async () => {
    vi.mocked(getStripeClient).mockImplementation(() => {
      throw new Error("STRIPE_SECRET_KEY is not configured");
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { deleteMany } = mockPrisma(null, { stripe_promotion_code_id: "promo_1" });

    await expect(prepareAccountDeletion("user-1")).resolves.toBeUndefined();
    expect(deleteMany).toHaveBeenCalled();
  });

  it("revokes connected social accounts before the account is deleted", async () => {
    const { deleteMany } = mockPrisma(null);

    await expect(prepareAccountDeletion("user-1")).resolves.toBeUndefined();
    expect(fetchBackend).toHaveBeenCalledWith("/social/connections", {
      method: "DELETE",
      userId: "user-1",
    });
    expect(deleteMany).toHaveBeenCalled();
  });

  it("still deletes the account when social revocation fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { deleteMany } = mockPrisma(null);
    vi.mocked(fetchBackend).mockRejectedValueOnce(new Error("backend down"));
    await expect(prepareAccountDeletion("user-1")).resolves.toBeUndefined();

    vi.mocked(fetchBackend).mockResolvedValueOnce(new Response(null, { status: 503 }));
    await expect(prepareAccountDeletion("user-1")).resolves.toBeUndefined();
    expect(deleteMany).toHaveBeenCalledTimes(2);
  });

  it("does not revoke social accounts when deletion is blocked", async () => {
    mockPrisma(stripeUser);

    await expect(prepareAccountDeletion("user-1")).rejects.toBeTruthy();
    expect(fetchBackend).not.toHaveBeenCalled();
  });
});
