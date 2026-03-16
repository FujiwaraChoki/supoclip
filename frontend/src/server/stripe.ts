import { getStripeClient } from "@/lib/stripe";

export function getServerStripeClient() {
  return getStripeClient();
}
