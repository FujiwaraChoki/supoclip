// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  COMMISSION_MONTHS,
  COMMISSION_PERCENT,
  commissionAmount,
  commissionBase,
  firstYearCommission,
  formatMinor,
  isPayoutCountry,
  withinCommissionWindow,
} from "./creator-commission";

describe("creator commission terms", () => {
  it("leaves tax out in proportion to what settled", () => {
    expect(commissionBase(1199, 1199, 200)).toBe(999);
    // The same invoice settled at a different rate in another currency.
    expect(commissionBase(1100, 1199, 200)).toBe(917);
    expect(commissionBase(999, 999, 0)).toBe(999);
    expect(commissionBase(0, 999, 0)).toBe(0);
  });

  it("rounds commission to the nearest minor unit", () => {
    expect(commissionAmount(999)).toBe(300);
    expect(commissionAmount(799)).toBe(240);
    expect(commissionAmount(1)).toBe(0);
  });

  it("ends the window exactly 12 months after the first payment", () => {
    const first = new Date("2026-01-31T10:00:00Z");
    expect(withinCommissionWindow(first, new Date("2026-12-31T10:00:00Z"))).toBe(true);
    expect(withinCommissionWindow(first, new Date("2027-01-31T10:00:00Z"))).toBe(false);
  });

  it("estimates a monthly subscriber's first year for the website copy", () => {
    // 3 months at $7.99 and 9 at $9.99, 30% each.
    expect(firstYearCommission(9.99)).toBe(34);
    expect(firstYearCommission(24.99)).toBe(85);
  });

  it("pays out only where Stripe cross-border payouts reach", () => {
    expect(isPayoutCountry("US")).toBe(true);
    expect(isPayoutCountry("DE")).toBe(true);
    expect(isPayoutCountry("IN")).toBe(false);
    expect(isPayoutCountry("AU")).toBe(false);
  });

  it("formats zero-decimal currencies", () => {
    expect(formatMinor(2500, "usd", "en-US")).toBe("$25.00");
    expect(formatMinor(2500, "jpy", "en-US")).toBe("¥2,500");
  });

  it("matches what the approval email promises", () => {
    const email = readFileSync(
      path.resolve(__dirname, "../../../backend/src/services/affiliate_email_service.py"),
      "utf8",
    );
    expect(email).toContain(`${COMMISSION_PERCENT}% of what customers`);
    expect(email).toContain(`first ${COMMISSION_MONTHS} months`);
  });
});
