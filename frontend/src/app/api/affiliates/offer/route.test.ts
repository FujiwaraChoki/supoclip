// Only the Stripe SDK is faked: the real account guard, routing and client cache run.
const fake = vi.hoisted(() => {
  type Code = { id: string; code: string; coupon: string; expires_at: number | null };
  const state = {
    accounts: {} as Record<string, { id: string; codes: Code[] }>,
    constructed: [] as string[],
    calls: [] as { key: string; method: string; args: unknown }[],
  };
  class FakeStripe {
    accounts: { retrieve: () => Promise<{ id: string }> };
    promotionCodes: { list: (params: { code: string }) => Promise<{ data: unknown[] }> };
    constructor(key: string) {
      state.constructed.push(key);
      const account = state.accounts[key];
      if (!account) throw new Error(`Unexpected Stripe key ${key}`);
      this.accounts = {
        retrieve: async () => {
          state.calls.push({ key, method: "accounts.retrieve", args: null });
          return { id: account.id };
        },
      };
      this.promotionCodes = {
        list: async (params) => {
          state.calls.push({ key, method: "promotionCodes.list", args: params });
          return {
            data: account.codes
              .filter((code) => code.code === params.code)
              .map(({ id, coupon, expires_at }) => ({ id, promotion: { type: "coupon", coupon }, expires_at })),
          };
        },
      };
    }
  }
  return { state, FakeStripe };
});

vi.mock("stripe", () => ({ default: fake.FakeStripe }));

const findUnique = vi.fn();
vi.mock("@/server/prisma", () => ({ getPrismaClient: () => ({ affiliate: { findUnique } }) }));

const SOURCE = "acct_source1";
const DESTINATION = "acct_destination1";
const SOURCE_KEY = "sk_test_source";
const DESTINATION_KEY = "sk_test_destination";

const approved = { user_id: "creator-1", status: "approved", stripe_promotion_code_id: "promo_source_maya" };

async function lookup(code: string) {
  // Fresh modules each time: the account client cache must not carry over between tests.
  const { GET } = await import("./route");
  return GET(new Request(`http://localhost/api/affiliates/offer?code=${encodeURIComponent(code)}`));
}

const stripeMethods = () => fake.state.calls.map(({ key, method }) => `${key} ${method}`);

function legacyEnv() {
  vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "");
  vi.stubEnv("STRIPE_SECRET_KEY", SOURCE_KEY);
}

function migrationEnv({ checkout }: { checkout?: string } = {}) {
  vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "true");
  vi.stubEnv("STRIPE_SOURCE_ACCOUNT_ID", SOURCE);
  vi.stubEnv("STRIPE_DESTINATION_ACCOUNT_ID", DESTINATION);
  vi.stubEnv("STRIPE_SECRET_KEY", SOURCE_KEY);
  vi.stubEnv("STRIPE_DESTINATION_SECRET_KEY", DESTINATION_KEY);
  vi.stubEnv("STRIPE_CHECKOUT_ACCOUNT_ID", checkout ?? "");
  vi.stubEnv("STRIPE_DESTINATION_CREATOR_COUPON_ID", "creator-us");
}

describe("GET /api/affiliates/offer", () => {
  beforeEach(() => {
    vi.resetModules();
    findUnique.mockReset();
    findUnique.mockResolvedValue(null);
    fake.state.constructed = [];
    fake.state.calls = [];
    fake.state.accounts = {
      [SOURCE_KEY]: {
        id: SOURCE,
        codes: [
          { id: "promo_source_ph", code: "PRODUCTHUNT", coupon: "creator-20-3mo", expires_at: null },
          { id: "promo_source_only", code: "SOURCEONLY", coupon: "creator-20-3mo", expires_at: null },
        ],
      },
      [DESTINATION_KEY]: {
        id: DESTINATION,
        codes: [
          { id: "promo_destination_ph", code: "PRODUCTHUNT", coupon: "creator-us", expires_at: null },
          { id: "promo_destination_only", code: "USONLY", coupon: "creator-us", expires_at: null },
        ],
      },
    };
    vi.stubEnv("STRIPE_CREATOR_COUPON_ID", "");
    vi.stubEnv("STRIPE_DESTINATION_PROMOTION_CODE_MAP", "");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    // Every client the route built used a test-mode key from this file; nothing can reach live Stripe.
    for (const key of fake.state.constructed) expect(key).toMatch(/^sk_test_/);
  });

  describe("with account migration off", () => {
    beforeEach(legacyEnv);

    it("confirms an approved creator's code without exposing who it belongs to or calling Stripe", async () => {
      findUnique.mockResolvedValue(approved);

      const response = await lookup("Maya");

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ code: "MAYA" });
      expect(fake.state.constructed).toEqual([]);
    });

    it("confirms a house code with the legacy client", async () => {
      const response = await lookup("producthunt");

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ code: "PRODUCTHUNT" });
      expect(stripeMethods()).toEqual([`${SOURCE_KEY} promotionCodes.list`]);
    });

    it.each([
      ["unknown", null],
      ["pending", { user_id: "creator-1", status: "pending", stripe_promotion_code_id: null }],
      ["revoked", { user_id: "creator-1", status: "revoked", stripe_promotion_code_id: "promo_old" }],
    ])("404s for %s codes", async (_label, affiliate) => {
      findUnique.mockResolvedValue(affiliate);

      expect((await lookup("maya")).status).toBe(404);
    });
  });

  describe("with account migration on", () => {
    it("checks a house code in the source account while checkout opens there", async () => {
      migrationEnv();

      const response = await lookup("producthunt");

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ code: "PRODUCTHUNT" });
      expect(stripeMethods()).toEqual([`${SOURCE_KEY} accounts.retrieve`, `${SOURCE_KEY} promotionCodes.list`]);
      expect(fake.state.constructed).toEqual([SOURCE_KEY]);
    });

    it("never confirms a destination-only code while checkout opens in the source", async () => {
      migrationEnv();

      expect((await lookup("usonly")).status).toBe(404);
      expect(fake.state.constructed).not.toContain(DESTINATION_KEY);
    });

    it("checks the destination account, and its coupon, once checkout moves there", async () => {
      migrationEnv({ checkout: DESTINATION });

      const house = await lookup("producthunt");
      expect(house.status).toBe(200);
      await expect(house.json()).resolves.toEqual({ code: "PRODUCTHUNT" });
      expect((await lookup("usonly")).status).toBe(200);
      expect((await lookup("sourceonly")).status).toBe(404);

      expect(fake.state.constructed.every((key) => key === DESTINATION_KEY)).toBe(true);
      expect(stripeMethods()).not.toContain(`${SOURCE_KEY} promotionCodes.list`);
    });

    it("rejects a source promotion code on the destination coupon's account", async () => {
      migrationEnv({ checkout: DESTINATION });
      fake.state.accounts[DESTINATION_KEY].codes.push({
        id: "promo_wrong_coupon",
        code: "WRONGCOUPON",
        coupon: "creator-20-3mo",
        expires_at: null,
      });

      expect((await lookup("wrongcoupon")).status).toBe(404);
    });

    it("confirms an approved creator's code in the source without a promotion code lookup", async () => {
      migrationEnv();
      findUnique.mockResolvedValue(approved);

      const response = await lookup("maya");

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ code: "MAYA" });
      expect(stripeMethods()).toEqual([`${SOURCE_KEY} accounts.retrieve`]);
    });

    it("confirms an approved creator's code in the destination only when it is mapped there", async () => {
      migrationEnv({ checkout: DESTINATION });
      findUnique.mockResolvedValue(approved);

      const unmapped = await lookup("maya");
      expect(unmapped.status).toBe(503);
      await expect(unmapped.json()).resolves.toEqual({
        error: "Your billing transfer is being verified. Please try again shortly.",
      });

      vi.stubEnv("STRIPE_DESTINATION_PROMOTION_CODE_MAP", JSON.stringify({ promo_source_maya: "promo_destmaya" }));
      const mapped = await lookup("maya");
      expect(mapped.status).toBe(200);
      await expect(mapped.json()).resolves.toEqual({ code: "MAYA" });
      expect(stripeMethods()).not.toContain(`${DESTINATION_KEY} promotionCodes.list`);
    });

    it.each(["", "a", "../admin", "x".repeat(40), "supoclip-team", "ma--ya"])(
      "404s for the invalid code %j before any database or Stripe work",
      async (code) => {
        migrationEnv();

        expect((await lookup(code)).status).toBe(404);
        expect(findUnique).not.toHaveBeenCalled();
        expect(fake.state.constructed).toEqual([]);
      },
    );

    it("404s for pending and unknown codes", async () => {
      migrationEnv();
      findUnique.mockResolvedValueOnce({ user_id: "creator-1", status: "pending", stripe_promotion_code_id: null });

      expect((await lookup("maya")).status).toBe(404);
      expect((await lookup("nobody")).status).toBe(404);
    });

    it("refuses to look up codes with credentials that belong to another account", async () => {
      migrationEnv();
      fake.state.accounts[SOURCE_KEY].id = DESTINATION;
      vi.spyOn(console, "error").mockImplementation(() => {});

      expect((await lookup("producthunt")).status).toBe(500);
      expect(stripeMethods()).toEqual([`${SOURCE_KEY} accounts.retrieve`]);
    });

    it("fails closed when the source account is not configured", async () => {
      migrationEnv();
      vi.stubEnv("STRIPE_SOURCE_ACCOUNT_ID", "");
      vi.spyOn(console, "error").mockImplementation(() => {});

      expect((await lookup("producthunt")).status).toBe(500);
      expect(fake.state.constructed).toEqual([]);
    });
  });
});
