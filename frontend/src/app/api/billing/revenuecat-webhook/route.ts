import crypto from "node:crypto";
import { NextResponse } from "next/server";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { fetchBackend } from "@/server/backend-api";
import { getPrismaClient } from "@/server/prisma";

type SubscriptionEmailEvent = "subscribed" | "unsubscribed";
type BillingPlan = "pro" | "scale";

type RevenueCatWebhookBody = {
  event?: RevenueCatEvent;
  api_version?: string;
};

type RevenueCatEvent = {
  id?: string;
  type?: string;
  app_user_id?: string;
  aliases?: unknown;
  product_id?: string | null;
  period_type?: string | null;
  purchased_at_ms?: number | string | null;
  expiration_at_ms?: number | string | null;
  grace_period_expiration_at_ms?: number | string | null;
  transferred_from?: unknown;
  transferred_to?: unknown;
};

const DEFAULT_PRODUCT_IDS: Record<BillingPlan, string[]> = {
  pro: ["com.samihindi.supoclip.pro.monthly"],
  scale: ["com.samihindi.supoclip.scale.monthly"],
};
// PRODUCT_CHANGE is intentionally excluded: it announces a pending plan switch
// (App Store downgrades apply at the next renewal), and the effective change
// arrives as a RENEWAL/INITIAL_PURCHASE carrying the new product_id.
const GRANT_EVENTS = new Set([
  "INITIAL_PURCHASE",
  "RENEWAL",
  "UNCANCELLATION",
]);
const ANONYMOUS_PREFIX = "$RCAnonymousID:";

function parseProductIds(value: string | undefined): string[] {
  if (!value) {
    return [];
  }
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function getProductPlan(productId: string | null | undefined): BillingPlan | null {
  if (!productId) {
    return null;
  }

  const productIds: Record<BillingPlan, string[]> = {
    pro: [
      ...DEFAULT_PRODUCT_IDS.pro,
      ...parseProductIds(process.env.REVENUECAT_PRO_PRODUCT_IDS),
    ],
    scale: [
      ...DEFAULT_PRODUCT_IDS.scale,
      ...parseProductIds(process.env.REVENUECAT_SCALE_PRODUCT_IDS),
    ],
  };

  if (productIds.pro.includes(productId)) {
    return "pro";
  }
  if (productIds.scale.includes(productId)) {
    return "scale";
  }
  return null;
}

function toDate(ms: number | string | null | undefined): Date | null {
  if (ms === null || ms === undefined || ms === "") {
    return null;
  }

  const numericValue = typeof ms === "number" ? ms : Number(ms);
  if (!Number.isFinite(numericValue)) {
    return null;
  }

  return new Date(numericValue);
}

function constantTimeEqual(actual: string | null, expected: string): boolean {
  const actualBuffer = Buffer.from(actual || "");
  const expectedBuffer = Buffer.from(expected);
  const length = Math.max(actualBuffer.length, expectedBuffer.length);
  const actualPadded = Buffer.alloc(length);
  const expectedPadded = Buffer.alloc(length);

  actualBuffer.copy(actualPadded);
  expectedBuffer.copy(expectedPadded);

  return (
    crypto.timingSafeEqual(actualPadded, expectedPadded) &&
    actualBuffer.length === expectedBuffer.length
  );
}

function normalizeStringArray(value: unknown): string[] {
  if (typeof value === "string") {
    return [value];
  }
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function isAnonymousUserId(userId: string): boolean {
  return userId.startsWith(ANONYMOUS_PREFIX);
}

async function resolveUserId(
  appUserId: string | null | undefined,
  aliases: unknown
): Promise<string | null> {
  if (!appUserId) {
    return null;
  }

  const candidates = isAnonymousUserId(appUserId)
    ? normalizeStringArray(aliases).filter((alias) => !isAnonymousUserId(alias))
    : [appUserId];

  for (const candidate of candidates) {
    const user = await getPrismaClient().user.findUnique({
      where: { id: candidate },
      select: { id: true },
    });
    if (user?.id) {
      return user.id;
    }
  }

  return null;
}

async function sendBackendSubscriptionEmail(userId: string, event: SubscriptionEmailEvent) {
  const response = await fetchBackend("/billing/subscription-email", {
    method: "POST",
    userId,
    extraHeaders: {
      ...buildBackendAuthHeaders(userId),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event }),
    cache: "no-store",
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Backend subscription email failed with ${response.status}: ${detail || "unknown error"}`
    );
  }
}

async function sendSubscriptionEmailBestEffort(
  userId: string,
  event: SubscriptionEmailEvent
) {
  try {
    await sendBackendSubscriptionEmail(userId, event);
  } catch (error) {
    console.error("Subscription email side effect failed", {
      userId,
      event,
      error,
    });
  }
}

// Grants intentionally apply even over an active Stripe subscription: a
// completed Apple purchase must confer the plan that was paid for. Checkout
// and portal routes return 409 for Apple-managed users, so dual-provider
// overlap is limited to restore edge cases resolved in the user's favor.
async function grantAppleSubscription(userId: string, event: RevenueCatEvent) {
  const plan = getProductPlan(event.product_id);
  if (!plan) {
    console.warn("RevenueCat event has no mapped product", {
      eventId: event.id,
      productId: event.product_id,
    });
    return;
  }

  const isTrial = event.period_type === "TRIAL";
  const expiration = toDate(event.expiration_at_ms);
  await getPrismaClient().user.update({
    where: { id: userId },
    data: {
      plan,
      subscription_status: isTrial ? "trialing" : "active",
      subscription_provider: "apple",
      billing_period_start: toDate(event.purchased_at_ms),
      billing_period_end: expiration,
      trial_ends_at: isTrial ? expiration : null,
    },
  });

  await sendSubscriptionEmailBestEffort(userId, "subscribed");
}

async function downgradeAppleSubscription(userId: string, eventExpiration?: Date | null) {
  // A retried/out-of-order EXPIRATION must not revoke access that a later
  // RENEWAL already extended past the event's own expiration date.
  const stalenessFilter = eventExpiration
    ? {
        OR: [
          { billing_period_end: null },
          { billing_period_end: { lte: eventExpiration } },
        ],
      }
    : {};
  const result = await getPrismaClient().user.updateMany({
    where: {
      id: userId,
      subscription_provider: "apple",
      ...stalenessFilter,
    },
    data: {
      plan: "free",
      subscription_status: "inactive",
      subscription_provider: null,
      billing_period_start: null,
      billing_period_end: null,
      trial_ends_at: null,
    },
  });

  if (result.count > 0) {
    await sendSubscriptionEmailBestEffort(userId, "unsubscribed");
  }
}

async function handleBillingIssue(userId: string, event: RevenueCatEvent) {
  const gracePeriodEnd = toDate(event.grace_period_expiration_at_ms);
  if (gracePeriodEnd && gracePeriodEnd.getTime() > Date.now()) {
    await getPrismaClient().user.updateMany({
      where: {
        id: userId,
        subscription_provider: "apple",
      },
      data: {
        billing_period_end: gracePeriodEnd,
      },
    });
    return;
  }

  await getPrismaClient().user.updateMany({
    where: {
      id: userId,
      subscription_provider: "apple",
    },
    data: {
      subscription_status: "past_due",
    },
  });
}

async function resolveTransferUsers(value: unknown): Promise<string[]> {
  const resolvedUsers: string[] = [];
  for (const appUserId of normalizeStringArray(value)) {
    const userId = await resolveUserId(appUserId, []);
    if (userId) {
      resolvedUsers.push(userId);
    }
  }
  return resolvedUsers;
}

async function handleTransfer(event: RevenueCatEvent) {
  const transferredTo = await resolveTransferUsers(event.transferred_to);
  const transferredFrom = await resolveTransferUsers(event.transferred_from);

  if (transferredTo.length === 0 || transferredFrom.length === 0) {
    console.warn("RevenueCat transfer event did not resolve both sides", {
      eventId: event.id,
    });
    return;
  }

  for (const userId of transferredTo) {
    await grantAppleSubscription(userId, event);
  }
  for (const userId of transferredFrom) {
    await downgradeAppleSubscription(userId);
  }
}

async function handleRevenueCatEvent(event: RevenueCatEvent) {
  if (event.type === "TRANSFER") {
    await handleTransfer(event);
    return;
  }

  const userId = await resolveUserId(event.app_user_id, event.aliases);
  if (!userId) {
    console.warn("RevenueCat event did not resolve to a user", {
      eventId: event.id,
      appUserId: event.app_user_id,
    });
    return;
  }

  if (GRANT_EVENTS.has(event.type || "")) {
    await grantAppleSubscription(userId, event);
    return;
  }

  if (event.type === "CANCELLATION") {
    return;
  }

  if (event.type === "EXPIRATION") {
    await downgradeAppleSubscription(userId, toDate(event.expiration_at_ms));
    return;
  }

  if (event.type === "BILLING_ISSUE") {
    await handleBillingIssue(userId, event);
  }
}

export async function POST(request: Request) {
  const expectedAuthHeader = process.env.REVENUECAT_WEBHOOK_AUTH_HEADER;
  if (!expectedAuthHeader) {
    return NextResponse.json(
      { error: "RevenueCat webhook auth header is not configured" },
      { status: 503 }
    );
  }

  if (!constantTimeEqual(request.headers.get("authorization"), expectedAuthHeader)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: RevenueCatWebhookBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const event = body.event;
  if (!event?.id || !event.type) {
    return NextResponse.json({ error: "Invalid RevenueCat event" }, { status: 400 });
  }

  try {
    await getPrismaClient().revenueCatWebhookEvent.create({
      data: {
        id: event.id,
        type: event.type,
      },
    });
  } catch (error) {
    const knownError = error as { code?: string };
    if (knownError.code === "P2002") {
      return NextResponse.json({ ok: true });
    }
    throw error;
  }

  try {
    await handleRevenueCatEvent(event);
  } catch (error) {
    await getPrismaClient()
      .revenueCatWebhookEvent.delete({ where: { id: event.id } })
      .catch(() => {});
    console.error("RevenueCat webhook handler failed", { eventId: event.id, error });
    return NextResponse.json({ error: "RevenueCat webhook handler failed" }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
