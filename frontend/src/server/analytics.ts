import { createHash } from "node:crypto";

import type { BillingInterval } from "@/lib/billing-plans";
import { getPostHogServer } from "@/server/posthog";

/**
 * Server-side product funnel events (billing). The catalog is in
 * docs/observability.md; browser events are in `lib/analytics.ts`.
 *
 * Off unless NEXT_PUBLIC_POSTHOG_KEY is set. Never throws. The distinct id is
 * the Better Auth user id, the same id the browser identifies with. Properties
 * never carry emails, names, or creator codes.
 */

export type SubscriptionProvider = "stripe" | "apple";

type Interval = BillingInterval | null;

export type ServerEventProperties = {
  checkout_started: {
    plan: string;
    interval: BillingInterval;
    provider: "stripe";
    has_creator_code: boolean;
    /** Checkout for new subscribers, the portal's plan change for existing ones. */
    flow: "checkout" | "plan_change" | "payment_link";
  };
  subscription_started: {
    plan: string;
    interval: Interval;
    provider: SubscriptionProvider;
    trial: boolean;
    has_creator_code?: boolean;
  };
  subscription_cancel_scheduled: {
    plan: string | null;
    interval: Interval;
    provider: SubscriptionProvider;
    reason?: string | null;
  };
  subscription_canceled: {
    plan: string | null;
    interval: Interval;
    provider: SubscriptionProvider;
    /** An approved creator went back to their free Pro. */
    kept_creator_pro?: boolean;
  };
  subscription_renewed: {
    plan: string | null;
    interval: Interval;
    provider: SubscriptionProvider;
  };
};

export type ServerEventName = keyof ServerEventProperties;

export type PersonProperties = {
  plan?: string;
  subscription_status?: string;
};

export type ServerEvent = {
  [E in ServerEventName]: {
    event: E;
    distinctId: string;
    properties: ServerEventProperties[E];
    set?: PersonProperties;
    uuid?: string;
    timestamp?: Date;
  };
}[ServerEventName];

export function captureServerEvent(event: ServerEvent): void {
  try {
    const client = getPostHogServer();
    if (!client || !event.distinctId) return;
    client.capture({
      distinctId: event.distinctId,
      event: event.event,
      properties: {
        ...event.properties,
        platform: "web-server",
        ...(event.set ? { $set: event.set } : {}),
      },
      ...(event.uuid ? { uuid: event.uuid } : {}),
      ...(event.timestamp ? { timestamp: event.timestamp } : {}),
    });
  } catch (error) {
    console.warn("Could not send analytics event", event.event, error instanceof Error ? error.name : "error");
  }
}

/** A stable UUID for the same inputs, so PostHog drops a resend of the same event. */
export function deterministicUuid(...parts: string[]): string {
  const hex = createHash("sha256").update(parts.join("\u0000")).digest("hex").slice(0, 32).split("");
  hex[12] = "5";
  hex[16] = ((parseInt(hex[16], 16) & 0x3) | 0x8).toString(16);
  const value = hex.join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

/**
 * Collects a webhook's events and sends them only after the handler finished.
 * The webhook's event row already drops redeliveries; holding events until the
 * end means a failed attempt that the provider retries does not send twice.
 *
 * `claimKey` covers facts that arrive in more than one webhook (Stripe sends
 * both `invoice.paid` and `invoice.payment_succeeded` for one renewal): the
 * first delivery to claim the key in the webhook dedup table sends it.
 */
export class WebhookAnalytics {
  private readonly pending: { event: ServerEvent; claimKey?: string }[] = [];

  constructor(
    private readonly sourceEventId: string,
    private readonly timestamp?: Date,
  ) {}

  add(event: ServerEvent, options?: { claimKey?: string }) {
    const seed = options?.claimKey ?? this.sourceEventId;
    this.pending.push({
      event: {
        ...event,
        uuid: deterministicUuid(seed, event.event, event.distinctId),
        timestamp: event.timestamp ?? this.timestamp,
      },
      claimKey: options?.claimKey,
    });
  }

  get events(): readonly ServerEvent[] {
    return this.pending.map((item) => item.event);
  }

  /** Never throws. `claim` returns false when another delivery already sent that key. */
  async flush(claim?: (key: string) => Promise<boolean>) {
    if (!getPostHogServer()) return;
    for (const { event, claimKey } of this.pending) {
      try {
        if (claimKey && claim && !(await claim(claimKey))) continue;
        captureServerEvent(event);
      } catch (error) {
        console.warn("Could not send analytics event", event.event, error instanceof Error ? error.name : "error");
      }
    }
    this.pending.length = 0;
    await flushServerEvents();
  }
}

/**
 * Wait (briefly) until queued events are sent. `capture` only queues the
 * request; on a serverless host (the RevenueCat webhook runs on Vercel) the
 * function can be frozen as soon as the response is returned, dropping it.
 * Capped so a slow PostHog never holds a webhook response. Never throws.
 */
export async function flushServerEvents(timeoutMs = 2000): Promise<void> {
  const client = getPostHogServer();
  if (!client) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      client.flush(),
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, timeoutMs);
      }),
    ]);
  } catch {
    // Analytics only.
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** Billing interval of a subscription period, from its length in seconds. */
export function intervalFromPeriod(start: number | null | undefined, end: number | null | undefined): Interval {
  if (typeof start !== "number" || typeof end !== "number" || end <= start) return null;
  return end - start > 60 * 24 * 60 * 60 ? "year" : "month";
}
