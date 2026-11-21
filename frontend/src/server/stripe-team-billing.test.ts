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
  $queryRaw: vi.fn(),
  $transaction: vi.fn(),
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
  handleTeamSubscriptionUpdated,
  syncTeamSubscriptionState,
  teamHasBillableStripeSubscription,
  updateTeamSeats,
} from "./stripe-team-billing";

describe("stripe-team-billing", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    prismaMock.$transaction.mockImplementation(async (fn: (tx: typeof prismaMock) => unknown) => fn(prismaMock));
    prismaMock.teamMember.count.mockResolvedValue(1);
    prismaMock.teamInvitation.count.mockResolvedValue(0);
    delete process.env.STRIPE_TEAM_LEGACY_PRICE_IDS;
    delete process.env.STRIPE_TEAM_SEAT_LEGACY_PRICE_IDS;
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
    // The personal checkout handler binds a session to `metadata.userId`.
    expect(stripeClientMock.checkout.sessions.create.mock.lastCall?.[0].metadata).not.toHaveProperty("userId");

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

  it("charges for every seat a team already fills when it subscribes again", async () => {
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      name: "Clip Studio",
      owner_id: "user-1",
      stripe_customer_id: "cus_team_1",
      stripe_account_id: "acct_dest",
      stripe_subscription_id: null,
      subscription_status: "canceled",
      seat_limit: 3,
    });
    // Seven members and one pending invitation stayed after the cancellation.
    prismaMock.teamMember.count.mockResolvedValue(7);
    prismaMock.teamInvitation.count.mockResolvedValue(1);
    stripeClientMock.subscriptions.list.mockResolvedValue({ data: [] });
    stripeClientMock.checkout.sessions.create.mockResolvedValue({ url: "https://checkout.stripe.test/team" });

    await createTeamCheckoutSession({
      teamId: "team-1",
      actorUserId: "user-1",
      ownerEmail: "owner@example.com",
      requestedSeats: 3,
      interval: "month",
    });

    expect(stripeClientMock.checkout.sessions.create).toHaveBeenLastCalledWith(
      expect.objectContaining({
        line_items: [
          { price: "price_team_base_mo", quantity: 1 },
          { price: "price_team_seat_mo", quantity: 5 },
        ],
        metadata: expect.objectContaining({ seats: "8" }),
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
    // More seats are invoiced and charged in the same call; a failed charge changes nothing.
    expect(stripeClientMock.subscriptions.update).toHaveBeenLastCalledWith("sub_team_1", {
      items: [{ price: "price_team_seat_mo", quantity: 2 }],
      proration_behavior: "always_invoice",
      payment_behavior: "error_if_incomplete",
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
    prismaMock.team.findUnique.mockResolvedValueOnce({
      id: "team-1",
      stripe_subscription_id: "sub_team_1",
      stripe_account_id: "acct_dest",
      subscription_status: "active",
      seat_limit: 5,
    });
    const lowered = await updateTeamSeats({ teamId: "team-1", newSeats: 4, monetizationEnabled: true });
    // Fewer seats give no credit: the minutes they added may already be spent.
    expect(lowered).toEqual({ seatLimit: 4, prorationApplied: false });
    expect(stripeClientMock.subscriptions.update).toHaveBeenLastCalledWith("sub_team_1", {
      items: [{ id: "si_seat", quantity: 1 }],
      proration_behavior: "none",
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
    prismaMock.team.findUnique.mockResolvedValueOnce({
      id: "team-1",
      stripe_subscription_id: "sub_team_1",
      stripe_account_id: "acct_dest",
      subscription_status: "active",
      seat_limit: 4,
    });
    const baseOnly = await updateTeamSeats({ teamId: "team-1", newSeats: 3, monetizationEnabled: true });
    expect(baseOnly).toEqual({ seatLimit: 3, prorationApplied: false });
    expect(stripeClientMock.subscriptions.update).toHaveBeenLastCalledWith("sub_team_1", {
      items: [{ id: "si_seat", deleted: true }],
      proration_behavior: "none",
    });
  });

  it("adds no seats when the charge for them fails", async () => {
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      stripe_subscription_id: "sub_team_1",
      stripe_account_id: "acct_dest",
      subscription_status: "active",
      seat_limit: 3,
    });
    stripeClientMock.subscriptions.retrieve.mockResolvedValueOnce({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      items: { data: [{ id: "si_base", price: { id: "price_team_base_mo" }, quantity: 1 }] },
    });
    stripeClientMock.subscriptions.update.mockRejectedValueOnce(
      Object.assign(new Error("Your card was declined."), { type: "StripeCardError", statusCode: 402 }),
    );

    await expect(
      updateTeamSeats({ teamId: "team-1", newSeats: 20, monetizationEnabled: true }),
    ).rejects.toMatchObject({ code: "payment_failed", status: 402 });
    expect(prismaMock.team.update).not.toHaveBeenCalled();
  });

  it("puts the seat limit back when Stripe refuses a reduction", async () => {
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      stripe_subscription_id: "sub_team_1",
      stripe_account_id: "acct_dest",
      subscription_status: "active",
      seat_limit: 6,
    });
    prismaMock.team.update.mockResolvedValue({});
    stripeClientMock.subscriptions.retrieve.mockRejectedValueOnce(new Error("Stripe is unavailable"));

    await expect(
      updateTeamSeats({ teamId: "team-1", newSeats: 4, monetizationEnabled: true }),
    ).rejects.toThrow("Stripe is unavailable");
    // Lowered under the lock before Stripe was called, then restored.
    expect(prismaMock.team.update.mock.calls.map(([call]) => call.data)).toEqual([
      { seat_limit: 4 },
      { seat_limit: 6 },
    ]);
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

  const baseItem = {
    price: { id: "price_team_base_mo" },
    quantity: 1,
    current_period_start: 1700000000,
    current_period_end: 1702592000,
  };
  const paidTeam = {
    id: "team-1",
    plan: "team",
    subscription_status: "active",
    stripe_customer_id: "cus_team_1",
    stripe_subscription_id: "sub_team_current",
    stripe_account_id: "acct_dest",
    seat_limit: 5,
  };

  it("gives no Team plan to a subscription without a Team base price", async () => {
    prismaMock.team.findUnique.mockResolvedValue({ ...paidTeam, stripe_subscription_id: "sub_team_1" });
    prismaMock.team.update.mockResolvedValue({});

    // The team's own subscription, switched to another product in the billing portal.
    await syncTeamSubscriptionState({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      metadata: { plan: "team", teamId: "team-1" },
      items: { data: [{ ...baseItem, price: { id: "price_solo_pro" } }] },
    } as unknown as Stripe.Subscription);

    expect(prismaMock.team.update).toHaveBeenCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ plan: "free", seat_limit: 3 }) }),
    );
  });

  it("keeps a team on an earlier price entitled, with its seats", async () => {
    process.env.STRIPE_TEAM_LEGACY_PRICE_IDS = "price_team_base_old";
    process.env.STRIPE_TEAM_SEAT_LEGACY_PRICE_IDS = "price_team_seat_old";
    prismaMock.team.findUnique.mockResolvedValue({ ...paidTeam, stripe_subscription_id: "sub_team_1" });
    prismaMock.team.update.mockResolvedValue({});

    await syncTeamSubscriptionState({
      id: "sub_team_1",
      customer: "cus_team_1",
      status: "active",
      metadata: { plan: "team", teamId: "team-1" },
      items: {
        data: [
          { ...baseItem, price: { id: "price_team_base_old" } },
          { price: { id: "price_team_seat_old" }, quantity: 4 },
        ],
      },
    } as unknown as Stripe.Subscription);

    expect(prismaMock.team.update).toHaveBeenCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ plan: "team", seat_limit: 7 }) }),
    );
  });

  it("ignores a late event about a subscription the team has replaced", async () => {
    prismaMock.team.findUnique.mockResolvedValue(paidTeam);
    const cancelled = {
      id: "sub_team_old",
      customer: "cus_team_1",
      status: "canceled",
      metadata: { plan: "team", teamId: "team-1" },
      items: { data: [baseItem] },
    } as unknown as Stripe.Subscription;
    stripeClientMock.subscriptions.retrieve.mockResolvedValue(cancelled);

    expect(await handleTeamSubscriptionUpdated(cancelled)).toBe(true);
    expect(prismaMock.team.update).not.toHaveBeenCalled();
  });

  it("applies the subscription as it is now, not the event's older copy", async () => {
    prismaMock.team.findUnique.mockResolvedValue(paidTeam);
    prismaMock.team.update.mockResolvedValue({});
    const snapshot = {
      id: "sub_team_current",
      customer: "cus_team_1",
      status: "active",
      metadata: { plan: "team", teamId: "team-1" },
      items: { data: [baseItem, { price: { id: "price_team_seat_mo" }, quantity: 1 }] },
    } as unknown as Stripe.Subscription;
    stripeClientMock.subscriptions.retrieve.mockResolvedValue({
      ...snapshot,
      items: { data: [baseItem, { price: { id: "price_team_seat_mo" }, quantity: 6 }] },
    });

    expect(await handleTeamSubscriptionUpdated(snapshot)).toBe(true);
    expect(stripeClientMock.subscriptions.retrieve).toHaveBeenCalledWith("sub_team_current");
    expect(prismaMock.team.update).toHaveBeenCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ plan: "team", seat_limit: 9 }) }),
    );
  });

  it("asks Stripe, not the team row, whether a subscription will charge again", async () => {
    prismaMock.team.findUnique.mockResolvedValue({ stripe_customer_id: "cus_team_1" });
    stripeClientMock.subscriptions.list.mockImplementation(async ({ status }: { status: string }) => ({
      data: status === "past_due" ? [{ id: "sub_team_1", cancel_at_period_end: false, cancel_at: null }] : [],
    }));
    expect(await teamHasBillableStripeSubscription("team-1")).toBe(true);

    // Already set to end: it will not charge again.
    stripeClientMock.subscriptions.list.mockImplementation(async ({ status }: { status: string }) => ({
      data: status === "active" ? [{ id: "sub_team_1", cancel_at_period_end: true, cancel_at: 1702592000 }] : [],
    }));
    expect(await teamHasBillableStripeSubscription("team-1")).toBe(false);

    prismaMock.team.findUnique.mockResolvedValue({ stripe_customer_id: null });
    expect(await teamHasBillableStripeSubscription("team-1")).toBe(false);
  });
});
