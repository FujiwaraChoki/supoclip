import Stripe from "stripe";
import { runWithStripeAccount } from "@/lib/stripe-accounts";
import { POST } from "./route";
import { auth } from "@/lib/auth";
import prisma from "@/lib/prisma";
import { getStripeClient } from "@/lib/stripe";

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("@/lib/monetization", () => ({
  monetizationEnabled: true,
}));

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/prisma", () => ({
  default: {
    user: {
      findUnique: vi.fn(),
      update: vi.fn(),
    },
  },
}));

vi.mock("@/lib/stripe", () => ({
  getStripeClient: vi.fn(),
}));

describe("/api/billing/portal", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: "user-1", email: "user@example.com", name: "User" },
    } as never);
  });

  it("blocks Stripe portal access for active App Store subscriptions", async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue({
      id: "user-1",
      email: "user@example.com",
      name: "User",
      stripe_customer_id: null,
      subscription_provider: "apple",
      subscription_status: "trialing",
    } as never);

    const response = await POST();

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({
      error: "Your subscription is managed through the App Store",
    });
    expect(getStripeClient).not.toHaveBeenCalled();
  });

  it("allows App Store users with a Stripe customer to open the Stripe portal", async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue({
      id: "user-1",
      email: "user@example.com",
      name: "User",
      stripe_customer_id: "cus_123",
      subscription_provider: "apple",
      subscription_status: "active",
    } as never);
    vi.mocked(getStripeClient).mockReturnValue({
      billingPortal: {
        sessions: {
          create: vi.fn().mockResolvedValue({ url: "https://billing.stripe.test/session" }),
        },
      },
    } as never);

    const response = await POST();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      url: "https://billing.stripe.test/session",
    });
    expect(getStripeClient).toHaveBeenCalled();
  });
});

describe("staged subscription invoice recovery", () => {
  it.each(["open", "paid", "wrong_customer"])(
    "handles %s without updating cards or disclosing another invoice",
    async (state) => {
      vi.resetAllMocks();
      vi.mocked(auth.api.getSession).mockResolvedValue({
        user: { id: "synthetic", email: "synthetic@example.invalid" },
      } as never);
      vi.mocked(prisma.user.findUnique).mockResolvedValue({
        id: "synthetic",
        stripe_customer_id: "cus_source",
        subscription_provider: "stripe",
        subscription_status: "active",
      } as never);
      const previousFlag = process.env.STRIPE_ACCOUNT_MIGRATION_ENABLED;
      process.env.STRIPE_ACCOUNT_MIGRATION_ENABLED = "false";
      const sdk = new Stripe("sk_test_synthetic", {
        httpClient: Stripe.createFetchHttpClient(async () => {
          throw new Error("External network forbidden");
        }),
      });
      vi.spyOn(sdk.subscriptions, "retrieve").mockResolvedValue({
        id: "sub_destination",
        customer: "cus_destination",
        latest_invoice: "in_action",
      } as Stripe.Response<Stripe.Subscription>);
      vi.spyOn(sdk.invoices, "retrieve").mockResolvedValue({
        id: "in_action",
        customer: state === "wrong_customer" ? "cus_other" : "cus_destination",
        parent: { subscription_details: { subscription: "sub_destination" } },
        status: state === "paid" ? "paid" : "open",
        amount_remaining: state === "paid" ? 0 : 999,
        hosted_invoice_url: "https://invoice.stripe.com/i/action",
      } as Stripe.Response<Stripe.Invoice>);
      const createPortal = vi.spyOn(sdk.billingPortal.sessions, "create");
      vi.mocked(getStripeClient).mockReturnValue(sdk);
      try {
        const response = await runWithStripeAccount(
          {
            accountId: "acct_destination",
            client: sdk,
            customerId: "cus_destination",
            recoveryOnly: true,
            recoverySubscriptionId: "sub_destination",
          },
          POST,
        );
        expect(response.status).toBe(state === "open" ? 200 : 409);
        const body = await response.json();
        if (state === "open")
          expect(body).toEqual({ url: "https://invoice.stripe.com/i/action" });
        else expect(body).not.toHaveProperty("url");
        expect(createPortal).not.toHaveBeenCalled();
      } finally {
        if (previousFlag === undefined)
          delete process.env.STRIPE_ACCOUNT_MIGRATION_ENABLED;
        else process.env.STRIPE_ACCOUNT_MIGRATION_ENABLED = previousFlag;
      }
    },
  );
});
