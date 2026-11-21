// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { runWithStripeAccount, type StripeAccountContext } from "@/lib/stripe-accounts";

import { getTeamStripePrices, teamPlanAvailable } from "./billing-plans";

const SOURCE = "acct_source1";
const DESTINATION = "acct_destination1";

/** Two Stripe accounts, new checkouts on `checkoutAccount`. */
function twoAccounts(checkoutAccount: string) {
  vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "true");
  vi.stubEnv("STRIPE_SOURCE_ACCOUNT_ID", SOURCE);
  vi.stubEnv("STRIPE_DESTINATION_ACCOUNT_ID", DESTINATION);
  vi.stubEnv("STRIPE_CHECKOUT_ACCOUNT_ID", checkoutAccount);
}

describe("teamPlanAvailable", () => {
  beforeEach(() => {
    for (const name of [
      "STRIPE_ACCOUNT_MIGRATION_ENABLED", "STRIPE_SOURCE_ACCOUNT_ID", "STRIPE_DESTINATION_ACCOUNT_ID",
      "STRIPE_CHECKOUT_ACCOUNT_ID", "STRIPE_TEAM_PRICE_ID", "STRIPE_TEAM_ANNUAL_PRICE_ID",
      "STRIPE_DESTINATION_TEAM_PRICE_ID", "STRIPE_DESTINATION_TEAM_ANNUAL_PRICE_ID",
    ]) vi.stubEnv(name, "");
  });
  afterEach(() => vi.unstubAllEnvs());

  it("is always open on a self-hosted install", () => {
    expect(teamPlanAvailable(false)).toBe(true);
  });

  it("with one Stripe account, follows the plain Team price", () => {
    expect(teamPlanAvailable(true)).toBe(false);
    vi.stubEnv("STRIPE_TEAM_ANNUAL_PRICE_ID", "price_team_year");
    expect(teamPlanAvailable(true)).toBe(true);
  });

  // The routes that ask this run outside any Stripe account scope. The answer has to come
  // from the account a team would actually check out on, not from the unscoped default.
  it("with checkout on the destination account, follows that account's Team price", () => {
    twoAccounts(DESTINATION);
    vi.stubEnv("STRIPE_DESTINATION_TEAM_PRICE_ID", "price_dest_team_month");
    expect(teamPlanAvailable(true)).toBe(true);

    // The source account's price does not count: teams cannot check out there.
    vi.stubEnv("STRIPE_DESTINATION_TEAM_PRICE_ID", "");
    vi.stubEnv("STRIPE_TEAM_PRICE_ID", "price_source_team_month");
    expect(teamPlanAvailable(true)).toBe(false);
  });

  it("with checkout still on the source account, follows the source's Team price", () => {
    twoAccounts(SOURCE);
    vi.stubEnv("STRIPE_DESTINATION_TEAM_PRICE_ID", "price_dest_team_month");
    expect(teamPlanAvailable(true)).toBe(false);
    vi.stubEnv("STRIPE_TEAM_PRICE_ID", "price_source_team_month");
    expect(teamPlanAvailable(true)).toBe(true);
  });

  it("stays closed when two accounts are switched on but not configured", () => {
    vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "true");
    vi.stubEnv("STRIPE_TEAM_PRICE_ID", "price_source_team_month");
    expect(teamPlanAvailable(true)).toBe(false);
  });

  it("agrees with the prices a scoped checkout on that account reads", () => {
    twoAccounts(DESTINATION);
    vi.stubEnv("STRIPE_DESTINATION_TEAM_PRICE_ID", "price_dest_team_month");
    const scoped = runWithStripeAccount(
      { accountId: DESTINATION, client: {} as StripeAccountContext["client"] },
      () => getTeamStripePrices(),
    );
    expect(scoped.base.month).toBe("price_dest_team_month");
    expect(teamPlanAvailable(true)).toBe(true);
  });
});
