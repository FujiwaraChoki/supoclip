import Stripe from "stripe";

let stripeClient: Stripe | null = null;

export function getStripeClient(): Stripe {
  if (stripeClient) {
    return stripeClient;
  }

  const secretKey = process.env.STRIPE_SECRET_KEY;
  if (!secretKey) {
    throw new Error("STRIPE_SECRET_KEY is not configured");
  }

  // stripe@22 pins 2026-08-26.dahlia. Managed Payments needs `managed_payments`
  // on Checkout, which the SDK types gained in 22.1.0 (API 2026-04-22.dahlia),
  // after the 2025-03-31.basil minimum in Stripe's docs.
  stripeClient = new Stripe(secretKey);
  return stripeClient;
}
