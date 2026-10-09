import { GET } from "./route";
import { getPrismaClient } from "@/server/prisma";

vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));

const lookup = (code: string) => GET(new Request(`http://localhost/api/affiliates/offer?code=${code}`));

describe("GET /api/affiliates/offer", () => {
  const findUnique = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getPrismaClient).mockReturnValue({ affiliate: { findUnique } } as never);
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
});
