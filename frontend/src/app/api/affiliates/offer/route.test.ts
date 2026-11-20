import { GET } from "./route";
import { getStripeClient } from "@/lib/stripe";
import { getPrismaClient } from "@/server/prisma";
import { auth } from "@/lib/auth";
import { scopedStripeClient, verifiedStripeClient } from "@/lib/stripe-accounts";

vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));
vi.mock("@/lib/stripe", () => ({ getStripeClient: vi.fn() }));
vi.mock("@/lib/auth", () => ({ auth: { api: { getSession: vi.fn() } } }));
vi.mock("@/lib/stripe-accounts", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/stripe-accounts")>(),
  verifiedStripeClient: vi.fn(),
}));

const lookup = (code: string) => GET(new Request(`http://localhost/api/affiliates/offer?code=${code}`));

describe("GET /api/affiliates/offer", () => {
  const findUnique = vi.fn();
  const listPromotionCodes = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "false");
    vi.mocked(getPrismaClient).mockReturnValue({ affiliate: { findUnique } } as never);
    vi.mocked(getStripeClient).mockReturnValue({ promotionCodes: { list: listPromotionCodes } } as never);
    listPromotionCodes.mockResolvedValue({ data: [] });
  });

  afterEach(() => vi.unstubAllEnvs());

  it("confirms an approved creator's code without exposing who it belongs to", async () => {
    findUnique.mockResolvedValue({ user_id: "creator-1", status: "approved", stripe_promotion_code_id: "promo_1" });

    const response = await lookup("Maya");

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ code: "MAYA" });
  });

  it.each([
    ["unknown", null],
    ["pending", { user_id: "creator-1", status: "pending", stripe_promotion_code_id: null }],
  ])("404s for %s codes", async (_label, affiliate) => {
    findUnique.mockResolvedValue(affiliate);

    expect((await lookup("maya")).status).toBe(404);
  });

  it("confirms a house code: an active Stripe promotion code on the creator coupon", async () => {
    findUnique.mockResolvedValue(null);
    listPromotionCodes.mockResolvedValue({
      data: [{ id: "promo_ph", promotion: { type: "coupon", coupon: "creator-20-3mo" }, expires_at: null }],
    });

    const response = await lookup("producthunt");

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ code: "PRODUCTHUNT" });
  });

  describe("account-aware checkout", () => {
    const source = "acct_source";
    const destination = "acct_destination";
    const sourceList = vi.fn();
    const destinationList = vi.fn();
    const user = vi.fn();
    const migration = vi.fn();

    beforeEach(() => {
      vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "true");
      vi.stubEnv("STRIPE_SOURCE_ACCOUNT_ID", source);
      vi.stubEnv("STRIPE_DESTINATION_ACCOUNT_ID", destination);
      vi.stubEnv("STRIPE_CHECKOUT_ACCOUNT_ID", destination);
      vi.stubEnv("STRIPE_CREATOR_COUPON_ID", "creator-source");
      vi.stubEnv("STRIPE_DESTINATION_CREATOR_COUPON_ID", "creator-destination");
      vi.stubEnv("STRIPE_DESTINATION_PROMOTION_CODE_MAP", JSON.stringify({ promo_source: "promo_destination" }));
      vi.mocked(auth.api.getSession).mockResolvedValue(null);
      vi.mocked(getPrismaClient).mockReturnValue({
        affiliate: { findUnique },
        user: { findUniqueOrThrow: user },
        stripeAccountMigration: { findUnique: migration },
      } as never);
      migration.mockResolvedValue(null);
      findUnique.mockResolvedValue(null);
      sourceList.mockImplementation(async () => ({
        data: [{ id: "promo_house_source", promotion: { coupon: "creator-source" }, expires_at: null }],
      }));
      destinationList.mockImplementation(async () => ({
        data: [{ id: "promo_house_destination", promotion: { coupon: "creator-destination" }, expires_at: null }],
      }));
      vi.mocked(verifiedStripeClient).mockImplementation(async (account) => ({
        promotionCodes: { list: account === source ? sourceList : destinationList },
      }) as never);
      // Exercise the real context guard which throws in production, rather than
      // returning a client regardless of whether the route established a scope.
      vi.mocked(getStripeClient).mockImplementation(() => scopedStripeClient(() => {
        throw new Error("Unexpected legacy Stripe fallback");
      }));
    });

    it.each([source, destination])("validates an anonymous house code on checkout account %s", async (account) => {
      vi.stubEnv("STRIPE_CHECKOUT_ACCOUNT_ID", account);
      const response = await lookup("producthunt");

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ code: "PRODUCTHUNT" });
      expect(verifiedStripeClient).toHaveBeenCalledWith(account);
      const selectedList = account === source ? sourceList : destinationList;
      const otherList = account === source ? destinationList : sourceList;
      expect(selectedList).toHaveBeenCalledWith({ code: "PRODUCTHUNT", active: true, limit: 1 });
      expect(otherList).not.toHaveBeenCalled();
    });

    it("validates an approved creator using its destination promotion mapping", async () => {
      findUnique.mockResolvedValue({ user_id: "creator-1", status: "approved", stripe_promotion_code_id: "promo_source" });

      const response = await lookup("maya");

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ code: "MAYA" });
      expect(verifiedStripeClient).toHaveBeenCalledWith(destination);
    });

    it("uses the signed-in user's source account even when new checkout uses destination", async () => {
      vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "buyer-1" } } as never);
      user.mockResolvedValue({ stripe_account_id: source });

      expect((await lookup("producthunt")).status).toBe(200);
      expect(user).toHaveBeenCalledWith({ where: { id: "buyer-1" } });
      expect(verifiedStripeClient).toHaveBeenCalledWith(source);
      expect(sourceList).toHaveBeenCalled();
      expect(destinationList).not.toHaveBeenCalled();
    });

    it("keeps anonymous and signed-in account scopes isolated during concurrent validation", async () => {
      vi.stubEnv("STRIPE_CHECKOUT_ACCOUNT_ID", source);
      vi.mocked(auth.api.getSession).mockImplementation(async (context) =>
        (context?.headers as Headers).has("cookie") ? { user: { id: "buyer-1" } } as never : null);
      user.mockResolvedValue({ stripe_account_id: destination });

      const responses = await Promise.all([
        lookup("producthunt"),
        GET(new Request("http://localhost/api/affiliates/offer?code=producthunt", { headers: { cookie: "demo-session" } })),
      ]);

      expect(responses.map((response) => response.status)).toEqual([200, 200]);
      expect(sourceList).toHaveBeenCalledOnce();
      expect(destinationList).toHaveBeenCalledOnce();
      expect(() => getStripeClient()).toThrow("Stripe operation requires an account context");
    });

    it("holds an unmapped creator offer instead of marking it invalid or applying a source ID", async () => {
      vi.stubEnv("STRIPE_DESTINATION_PROMOTION_CODE_MAP", "{}");
      findUnique.mockResolvedValue({ user_id: "creator-1", status: "approved", stripe_promotion_code_id: "promo_source" });

      const response = await lookup("maya");

      expect(response.status).toBe(409);
      await expect(response.json()).resolves.toMatchObject({ code: "billing_transfer_pending" });
      expect(sourceList).not.toHaveBeenCalled();
    });

    it("still returns 404 for unknown codes on the correct account", async () => {
      destinationList.mockResolvedValue({ data: [] });

      expect((await lookup("unknown")).status).toBe(404);
      expect(sourceList).not.toHaveBeenCalled();
    });
  });
});
