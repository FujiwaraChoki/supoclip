import { POST } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getPrismaClient } from "@/server/prisma";

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(),
}));

describe("/api/billing/revenuecat-webhook", () => {
  const env = process.env;

  beforeEach(() => {
    vi.resetAllMocks();
    process.env = {
      ...env,
      REVENUECAT_WEBHOOK_AUTH_HEADER: "rc_secret",
    };
    vi.mocked(fetchBackend).mockResolvedValue(new Response("{}", { status: 200 }));
  });

  afterAll(() => {
    process.env = env;
  });

  function createRequest(body: unknown, authorization: string | null = "rc_secret") {
    return new Request("http://localhost/api/billing/revenuecat-webhook", {
      method: "POST",
      headers: authorization ? { Authorization: authorization } : {},
      body: JSON.stringify(body),
    });
  }

  function event(overrides: Record<string, unknown> = {}) {
    return {
      event: {
        id: "rc_evt_1",
        type: "INITIAL_PURCHASE",
        app_user_id: "user-1",
        product_id: "com.samihindi.supoclip.pro.monthly",
        period_type: "NORMAL",
        purchased_at_ms: 1770000000000,
        expiration_at_ms: 1772592000000,
        ...overrides,
      },
      api_version: "1.0",
    };
  }

  it.each([null, "wrong_secret"])(
    "rejects requests with missing or wrong auth header",
    async (authorization) => {
      const response = await POST(createRequest(event(), authorization));

      expect(response.status).toBe(401);
    },
  );

  it("treats duplicate event IDs as idempotent without side effects", async () => {
    const update = vi.fn();
    vi.mocked(getPrismaClient).mockReturnValue({
      revenueCatWebhookEvent: {
        create: vi.fn().mockRejectedValue({ code: "P2002" }),
      },
      user: {
        update,
      },
    } as never);

    const response = await POST(createRequest(event()));

    expect(response.status).toBe(200);
    expect(update).not.toHaveBeenCalled();
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("grants Pro for an initial purchase", async () => {
    const update = vi.fn().mockResolvedValue({});
    vi.mocked(getPrismaClient).mockReturnValue({
      revenueCatWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        findUnique: vi.fn().mockResolvedValue({ id: "user-1" }),
        update,
      },
    } as never);

    const response = await POST(createRequest(event()));

    expect(response.status).toBe(200);
    expect(update).toHaveBeenCalledWith({
      where: { id: "user-1" },
      data: expect.objectContaining({
        plan: "pro",
        subscription_status: "active",
        subscription_provider: "apple",
        trial_ends_at: null,
      }),
    });
    expect(fetchBackend).toHaveBeenCalled();
  });

  it("downgrades an Apple user on expiration", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });
    vi.mocked(getPrismaClient).mockReturnValue({
      revenueCatWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        findUnique: vi.fn().mockResolvedValue({ id: "user-1" }),
        updateMany,
      },
    } as never);

    const response = await POST(createRequest(event({ type: "EXPIRATION" })));

    expect(response.status).toBe(200);
    expect(updateMany).toHaveBeenCalledWith({
      where: {
        id: "user-1",
        subscription_provider: "apple",
        OR: [
          { billing_period_end: null },
          { billing_period_end: { lte: new Date(1772592000000) } },
        ],
      },
      data: {
        plan: "free",
        subscription_status: "inactive",
        subscription_provider: null,
        billing_period_start: null,
        billing_period_end: null,
        trial_ends_at: null,
      },
    });
    expect(fetchBackend).toHaveBeenCalled();
  });

  it("does not treat PRODUCT_CHANGE as an immediate grant", async () => {
    const update = vi.fn();
    const updateMany = vi.fn();
    vi.mocked(getPrismaClient).mockReturnValue({
      revenueCatWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        findUnique: vi.fn().mockResolvedValue({ id: "user-1" }),
        update,
        updateMany,
      },
    } as never);

    const response = await POST(
      createRequest(
        event({
          type: "PRODUCT_CHANGE",
          product_id: "com.samihindi.supoclip.scale.monthly",
        }),
      ),
    );

    expect(response.status).toBe(200);
    expect(update).not.toHaveBeenCalled();
    expect(updateMany).not.toHaveBeenCalled();
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("does not downgrade a Stripe user on expiration", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 0 });
    vi.mocked(getPrismaClient).mockReturnValue({
      revenueCatWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        findUnique: vi.fn().mockResolvedValue({ id: "user-1" }),
        updateMany,
      },
    } as never);

    const response = await POST(createRequest(event({ type: "EXPIRATION" })));

    expect(response.status).toBe(200);
    expect(updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: expect.objectContaining({ subscription_provider: "apple" }),
      }),
    );
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("acknowledges anonymous app user IDs with no alias as no-ops", async () => {
    const update = vi.fn();
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.mocked(getPrismaClient).mockReturnValue({
      revenueCatWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        findUnique: vi.fn(),
        update,
      },
    } as never);

    const response = await POST(
      createRequest(event({ app_user_id: "$RCAnonymousID:abc", aliases: [] })),
    );

    expect(response.status).toBe(200);
    expect(update).not.toHaveBeenCalled();
    expect(fetchBackend).not.toHaveBeenCalled();
    consoleWarn.mockRestore();
  });

  it("maps trial RevenueCat purchases to trialing", async () => {
    const update = vi.fn().mockResolvedValue({});
    vi.mocked(getPrismaClient).mockReturnValue({
      revenueCatWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        findUnique: vi.fn().mockResolvedValue({ id: "user-1" }),
        update,
      },
    } as never);

    const response = await POST(createRequest(event({ period_type: "TRIAL" })));

    expect(response.status).toBe(200);
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          subscription_status: "trialing",
          trial_ends_at: new Date(1772592000000),
        }),
      }),
    );
  });
});
