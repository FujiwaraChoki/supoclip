// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import type Stripe from "stripe";

const prismaMock = {
  team: {
    findUnique: vi.fn(),
    findFirst: vi.fn(),
    findMany: vi.fn(),
    update: vi.fn(),
  },
  user: {
    findUnique: vi.fn(),
    findFirst: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  account: { findFirst: vi.fn() },
  stripeAccountMigration: { findFirst: vi.fn() },
  stripeWebhookEvent: { create: vi.fn(), update: vi.fn() },
};

const stripeClientMock = {
  subscriptions: { retrieve: vi.fn(), cancel: vi.fn() },
  webhooks: { constructEvent: vi.fn() },
};

vi.mock("@/server/prisma", () => ({ getPrismaClient: () => prismaMock }));
vi.mock("@/lib/stripe", () => ({
  getStripeClient: () => stripeClientMock,
  getServerStripeClient: () => stripeClientMock,
}));
vi.mock("@/lib/stripe-accounts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/stripe-accounts")>();
  return {
    ...actual,
    stripeAccountsEnabled: () => false,
    stripeAccountContext: () => ({ accountId: "default", client: stripeClientMock }),
    stripePriceEnvPrefix: () => "STRIPE_",
  };
});
vi.mock("@/server/backend-api", () => ({ fetchBackend: vi.fn().mockResolvedValue(new Response("{}", { status: 200 })) }));
vi.mock("@/lib/monetization", () => ({ monetizationEnabled: true }));

import { dispatchBillingEvent } from "./stripe-billing-webhook";
import { prepareAccountDeletion } from "./account-deletion";

describe("Stripe webhook Team vs Personal isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.STRIPE_TEAM_PRICE_ID = "price_team_base_mo";
    process.env.STRIPE_TEAM_SEAT_PRICE_ID = "price_team_seat_mo";
    process.env.STRIPE_PRO_PRICE_ID = "price_pro_mo";
    process.env.NEXT_PUBLIC_TEAM_INCLUDED_SEATS = "3";
  });

  it("routes Team checkout & subscription webhooks solely to teams table without touching users", async () => {
    const teamSub = {
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      metadata: { plan: "team", teamId: "team-1" },
      cancel_at_period_end: false,
      cancel_at: null,
      items: {
        data: [
          { price: { id: "price_team_base_mo" }, quantity: 1, current_period_start: 1700000000, current_period_end: 1702592000 },
          { price: { id: "price_team_seat_mo" }, quantity: 2 },
        ],
      },
    } as unknown as Stripe.Subscription;

    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      stripe_customer_id: "cus_team_1",
      stripe_subscription_id: "sub_team_1",
      subscription_provider: "stripe",
      seat_limit: 3,
    });
    prismaMock.team.update.mockResolvedValue({});
    stripeClientMock.subscriptions.retrieve.mockResolvedValue(teamSub);

    // 1. checkout.session.completed for Team
    await dispatchBillingEvent({
      id: "evt_team_checkout",
      type: "checkout.session.completed",
      data: {
        object: {
          id: "cs_team_1",
          mode: "subscription",
          customer: "cus_team_1",
          subscription: "sub_team_1",
          metadata: { plan: "team", teamId: "team-1", seats: "5" },
        },
      },
    } as unknown as Stripe.Event);

    expect(prismaMock.team.update).toHaveBeenCalled();
    expect(prismaMock.user.update).not.toHaveBeenCalled();

    vi.clearAllMocks();
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      stripe_customer_id: "cus_team_1",
      stripe_subscription_id: "sub_team_1",
      subscription_provider: "stripe",
      seat_limit: 5,
    });
    prismaMock.team.update.mockResolvedValue({});

    // 2. customer.subscription.updated for Team
    await dispatchBillingEvent({
      id: "evt_team_update",
      type: "customer.subscription.updated",
      data: { object: teamSub },
    } as unknown as Stripe.Event);

    expect(prismaMock.team.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: "team-1" },
        data: expect.objectContaining({ plan: "team", seat_limit: 5 }),
      }),
    );
    expect(prismaMock.user.update).not.toHaveBeenCalled();

    vi.clearAllMocks();
    prismaMock.team.findFirst.mockResolvedValue({
      id: "team-1",
      stripe_customer_id: "cus_team_1",
      stripe_subscription_id: "sub_team_1",
      subscription_provider: "stripe",
    });
    prismaMock.team.update.mockResolvedValue({});

    // 3. invoice.payment_failed for Team
    await dispatchBillingEvent({
      id: "evt_team_fail",
      type: "invoice.payment_failed",
      data: {
        object: {
          id: "in_team_fail",
          customer: "cus_team_1",
          status: "open",
        },
      },
    } as unknown as Stripe.Event);

    expect(prismaMock.team.update).toHaveBeenCalledWith({
      where: { id: "team-1" },
      data: { subscription_status: "past_due" },
    });
    expect(prismaMock.user.update).not.toHaveBeenCalled();
  });
});

describe("Account deletion protection for team owners", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("blocks deleting an account that owns a team with an active subscription", async () => {
    prismaMock.user.findUnique.mockResolvedValue({
      id: "u-owner",
      email: "owner@example.com",
      stripe_customer_id: null,
      stripe_subscription_id: null,
      subscription_provider: null,
    });
    prismaMock.team.findMany.mockResolvedValue([
      {
        id: "t1",
        name: "Alpha Crew",
        stripe_subscription_id: "sub_team_active",
        subscription_status: "active",
        subscription_cancel_at: null,
        _count: { members: 1 },
      },
    ]);

    await expect(prepareAccountDeletion("u-owner")).rejects.toThrow(/Alpha Crew/);
  });

  it("blocks deleting an account that owns a team with other members", async () => {
    prismaMock.user.findUnique.mockResolvedValue({
      id: "u-owner",
      email: "owner@example.com",
      stripe_customer_id: null,
      stripe_subscription_id: null,
      subscription_provider: null,
    });
    prismaMock.team.findMany.mockResolvedValue([
      {
        id: "t1",
        name: "Alpha Crew",
        stripe_subscription_id: null,
        subscription_status: "inactive",
        subscription_cancel_at: null,
        _count: { members: 3 },
      },
    ]);

    await expect(prepareAccountDeletion("u-owner")).rejects.toThrow(/Transfer ownership/);
  });
});
