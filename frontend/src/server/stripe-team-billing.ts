import type Stripe from "stripe";
import type { BillingInterval } from "@/lib/billing-plans";
import { getTeamBillingConfig, MAX_TEAM_SEATS } from "@/lib/billing-plans";
import {
  accountPortalConfiguration,
  checkoutStripeAccountId,
  runWithStripeAccount,
  sourceStripeAccountId,
  stripeAccountContext,
  stripeAccountsEnabled,
  verifiedStripeClient,
} from "@/lib/stripe-accounts";
import { destinationCheckoutTax } from "@/lib/stripe-billing-safety";
import {
  CHECKOUT_BLOCKING_STATUSES,
  EXISTING_SUBSCRIPTION_NEEDS_PAYMENT,
  EXISTING_SUBSCRIPTION_NEEDS_PAYMENT_MESSAGE,
  subscriptionNeedsPayment,
} from "@/lib/subscription-status";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import {
  getTeamIntervalForStripePrice,
  getTeamSeatsFromSubscription,
  getTeamStripePrices,
  hasTeamBasePrice,
  isTeamBaseStripePrice,
  isTeamSeatStripePrice,
  isTeamStripePrice,
} from "@/server/billing-plans";
import { countOccupiedSeats, TeamError } from "@/server/teams";

const ENTITLED_SUBSCRIPTION_STATUSES = new Set(["active", "trialing", "past_due"]);
const BILLABLE_SUBSCRIPTION_STATUSES = new Set(["active", "trialing", "past_due", "unpaid"]);

function toDate(unixSeconds: number | null | undefined): Date | null {
  if (!unixSeconds) return null;
  return new Date(unixSeconds * 1000);
}

function customerIdOf(
  customer: string | { id: string } | null | undefined,
): string | null {
  if (!customer) return null;
  return typeof customer === "string" ? customer : customer.id;
}

function getSubscriptionPeriod(subscription: Stripe.Subscription): {
  currentPeriodStart: number | null;
  currentPeriodEnd: number | null;
} {
  const starts = (subscription.items?.data ?? [])
    .map((item) => item.current_period_start)
    .filter((value): value is number => typeof value === "number");
  const ends = (subscription.items?.data ?? [])
    .map((item) => item.current_period_end)
    .filter((value): value is number => typeof value === "number");
  return {
    currentPeriodStart: starts.length > 0 ? Math.min(...starts) : null,
    currentPeriodEnd: ends.length > 0 ? Math.max(...ends) : null,
  };
}

/** Whether a Stripe checkout session is for a Team subscription. */
export function isTeamCheckoutSession(session: Stripe.Checkout.Session): boolean {
  return Boolean(session.metadata?.teamId || session.metadata?.plan === "team");
}

/** Whether a Stripe subscription belongs to a Team plan (by metadata or price). */
export function isTeamStripeSubscription(subscription: Stripe.Subscription): boolean {
  if (subscription.metadata?.teamId || subscription.metadata?.plan === "team") {
    return true;
  }
  return (subscription.items?.data ?? []).some((item) =>
    isTeamStripePrice(item.price?.id),
  );
}

/** Run a team billing operation inside the right Stripe account context. */
export async function withTeamBillingAccount<T>(
  teamId: string,
  callback: () => Promise<T>,
): Promise<T> {
  if (!stripeAccountsEnabled()) return callback();
  const prisma = getPrismaClient();
  const team = await prisma.team.findUniqueOrThrow({
    where: { id: teamId },
    select: { stripe_account_id: true, stripe_customer_id: true },
  });
  const accountId =
    team.stripe_account_id ||
    (team.stripe_customer_id
      ? sourceStripeAccountId()
      : checkoutStripeAccountId());
  const client = await verifiedStripeClient(accountId);
  return runWithStripeAccount(
    {
      accountId,
      client,
      customerId: team.stripe_customer_id || undefined,
    },
    callback,
  );
}

export async function findTeamForStripeCustomer(
  customerId: string | null | undefined,
  teamIdHint?: string | null,
) {
  const prisma = getPrismaClient();
  if (!prisma.team) return null;
  if (teamIdHint) {
    const byId = await prisma.team.findUnique({ where: { id: teamIdHint } });
    if (byId) return byId;
  }
  if (!customerId) return null;
  return prisma.team.findFirst({ where: { stripe_customer_id: customerId } });
}

export async function syncTeamSubscriptionState(
  subscription: Stripe.Subscription,
  options?: { teamIdHint?: string | null },
): Promise<boolean> {
  const customerId = customerIdOf(subscription.customer);
  const teamIdHint = options?.teamIdHint || subscription.metadata?.teamId || null;
  const team = await findTeamForStripeCustomer(customerId, teamIdHint);
  if (!team) return false;

  const accountId = stripeAccountContext()?.accountId;
  if (
    stripeAccountsEnabled() &&
    accountId &&
    team.stripe_account_id &&
    team.stripe_account_id !== accountId
  ) {
    return false;
  }

  const includedSeats = getTeamBillingConfig().includedSeats;
  const { currentPeriodStart, currentPeriodEnd } = getSubscriptionPeriod(subscription);
  const cancelAt =
    subscription.cancel_at ??
    (subscription.cancel_at_period_end ? currentPeriodEnd : null);
  const entitled =
    ENTITLED_SUBSCRIPTION_STATUSES.has(subscription.status) && hasTeamBasePrice(subscription);

  // An event about a subscription the team has already replaced (a late or
  // retried delivery for the one it cancelled) must not take the plan away
  // from the subscription that is paying now.
  const teamIsEntitled =
    team.plan === "team" && ENTITLED_SUBSCRIPTION_STATUSES.has(team.subscription_status);
  if (
    !entitled &&
    teamIsEntitled &&
    team.stripe_subscription_id &&
    team.stripe_subscription_id !== subscription.id
  ) {
    return true;
  }

  const seatLimit = entitled
    ? getTeamSeatsFromSubscription(subscription, includedSeats)
    : includedSeats;

  await getPrismaClient().team.update({
    where: { id: team.id },
    data: {
      plan: entitled ? "team" : "free",
      subscription_status: subscription.status,
      subscription_provider: "stripe",
      ...(customerId ? { stripe_customer_id: customerId } : {}),
      ...(accountId ? { stripe_account_id: accountId } : {}),
      stripe_subscription_id: subscription.id,
      seat_limit: seatLimit,
      billing_period_start: toDate(currentPeriodStart),
      billing_period_end: toDate(currentPeriodEnd),
      subscription_cancel_at: toDate(cancelAt),
      pending_plan: null,
      pending_plan_invoice_id: null,
    },
  });
  return true;
}

/**
 * `customer.subscription.updated` for a team. The event carries the
 * subscription as it was when the event was made, and Stripe does not deliver
 * events in order: applying an older snapshot would undo a newer seat count.
 * The team's state is always taken from the subscription as it is now.
 */
export async function handleTeamSubscriptionUpdated(
  snapshot: Stripe.Subscription,
): Promise<boolean> {
  const team = await findTeamForStripeCustomer(
    customerIdOf(snapshot.customer),
    snapshot.metadata?.teamId || null,
  );
  if (!team) return false;
  const current = await getServerStripeClient().subscriptions.retrieve(snapshot.id);
  return syncTeamSubscriptionState(current, { teamIdHint: team.id });
}

export async function handleTeamCheckoutCompleted(
  session: Stripe.Checkout.Session,
): Promise<boolean> {
  if (session.mode !== "subscription") return false;
  const customerId = session.customer ? String(session.customer) : null;
  const subscriptionId = session.subscription ? String(session.subscription) : null;
  const teamId = session.metadata?.teamId || null;

  const team = await findTeamForStripeCustomer(customerId, teamId);
  if (!team && !isTeamCheckoutSession(session)) return false;
  if (!customerId || !subscriptionId) return Boolean(team);

  const stripe = getServerStripeClient();
  const subscription = await stripe.subscriptions.retrieve(subscriptionId);

  if (team) {
    await getPrismaClient().team.update({
      where: { id: team.id },
      data: {
        stripe_customer_id: customerId,
        stripe_subscription_id: subscriptionId,
        ...(stripeAccountContext()?.accountId
          ? { stripe_account_id: stripeAccountContext()?.accountId }
          : {}),
      },
    });
  }

  return syncTeamSubscriptionState(subscription, { teamIdHint: team?.id ?? teamId });
}

export async function handleTeamSubscriptionDeleted(
  subscription: Stripe.Subscription,
): Promise<boolean> {
  const customerId = customerIdOf(subscription.customer);
  const team = await findTeamForStripeCustomer(
    customerId,
    subscription.metadata?.teamId || null,
  );
  if (!team) return false;

  const accountId = stripeAccountContext()?.accountId;
  if (
    stripeAccountsEnabled() &&
    accountId &&
    team.stripe_account_id &&
    team.stripe_account_id !== accountId
  ) {
    return false;
  }

  // Only clear if this is the team's current subscription (or unset).
  if (team.stripe_subscription_id && team.stripe_subscription_id !== subscription.id) {
    return true;
  }

  await getPrismaClient().team.update({
    where: { id: team.id },
    data: {
      plan: "free",
      subscription_status: "canceled",
      subscription_provider: null,
      stripe_subscription_id: null,
      seat_limit: getTeamBillingConfig().includedSeats,
      billing_period_start: null,
      billing_period_end: null,
      subscription_cancel_at: null,
      pending_plan: null,
      pending_plan_invoice_id: null,
    },
  });
  return true;
}

export async function handleTeamInvoicePaid(invoice: Stripe.Invoice): Promise<boolean> {
  const customerId = customerIdOf(invoice.customer);
  const team = await findTeamForStripeCustomer(customerId);
  if (!team) return false;

  const parentSub = invoice.parent?.subscription_details?.subscription;
  const legacySub = (invoice as Stripe.Invoice & { subscription?: string | Stripe.Subscription | null }).subscription;
  const subId =
    typeof parentSub === "string"
      ? parentSub
      : parentSub && typeof parentSub === "object" && "id" in parentSub
        ? parentSub.id
        : typeof legacySub === "string"
          ? legacySub
          : legacySub && typeof legacySub === "object"
            ? legacySub.id
            : team.stripe_subscription_id;
  if (!subId) return true;

  const subscription = await getServerStripeClient().subscriptions.retrieve(subId);
  await syncTeamSubscriptionState(subscription, { teamIdHint: team.id });
  return true;
}

export async function handleTeamInvoicePaymentFailed(
  invoice: Stripe.Invoice,
): Promise<boolean> {
  const customerId = customerIdOf(invoice.customer);
  const team = await findTeamForStripeCustomer(customerId);
  if (!team) return false;
  if (invoice.status === "void") return true;

  if (team.subscription_provider === "stripe" && team.stripe_subscription_id) {
    await getPrismaClient().team.update({
      where: { id: team.id },
      data: {
        subscription_status: "past_due",
      },
    });
  }
  return true;
}

export async function createTeamCheckoutSession({
  teamId,
  ownerEmail,
  ownerName,
  actorUserId,
  interval,
  requestedSeats,
}: {
  teamId: string;
  ownerEmail: string;
  ownerName?: string | null;
  actorUserId: string;
  interval: BillingInterval;
  requestedSeats?: number;
}): Promise<{ url: string }> {
  const prisma = getPrismaClient();
  const team = await prisma.team.findUnique({ where: { id: teamId } });
  if (!team) throw new TeamError("Team not found", 404, "team_not_found");

  const config = getTeamBillingConfig();
  // A team that kept its members after a cancellation pays for all of them.
  const { occupiedSeats } = await countOccupiedSeats(prisma, teamId);
  if (occupiedSeats > MAX_TEAM_SEATS) {
    throw new TeamError(
      `Teams can have up to ${MAX_TEAM_SEATS} seats online. Contact us for larger teams.`,
      400,
      "max_seats",
    );
  }
  const seats = Math.min(
    MAX_TEAM_SEATS,
    Math.max(
      config.includedSeats,
      occupiedSeats,
      Math.floor(requestedSeats ?? team.seat_limit ?? config.includedSeats),
    ),
  );
  const extraSeats = Math.max(0, seats - config.includedSeats);

  const prices = getTeamStripePrices();
  const basePriceId = prices.base[interval];
  if (!basePriceId) {
    throw new TeamError(
      `Stripe ${interval}ly price is not configured for the Team plan`,
      500,
      "stripe_not_configured",
    );
  }
  const seatPriceId = prices.seat[interval];
  if (extraSeats > 0 && !seatPriceId) {
    throw new TeamError(
      `Stripe ${interval}ly additional seat price is not configured for the Team plan`,
      500,
      "stripe_not_configured",
    );
  }

  const stripe = getServerStripeClient();
  const checkoutTax = await destinationCheckoutTax();
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3107";

  let customerId = team.stripe_customer_id;
  if (!customerId) {
    const customer = await stripe.customers.create({
      email: ownerEmail,
      name: `${team.name} (${ownerName || ownerEmail})`,
      metadata: {
        app: "supoclip",
        teamId: team.id,
        ownerId: team.owner_id,
      },
    });
    customerId = customer.id;
    await prisma.team.update({
      where: { id: team.id },
      data: {
        stripe_customer_id: customerId,
        ...(stripeAccountContext()?.accountId
          ? { stripe_account_id: stripeAccountContext()?.accountId }
          : {}),
      },
    });
  } else {
    // Prevent duplicate subscriptions on the same team customer.
    for (const status of CHECKOUT_BLOCKING_STATUSES) {
      const existing = await stripe.subscriptions.list({
        customer: customerId,
        status,
        limit: 1,
      });
      if (existing.data[0]) {
        if (subscriptionNeedsPayment(existing.data[0].status)) {
          throw new TeamError(
            EXISTING_SUBSCRIPTION_NEEDS_PAYMENT_MESSAGE,
            409,
            EXISTING_SUBSCRIPTION_NEEDS_PAYMENT,
          );
        }
        throw new TeamError(
          "This team already has an active subscription. Use Manage Seats or Manage Billing instead.",
          409,
          "already_subscribed",
        );
      }
    }
  }

  const lineItems: Stripe.Checkout.SessionCreateParams.LineItem[] = [
    { price: basePriceId, quantity: 1 },
    ...(extraSeats > 0 && seatPriceId
      ? [{ price: seatPriceId, quantity: extraSeats }]
      : []),
  ];

  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    ...checkoutTax,
    customer: customerId,
    // No `userId` here: the personal-plan checkout handler binds a session's
    // customer and subscription to the user named by that key.
    metadata: {
      app: "supoclip",
      plan: "team",
      teamId: team.id,
      startedBy: actorUserId,
      interval,
      seats: String(seats),
    },
    subscription_data: {
      metadata: {
        app: "supoclip",
        plan: "team",
        teamId: team.id,
        ownerId: team.owner_id,
      },
    },
    line_items: lineItems,
    success_url: `${appUrl}/settings/team?billing=success`,
    cancel_url: `${appUrl}/settings/team?billing=cancelled`,
  });

  if (!session.url) {
    throw new TeamError("Unable to create team checkout session", 500, "checkout_failed");
  }
  return { url: session.url };
}

export async function updateTeamSeats({
  teamId,
  newSeats,
  monetizationEnabled,
}: {
  teamId: string;
  newSeats: number;
  monetizationEnabled: boolean;
}): Promise<{ seatLimit: number; prorationApplied: boolean }> {
  const config = getTeamBillingConfig();
  if (!Number.isInteger(newSeats)) {
    throw new TeamError("Seat count must be a whole number", 400, "invalid_seats");
  }
  if (newSeats < config.includedSeats) {
    throw new TeamError(
      `Team plan includes a minimum of ${config.includedSeats} seats`,
      400,
      "min_seats",
    );
  }
  if (newSeats > MAX_TEAM_SEATS) {
    throw new TeamError(
      `Teams can have up to ${MAX_TEAM_SEATS} seats online. Contact us for larger teams.`,
      400,
      "max_seats",
    );
  }

  const prisma = getPrismaClient();
  const team = await prisma.team.findUnique({ where: { id: teamId } });
  if (!team) throw new TeamError("Team not found", 404, "team_not_found");

  const paid =
    monetizationEnabled &&
    Boolean(team.stripe_subscription_id) &&
    BILLABLE_SUBSCRIPTION_STATUSES.has(team.subscription_status);
  if (monetizationEnabled && !paid) {
    throw new TeamError(
      "Start a Team subscription before changing paid seats",
      409,
      "subscription_required",
    );
  }

  // Invitations and acceptances take the same lock on the team row, so the
  // seats in use cannot grow between this check and the new limit. A lower
  // limit is written here, before Stripe is told; a higher one only after
  // Stripe has charged for it.
  const previousSeats = team.seat_limit;
  const lowering = newSeats < previousSeats;
  await prisma.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${teamId} FOR UPDATE`;
    const usage = await countOccupiedSeats(tx, teamId);
    if (newSeats < usage.occupiedSeats) {
      throw new TeamError(
        `You currently use ${usage.occupiedSeats} seats (${usage.activeMembers} members` +
          `${usage.pendingInvitations > 0 ? ` and ${usage.pendingInvitations} pending invitations` : ""}). ` +
          `Remove members or revoke pending invitations before lowering seats below ${usage.occupiedSeats}.`,
        409,
        "seats_in_use",
        usage,
      );
    }
    if (!paid || lowering) {
      await tx.team.update({ where: { id: teamId }, data: { seat_limit: newSeats } });
    }
  });

  if (!paid) {
    return { seatLimit: newSeats, prorationApplied: false };
  }

  const restoreSeats = async () => {
    if (!lowering) return;
    await prisma.team
      .update({ where: { id: teamId }, data: { seat_limit: previousSeats } })
      .catch((error) => console.error("Failed to restore team seat limit", error));
  };

  try {
    const stripe = getServerStripeClient();
    const subscription = await stripe.subscriptions.retrieve(team.stripe_subscription_id!);
    const baseItem = subscription.items.data.find((item) =>
      isTeamBaseStripePrice(item.price?.id),
    );
    const existingSeatItem = subscription.items.data.find((item) =>
      isTeamSeatStripePrice(item.price?.id),
    );
    const currentExtraSeats = existingSeatItem ? Number(existingSeatItem.quantity ?? 0) : 0;

    // The interval comes from what the subscription is billed on, so a team on
    // an earlier price keeps its interval.
    const detectedInterval: BillingInterval =
      getTeamIntervalForStripePrice(baseItem?.price?.id) ||
      (baseItem?.price?.recurring?.interval === "year" ? "year" : "month");
    const prices = getTeamStripePrices();
    const seatPriceId = prices.seat[detectedInterval];
    const extraSeats = Math.max(0, newSeats - config.includedSeats);

    const items: Stripe.SubscriptionUpdateParams.Item[] = [];
    if (extraSeats === 0) {
      if (existingSeatItem) {
        items.push({ id: existingSeatItem.id, deleted: true });
      }
    } else if (existingSeatItem) {
      if (currentExtraSeats !== extraSeats) {
        items.push({ id: existingSeatItem.id, quantity: extraSeats });
      }
    } else if (seatPriceId) {
      items.push({ price: seatPriceId, quantity: extraSeats });
    } else {
      throw new TeamError(
        `Stripe ${detectedInterval}ly additional seat price is not configured`,
        500,
        "stripe_not_configured",
      );
    }

    if (items.length === 0) {
      await syncTeamSubscriptionState(subscription, { teamIdHint: team.id });
      return {
        seatLimit: getTeamSeatsFromSubscription(subscription, config.includedSeats),
        prorationApplied: false,
      };
    }

    // Seats add pooled minutes the moment they exist, so more seats are paid
    // for now: the prorated amount is invoiced and charged in this call, and
    // nothing changes if the charge fails. Fewer seats give no credit, or a
    // team could add seats, spend their minutes and take the money back.
    const adding = extraSeats > currentExtraSeats;
    const updatedSubscription = await stripe.subscriptions.update(subscription.id, {
      items,
      ...(adding
        ? { proration_behavior: "always_invoice", payment_behavior: "error_if_incomplete" }
        : { proration_behavior: "none" }),
    });
    await syncTeamSubscriptionState(updatedSubscription, { teamIdHint: team.id });
    return {
      seatLimit: getTeamSeatsFromSubscription(updatedSubscription, config.includedSeats),
      prorationApplied: adding,
    };
  } catch (error) {
    await restoreSeats();
    if (isStripePaymentError(error)) {
      throw new TeamError(
        "The payment for the extra seats didn't go through. Update the payment method in Manage Billing and try again.",
        402,
        "payment_failed",
      );
    }
    throw error;
  }
}

function isStripePaymentError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const { type, statusCode } = error as { type?: unknown; statusCode?: unknown };
  return type === "StripeCardError" || statusCode === 402;
}

/**
 * Whether Stripe still has a subscription on the team's customer that will
 * charge again. Asked of Stripe, not of the team row: a missed webhook must
 * not let a team be deleted while its subscription keeps billing.
 */
export async function teamHasBillableStripeSubscription(teamId: string): Promise<boolean> {
  const team = await getPrismaClient().team.findUnique({
    where: { id: teamId },
    select: { stripe_customer_id: true },
  });
  if (!team?.stripe_customer_id) return false;
  const stripe = getServerStripeClient();
  for (const status of CHECKOUT_BLOCKING_STATUSES) {
    const subscriptions = await stripe.subscriptions.list({
      customer: team.stripe_customer_id,
      status,
      limit: 10,
    });
    if (subscriptions.data.some((subscription) => !subscription.cancel_at_period_end && !subscription.cancel_at)) {
      return true;
    }
  }
  return false;
}

export async function createTeamBillingPortalSession({
  teamId,
}: {
  teamId: string;
}): Promise<{ url: string }> {
  const prisma = getPrismaClient();
  const team = await prisma.team.findUnique({ where: { id: teamId } });
  if (!team) throw new TeamError("Team not found", 404, "team_not_found");
  if (!team.stripe_customer_id) {
    throw new TeamError(
      "This team does not have a billing account yet",
      400,
      "no_billing_account",
    );
  }

  const stripe = getServerStripeClient();
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3107";
  const portal = await stripe.billingPortal.sessions.create({
    ...accountPortalConfiguration(),
    customer: team.stripe_customer_id,
    return_url: `${appUrl}/settings/team`,
  });
  return { url: portal.url };
}
