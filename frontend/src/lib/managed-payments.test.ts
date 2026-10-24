import { formatPriceTaxNote, managedPaymentsPriceCopyEnabled, stripeManagedPaymentsEnabled } from "./managed-payments";

describe("stripe managed payments flag", () => {
  const env = process.env;

  beforeEach(() => {
    process.env = { ...env };
    delete process.env.STRIPE_MANAGED_PAYMENTS_ENABLED;
    delete process.env.NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED;
  });

  afterAll(() => {
    process.env = env;
  });

  it.each(["", "false", "0", "off", "no", undefined])("stays off for %j", (value) => {
    if (value === undefined) {
      delete process.env.STRIPE_MANAGED_PAYMENTS_ENABLED;
    } else {
      process.env.STRIPE_MANAGED_PAYMENTS_ENABLED = value;
    }
    expect(stripeManagedPaymentsEnabled()).toBe(false);
    expect(managedPaymentsPriceCopyEnabled()).toBe(false);
    expect(formatPriceTaxNote("+ applicable tax")).toBe("");
  });

  it.each(["true", "TRUE", "1", "on", "yes"])("turns checkout on for server flag %j", (value) => {
    process.env.STRIPE_MANAGED_PAYMENTS_ENABLED = value;
    expect(stripeManagedPaymentsEnabled()).toBe(true);
    expect(managedPaymentsPriceCopyEnabled()).toBe(false);
  });

  it("uses the public flag for price copy and as the checkout fallback", () => {
    process.env.NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED = "true";
    expect(managedPaymentsPriceCopyEnabled()).toBe(true);
    expect(stripeManagedPaymentsEnabled()).toBe(true);
    expect(formatPriceTaxNote("+ applicable tax")).toBe(" + applicable tax");
  });

  it("lets an explicit server false override a public true", () => {
    process.env.STRIPE_MANAGED_PAYMENTS_ENABLED = "false";
    process.env.NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED = "true";
    expect(stripeManagedPaymentsEnabled()).toBe(false);
    expect(managedPaymentsPriceCopyEnabled()).toBe(true);
  });
});
