import { beforeEach, describe, expect, it, vi } from "vitest";
import type Stripe from "stripe";

const prismaMock = {
  team: {
    findUnique: vi.fn(),
    findFirst: vi.fn(),
    update: vi.fn(),
  },
  teamMember: {
    findUnique: vi.fn(),
    count: vi.fn(),
  },
  teamInvitation: {
    count: vi.fn(),
  },
  user: {
    findUnique: vi.fn(),
  },
};

const stripeClientMock = {
  customers: {
    create: vi.fn(),
    retrieve: vi.fn(),
  },
  checkout: {
    sessions: {
      create: vi.fn(),
    },
  },
  subscriptions: {
    list: vi.fn(),
    retrieve: vi.fn(),
    update: vi.fn(),
  },
  subscriptionItems: {
    create: vi.fn(),
    update: vi.fn(),
    del: vi.fn(),
  },
  billingPortal: {
    sessions: {
      create: vi.fn(),
    },
  },
};

vi.mock("@/server/prisma", () => ({
  getPrismaClient: () => prismaMock,
}));

vi.mock("@/lib/stripe", () => ({
  getStripeClient: () => stripeClientMock,
  getServerStripeClient: () => stripeClientMock,
  getStripeForAccount: () => stripeClientMock,
  resolveStripeAccount: () => "destination",
}));

vi.mock("@/lib/stripe-accounts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/stripe-accounts")>();
  return {
    ...actual,
    checkoutStripeAccountId: () => "acct_dest",
    runWithStripeAccount: async (_ctx: unknown, fn: () => unknown) => fn(),
    sourceStripeAccountId: () => "acct_src",
    stripeAccountContext: () => ({ accountId: "acct_dest", client: stripeClientMock }),
    stripeAccountRole: () => "source",
    stripeAccountsEnabled: () => false,
    stripePriceEnvPrefix: () => "STRIPE_",
    verifiedStripeClient: async () => stripeClientMock,
  };
});

vi.mock("@/lib/monetization", () => ({
  monetizationEnabled: true,
}));

import {
  createTeamCheckoutSession,
  handleTeamCheckoutCompleted,
  handleTeamInvoicePaymentFailed,
  handleTeamSubscriptionDeleted,
  syncTeamSubscriptionState,
  updateTeamSeats,
} from "./stripe-team-billing";

describe("stripe-team-billing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.STRIPE_TEAM_PRICE_ID = "price_team_base_mo";
    process.env.STRIPE_TEAM_SEAT_PRICE_ID = "price_team_seat_mo";
    process.env.STRIPE_TEAM_ANNUAL_PRICE_ID = "price_team_base_yr";
    process.env.STRIPE_TEAM_SEAT_ANNUAL_PRICE_ID = "price_team_seat_yr";
    process.env.NEXT_PUBLIC_TEAM_INCLUDED_SEATS = "3";
  });

  it("builds Stripe checkout with base price only for 3 seats and base + extra seats for 5 seats", async () => {
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      name: "Clip Studio",
      owner_id: "user-1",
      stripe_customer_id: "cus_team_1",
      stripe_account_id: "acct_dest",
      stripe_subscription_id: null,
      subscription_status: "inactive",
      seat_limit: 3,
    });
    stripeClientMock.subscriptions.list.mockResolvedValue({ data: [] });
    stripeClientMock.checkout.sessions.create.mockResolvedValue({
      url: "https://checkout.stripe.test/team",
    });

    await createTeamCheckoutSession({
      teamId: "team-1",
      actorUserId: "user-1",
      ownerEmail: "owner@example.com",
      ownerName: "Owner",
      requestedSeats: 3,
      interval: "month",
    });

    expect(stripeClientMock.checkout.sessions.create).toHaveBeenLastCalledWith(
      expect.objectContaining({
        customer: "cus_team_1",
        line_items: [{ price: "price_team_base_mo", quantity: 1 }],
        metadata: expect.objectContaining({
          plan: "team",
          teamId: "team-1",
          seats: "3",
        }),
      }),
    );

    await createTeamCheckoutSession({
      teamId: "team-1",
      actorUserId: "user-1",
      ownerEmail: "owner@example.com",
      ownerName: "Owner",
      requestedSeats: 5,
      interval: "year",
    });

    expect(stripeClientMock.checkout.sessions.create).toHaveBeenLastCalledWith(
      expect.objectContaining({
        line_items: [
          { price: "price_team_base_yr", quantity: 1 },
          { price: "price_team_seat_yr", quantity: 2 },
        ],
        metadata: expect.objectContaining({
          plan: "team",
          teamId: "team-1",
          seats: "5",
          interval: "year",
        }),
      }),
    );
  });

  it("blocks duplicate checkout when team already has an active Stripe subscription", async () => {
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      name: "Clip Studio",
      owner_id: "user-1",
      stripe_customer_id: "cus_team_1",
      stripe_account_id: "acct_dest",
      stripe_subscription_id: "sub_team_1",
      subscription_status: "active",
      seat_limit: 3,
    });
    stripeClientMock.subscriptions.list.mockResolvedValue({
      data: [{ id: "sub_team_1", status: "active" }],
    });

    await expect(
      createTeamCheckoutSession({
        teamId: "team-1",
        actorUserId: "user-1",
        ownerEmail: "owner@example.com",
        requestedSeats: 3,
        interval: "month",
      }),
    ).rejects.toMatchObject({ code: "already_subscribed", status: 409 });
  });

  it("adds, updates, and deletes the extra-seat Stripe subscription item when changing seats", async () => {
    prismaMock.teamMember.count.mockResolvedValue(2);
    prismaMock.teamInvitation.count.mockResolvedValue(0);
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      stripe_subscription_id: "sub_team_1",
      stripe_account_id: "acct_dest",
      subscription_status: "active",
      seat_limit: 3,
    });
    prismaMock.team.update.mockResolvedValue({});

    // 3 -> 5: adds extra-seat item with qty 2
    stripeClientMock.subscriptions.retrieve.mockResolvedValueOnce({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      items: { data: [{ id: "si_base", price: { id: "price_team_base_mo" }, quantity: 1 }] },
    });
    stripeClientMock.subscriptions.update.mockResolvedValueOnce({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      items: {
        data: [
          { id: "si_base", price: { id: "price_team_base_mo" }, quantity: 1 },
          { id: "si_seat", price: { id: "price_team_seat_mo" }, quantity: 2 },
        ],
      },
    });
    const added = await updateTeamSeats({ teamId: "team-1", newSeats: 5, monetizationEnabled: true });
    expect(added).toEqual({ seatLimit: 5, prorationApplied: true });
    expect(stripeClientMock.subscriptions.update).toHaveBeenLastCalledWith("sub_team_1", {
      items: [{ price: "price_team_seat_mo", quantity: 2 }],
      proration_behavior: "create_prorations",
    });

    // 5 -> 4: updates extra-seat item to qty 1
    stripeClientMock.subscriptions.retrieve.mockResolvedValueOnce({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      items: {
        data: [
          { id: "si_base", price: { id: "price_team_base_mo" }, quantity: 1 },
          { id: "si_seat", price: { id: "price_team_seat_mo" }, quantity: 2 },
        ],
      },
    });
    stripeClientMock.subscriptions.update.mockResolvedValueOnce({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      items: {
        data: [
          { id: "si_base", price: { id: "price_team_base_mo" }, quantity: 1 },
          { id: "si_seat", price: { id: "price_team_seat_mo" }, quantity: 1 },
        ],
      },
    });
    const lowered = await updateTeamSeats({ teamId: "team-1", newSeats: 4, monetizationEnabled: true });
    expect(lowered).toEqual({ seatLimit: 4, prorationApplied: true });
    expect(stripeClientMock.subscriptions.update).toHaveBeenLastCalledWith("sub_team_1", {
      items: [{ id: "si_seat", quantity: 1 }],
      proration_behavior: "create_prorations",
    });

    // 4 -> 3: deletes extra-seat item
    stripeClientMock.subscriptions.retrieve.mockResolvedValueOnce({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      items: {
        data: [
          { id: "si_base", price: { id: "price_team_base_mo" }, quantity: 1 },
          { id: "si_seat", price: { id: "price_team_seat_mo" }, quantity: 1 },
        ],
      },
    });
    stripeClientMock.subscriptions.update.mockResolvedValueOnce({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      items: {
        data: [{ id: "si_base", price: { id: "price_team_base_mo" }, quantity: 1 }],
      },
    });
    const baseOnly = await updateTeamSeats({ teamId: "team-1", newSeats: 3, monetizationEnabled: true });
    expect(baseOnly).toEqual({ seatLimit: 3, prorationApplied: true });
    expect(stripeClientMock.subscriptions.update).toHaveBeenLastCalledWith("sub_team_1", {
      items: [{ id: "si_seat", deleted: true }],
      proration_behavior: "create_prorations",
    });
  });

  it("blocks reducing seats below occupied members + pending invitations", async () => {
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      stripe_subscription_id: "sub_team_1",
      stripe_account_id: "acct_dest",
      subscription_status: "active",
      seat_limit: 6,
    });
    prismaMock.teamMember.count.mockResolvedValue(4);
    prismaMock.teamInvitation.count.mockResolvedValue(1);

    await expect(
      updateTeamSeats({ teamId: "team-1", newSeats: 4, monetizationEnabled: true }),
    ).rejects.toMatchObject({
      code: "seats_in_use",
      status: 409,
    });
  });

  it("handles team webhooks cleanly and computes seat_limit from subscription items", async () => {
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      stripe_customer_id: "cus_team_1",
      stripe_subscription_id: "sub_team_1",
      stripe_account_id: "acct_dest",
      seat_limit: 3,
    });
    prismaMock.team.update.mockResolvedValue({});

    const subscription = {
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      metadata: { plan: "team", teamId: "team-1" },
      cancel_at_period_end: false,
      cancel_at: null,
      items: {
        data: [
          {
            price: { id: "price_team_base_mo" },
            quantity: 1,
            current_period_start: 1700000000,
            current_period_end: 1702592000,
          },
          {
            price: { id: "price_team_seat_mo" },
            quantity: 3,
          },
        ],
      },
    } as unknown as Stripe.Subscription;

    const handled = await syncTeamSubscriptionState(subscription);
    expect(handled).toBe(true);
    expect(prismaMock.team.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: "team-1" },
        data: expect.objectContaining({
          plan: "team",
          subscription_status: "active",
          subscription_provider: "stripe",
          seat_limit: 6,
        }),
      }),
    );

    // Checkout completion activates team subscription
    stripeClientMock.subscriptions.retrieve.mockResolvedValueOnce(subscription);
    const checkoutHandled = await handleTeamCheckoutCompleted({
      id: "cs_test_1",
      mode: "subscription",
      customer: "cus_team_1",
      subscription: "sub_team_1",
      metadata: { plan: "team", teamId: "team-1", seats: "6" },
    } as unknown as Stripe.Checkout.Session);
    expect(checkoutHandled).toBe(true);

    // Invoice payment failure marks team past_due
    prismaMock.team.findFirst.mockResolvedValueOnce({
      id: "team-1",
      stripe_customer_id: "cus_team_1",
      stripe_subscription_id: "sub_team_1",
      subscription_provider: "stripe",
    });
    const failedHandled = await handleTeamInvoicePaymentFailed({
      customer: "cus_team_1",
      parent: { subscription_details: { subscription: "sub_team_1" } },
    } as unknown as Stripe.Invoice);
    expect(failedHandled).toBe(true);
    expect(prismaMock.team.update).toHaveBeenLastCalledWith({
      where: { id: "team-1" },
      data: { subscription_status: "past_due" },
    });

    // Subscription deletion resets team plan to free & cancels status
    const deletedHandled = await handleTeamSubscriptionDeleted(subscription);
    expect(deletedHandled).toBe(true);
    expect(prismaMock.team.update).toHaveBeenLastCalledWith(
      expect.objectContaining({
        where: { id: "team-1" },
        data: expect.objectContaining({
          plan: "free",
          subscription_status: "canceled",
          seat_limit: 3,
        }),
      }),
    );
  });
});
