import { ACTIVE_STRIPE_SUBSCRIPTION_MESSAGE, prepareAccountDeletion } from "./account-deletion";
import { getPrismaClient } from "@/server/prisma";

vi.mock("@/lib/monetization", () => ({
  monetizationEnabled: true,
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

function mockPrisma(user: Record<string, unknown> | null) {
  const deleteMany = vi.fn().mockResolvedValue({ count: 1 });
  vi.mocked(getPrismaClient).mockReturnValue({
    user: { findUnique: vi.fn().mockResolvedValue(user) },
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
});
