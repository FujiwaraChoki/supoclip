import type Stripe from "stripe";
import {
  stripeAccountContext,
  stripeAccountsEnabled,
  sourceStripeAccountId,
} from "@/lib/stripe-accounts";
import {
  resolveBillingRisk,
  isBillingRiskEvent,
} from "@/lib/stripe-billing-safety";
import { getPrismaClient } from "./prisma";
import { getServerStripeClient } from "./stripe";
import { upsertSubscriptionState } from "./stripe-subscription-sync";
import { stripeObjectId } from "@/lib/stripe-migration-engine";

/** Risk cannot revoke unrelated payments, other accounts, Apple, or affiliate grants. */
export async function handleOwnedBillingRisk(
  event: Stripe.Event,
): Promise<void> {
  if (!stripeAccountsEnabled() || !isBillingRiskEvent(event)) return;
  const context = stripeAccountContext();
  if (!context) throw new Error("Missing risk account context");
  const client = getServerStripeClient();
  const risk = await resolveBillingRisk(event, client);
  if (!risk?.subscriptionId) return;
  const users = await getPrismaClient().user.findMany({
    where: { stripe_customer_id: risk.customerId },
    take: 2,
  });
  const user = users.length === 1 ? users[0] : undefined;
  if (
    !user ||
    (user.stripe_account_id || sourceStripeAccountId()) !== context.accountId ||
    user.stripe_subscription_id !== risk.subscriptionId ||
    user.subscription_provider !== "stripe"
  )
    return;
  const subscription = await client.subscriptions.retrieve(risk.subscriptionId);
  if (stripeObjectId(subscription.customer) !== risk.customerId)
    throw new Error("Risk subscription customer mismatch");
  const cancelled =
    subscription.status === "canceled"
      ? subscription
      : await client.subscriptions.cancel(
          subscription.id,
          { cancellation_details: { comment: `billing_risk:${event.id}` } },
          { idempotencyKey: `risk:${event.id}` },
        );
  await upsertSubscriptionState(cancelled);
}
