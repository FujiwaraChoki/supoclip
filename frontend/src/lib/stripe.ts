import Stripe from "stripe";
import { scopedStripeClient } from "@/lib/stripe-accounts";

let stripeClient: Stripe | null = null;

export function getStripeClient(): Stripe {
  return scopedStripeClient(getLegacyStripeClient);
}

function getLegacyStripeClient(): Stripe {
  if (stripeClient) {
    return stripeClient;
  }

  const secretKey = process.env.STRIPE_SECRET_KEY;
  if (!secretKey) {
    throw new Error("STRIPE_SECRET_KEY is not configured");
  }

  stripeClient = new Stripe(secretKey);
  return stripeClient;
}
