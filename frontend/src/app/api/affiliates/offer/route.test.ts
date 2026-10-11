import { GET } from "./route";
import { getStripeClient } from "@/lib/stripe";
import { getPrismaClient } from "@/server/prisma";

vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));
vi.mock("@/lib/stripe", () => ({ getStripeClient: vi.fn() }));

const lookup = (code: string) => GET(new Request(`http://localhost/api/affiliates/offer?code=${code}`));

describe("GET /api/affiliates/offer", () => {
  const findUnique = vi.fn();
  const listPromotionCodes = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getPrismaClient).mockReturnValue({ affiliate: { findUnique } } as never);
    vi.mocked(getStripeClient).mockReturnValue({ promotionCodes: { list: listPromotionCodes } } as never);
    listPromotionCodes.mockResolvedValue({ data: [] });
  });

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
});
