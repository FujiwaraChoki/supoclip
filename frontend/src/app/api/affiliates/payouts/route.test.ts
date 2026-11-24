// @vitest-environment node
import { POST } from "./route";
import { prismaCommissionStore } from "@/server/affiliate-commissions";
import { connectPlatformClient } from "@/server/affiliate-payouts";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/affiliate-commissions", () => ({ prismaCommissionStore: vi.fn() }));
vi.mock("@/server/affiliate-payouts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/server/affiliate-payouts")>()),
  connectPlatformClient: vi.fn(),
}));

const approved = {
  user_id: "user-1", slug: "ada", status: "approved", stripe_connect_account_id: null, stripe_connect_platform_id: null,
  payout_country: null, payouts_enabled: false, profile_url: "https://youtube.com/@ada", email: "ada@example.com",
  country: "US",
};

function setup(affiliate: Record<string, unknown> | undefined) {
  const execute = vi.fn().mockResolvedValue(1);
  vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
  vi.mocked(prismaCommissionStore).mockReturnValue({ query: vi.fn().mockResolvedValue(affiliate ? [affiliate] : []), execute } as never);
  const stripe = {
    accounts: { create: vi.fn().mockResolvedValue({ id: "acct_new" }), createLoginLink: vi.fn().mockResolvedValue({ url: "https://connect.stripe.com/express/x" }) },
    accountLinks: { create: vi.fn().mockResolvedValue({ url: "https://connect.stripe.com/setup/x" }) },
  };
  vi.mocked(connectPlatformClient).mockResolvedValue(stripe as never);
  return { stripe, execute };
}

const post = (body: unknown) =>
  POST(new Request("http://localhost/api/affiliates/payouts", { method: "POST", body: JSON.stringify(body) }));

describe("/api/affiliates/payouts", () => {
  beforeEach(() => vi.resetAllMocks());

  it("is only for approved creators", async () => {
    setup({ ...approved, status: "pending" });
    expect((await post({ action: "onboard" })).status).toBe(403);
  });

  it.each(["ZZ", null])("refuses creators who live where Stripe can't pay (%s)", async (country) => {
    const { stripe } = setup({ ...approved, country });
    const response = await post({ action: "onboard" });
    expect(response.status).toBe(403);
    expect(stripe.accounts.create).not.toHaveBeenCalled();
  });

  it("creates a transfers-only Express account and returns Stripe's onboarding link", async () => {
    const { stripe, execute } = setup(approved);
    const response = await post({ action: "onboard" });

    expect(await response.json()).toEqual({ url: "https://connect.stripe.com/setup/x" });
    expect(stripe.accounts.create).toHaveBeenCalledWith(
      expect.objectContaining({ type: "express", country: "US", capabilities: { transfers: { requested: true } } }),
      expect.objectContaining({ idempotencyKey: expect.stringContaining("user-1") }),
    );
    expect(execute).toHaveBeenCalledWith(expect.stringContaining("UPDATE affiliates"), ["user-1", "acct_new", "default", "US"]);
    expect(stripe.accountLinks.create).toHaveBeenCalledWith(expect.objectContaining({ account: "acct_new", type: "account_onboarding" }));
  });

  it("also asks for card payments outside the US, as Stripe requires there", async () => {
    const { stripe } = setup({ ...approved, country: "DE" });
    await post({ action: "onboard" });
    expect(stripe.accounts.create).toHaveBeenCalledWith(
      expect.objectContaining({
        country: "DE",
        capabilities: { transfers: { requested: true }, card_payments: { requested: true } },
      }),
      expect.anything(),
    );
  });

  it("reuses the creator's account to finish onboarding", async () => {
    const { stripe } = setup({ ...approved, stripe_connect_account_id: "acct_old", stripe_connect_platform_id: "default" });
    await post({ action: "onboard" });
    expect(stripe.accounts.create).not.toHaveBeenCalled();
    expect(stripe.accountLinks.create).toHaveBeenCalledWith(expect.objectContaining({ account: "acct_old" }));
  });

  it("opens the Express dashboard only once payouts are set up", async () => {
    setup(approved);
    expect((await post({ action: "dashboard" })).status).toBe(409);
    setup({ ...approved, stripe_connect_account_id: "acct_old", stripe_connect_platform_id: "default" });
    expect(await (await post({ action: "dashboard" })).json()).toEqual({ url: "https://connect.stripe.com/express/x" });
  });
});
