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

describe("/api/billing/checkout", () => {
  const env = process.env;

  beforeEach(() => {
    vi.resetAllMocks();
    process.env = {
      ...env,
      STRIPE_PRO_PRICE_ID: "price_pro",
      STRIPE_SCALE_PRICE_ID: "price_scale",
      NEXT_PUBLIC_APP_URL: "http://localhost:3000",
    };
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: "user-1", email: "user@example.com", name: "User" },
    } as never);
    vi.mocked(prisma.user.findUnique).mockResolvedValue({
      stripe_customer_id: "cus_123",
    } as never);
  });

  afterAll(() => {
    process.env = env;
  });

  it("creates checkout for the selected Scale plan", async () => {
    const stripe = {
      checkout: {
        sessions: {
          create: vi.fn().mockResolvedValue({ url: "https://checkout.example/scale" }),
        },
      },
    };
    vi.mocked(getStripeClient).mockReturnValue(stripe as never);

    const response = await POST(
      new Request("http://localhost/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan: "scale" }),
      }),
    );

    expect(response.status).toBe(200);
    expect(stripe.checkout.sessions.create).toHaveBeenCalledWith(
      expect.objectContaining({
        line_items: [{ price: "price_scale", quantity: 1 }],
        metadata: { userId: "user-1", plan: "scale" },
      }),
    );
    await expect(response.json()).resolves.toEqual({ url: "https://checkout.example/scale" });
  });

  it("rejects an unknown billing plan", async () => {
    const response = await POST(
      new Request("http://localhost/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan: "enterprise" }),
      }),
    );

    expect(response.status).toBe(400);
  });

  it("returns a configuration error when the selected plan has no Stripe price", async () => {
    process.env.STRIPE_SCALE_PRICE_ID = "";

    const response = await POST(
      new Request("http://localhost/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan: "scale" }),
      }),
    );

    expect(response.status).toBe(500);
  });
});
