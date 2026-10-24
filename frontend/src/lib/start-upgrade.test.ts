import { getStoredCreatorCode, storeCreatorCode } from "./creator-code";
import { startUpgrade } from "./start-upgrade";

vi.mock("@/lib/datafast", () => ({ track: vi.fn() }));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
const bodies = (fetch: { mock: { calls: unknown[][] } }) =>
  fetch.mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));

const storage = new Map<string, string>();

beforeEach(() => {
  storage.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("startUpgrade", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("applies a remembered creator code", async () => {
    storeCreatorCode("MAYA");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { url: "#checkout" }));

    await startUpgrade("pro", "test");

    expect(bodies(fetch)).toEqual([{ plan: "pro", interval: "month", code: "MAYA" }]);
  });

  it("drops a code that stopped being valid and checks out at the regular price", async () => {
    storeCreatorCode("MAYA");
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(400, { error: "That creator code isn't valid", code: "invalid_creator_code" }))
      .mockResolvedValueOnce(json(200, { url: "#checkout" }));

    await startUpgrade("scale", "test");

    expect(bodies(fetch)).toEqual([{ plan: "scale", interval: "month", code: "MAYA" }, { plan: "scale", interval: "month" }]);
    expect(getStoredCreatorCode()).toBeNull();
  });

  it("keeps a valid code when checkout fails for another reason", async () => {
    storeCreatorCode("MAYA");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(400, { error: "Unknown billing plan" }));

    await expect(startUpgrade("pro", "test")).rejects.toThrow("Unknown billing plan");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(getStoredCreatorCode()).toBe("MAYA");
  });

  it("opens the billing portal when a subscription still needs payment", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        json(409, {
          error: "Your last payment failed. Update your payment method to keep your plan.",
          code: "existing_subscription_needs_payment",
        }),
      )
      .mockResolvedValueOnce(json(200, { url: "https://billing.stripe.test/session" }));

    await startUpgrade("pro", "test");

    expect(fetch).toHaveBeenNthCalledWith(2, "/api/billing/portal", { method: "POST" });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("surfaces checkout errors without a code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(400, { error: "Unknown billing plan" }));

    await expect(startUpgrade("pro", "test")).rejects.toThrow("Unknown billing plan");
  });
});
