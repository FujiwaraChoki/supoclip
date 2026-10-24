import { GET } from "./route";
import { auth } from "@/lib/auth";
import { restorePastDueEntitlement } from "@/server/stripe-subscription-sync";

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/server/stripe-subscription-sync", () => ({
  restorePastDueEntitlement: vi.fn(),
}));

const pastDueFree = {
  plan: "free",
  subscription_status: "past_due",
  subscription_provider: "stripe",
};

describe("/api/tasks/billing-summary", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "user-1" } } as never);
  });

  it("reloads billing after a past-due plan is restored from Stripe", async () => {
    vi.mocked(restorePastDueEntitlement).mockResolvedValue(true);
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json(pastDueFree))
      .mockResolvedValueOnce(Response.json({ ...pastDueFree, plan: "pro" }));

    const response = await GET();

    expect(response.status).toBe(200);
    expect(restorePastDueEntitlement).toHaveBeenCalledWith("user-1");
    await expect(response.json()).resolves.toMatchObject({ plan: "pro", subscription_status: "past_due" });
  });

  it("leaves an active summary alone", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ plan: "pro", subscription_status: "active", subscription_provider: "stripe" }),
    );

    const response = await GET();

    expect(restorePastDueEntitlement).not.toHaveBeenCalled();
    await expect(response.json()).resolves.toMatchObject({ plan: "pro", subscription_status: "active" });
  });
});
