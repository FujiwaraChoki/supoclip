const ENABLED_VALUES = new Set(["1", "true", "on", "yes"]);

function isEnabled(value: string | undefined): boolean {
  return ENABLED_VALUES.has(value?.trim().toLowerCase() ?? "");
}

/**
 * Price copy. Next.js inlines `NEXT_PUBLIC_*` at build time, including into
 * client components. `next.config.ts` copies `STRIPE_MANAGED_PAYMENTS_ENABLED`
 * into this name when the public var is unset, so one build-time flag is enough.
 */
export function managedPaymentsPriceCopyEnabled(): boolean {
  return isEnabled(process.env.NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED);
}

/**
 * New Checkout sessions only. An explicit server value wins, so a runtime
 * `false` can turn checkout off without a rebuild. When the server var is
 * unset, the public flag (inlined at build) applies.
 *
 * Existing subscriptions are unchanged: plan changes stay on the billing portal.
 */
export function stripeManagedPaymentsEnabled(): boolean {
  const server = process.env.STRIPE_MANAGED_PAYMENTS_ENABLED;
  if (server !== undefined && server.trim() !== "") {
    return isEnabled(server);
  }
  return managedPaymentsPriceCopyEnabled();
}

/** Leading space included, so it can be interpolated as `{taxNote}` or rendered alone. */
export function formatPriceTaxNote(label: string): string {
  return managedPaymentsPriceCopyEnabled() ? ` ${label}` : "";
}
