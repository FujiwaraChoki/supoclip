// @vitest-environment node
import { createAccountWebhookHandler, type AccountWebhookDependencies } from "./stripe-account-webhook";
import { handleMigrationEvent } from "@/lib/stripe-migration-engine";

vi.mock("@/lib/stripe-migration-engine", () => ({ handleMigrationEvent: vi.fn() }));

const event = { id: "evt_1", type: "charge.refunded", livemode: true, data: { object: { id: "ch_1" } } };

function handler(overrides: Partial<AccountWebhookDependencies> = {}) {
  const deps = {
    signatureClient: () => ({ webhooks: { constructEventAsync: vi.fn().mockResolvedValue(event) } }),
    secret: () => "whsec",
    assertMode: vi.fn(),
    claim: vi.fn().mockResolvedValue({ kind: "claimed" }),
    finish: vi.fn(),
    client: vi.fn().mockResolvedValue({}),
    dispatch: vi.fn(),
    afterEvent: vi.fn(),
    ...overrides,
  } as unknown as AccountWebhookDependencies;
  const request = new Request("http://localhost/api/billing/webhook/acct_us", {
    method: "POST",
    headers: { "stripe-signature": "sig" },
    body: "{}",
  });
  return { deps, run: () => createAccountWebhookHandler(deps)(request, "acct_us") };
}

describe("account webhook afterEvent", () => {
  it("runs for events the migration engine handled itself", async () => {
    vi.mocked(handleMigrationEvent).mockResolvedValue(true);
    const { deps, run } = handler();
    expect((await run()).status).toBe(200);
    expect(deps.dispatch).not.toHaveBeenCalled();
    expect(deps.afterEvent).toHaveBeenCalledWith(event);
  });

  it("runs after ordinary dispatch", async () => {
    vi.mocked(handleMigrationEvent).mockResolvedValue(false);
    const { deps, run } = handler();
    await run();
    expect(deps.dispatch).toHaveBeenCalledWith(event);
    expect(deps.afterEvent).toHaveBeenCalledWith(event);
  });

  it("asks Stripe to redeliver when it fails", async () => {
    vi.mocked(handleMigrationEvent).mockResolvedValue(false);
    const { deps, run } = handler({ afterEvent: vi.fn().mockRejectedValue(new Error("db down")) });
    expect((await run()).status).toBe(503);
    expect(deps.finish).toHaveBeenCalledWith({ kind: "claimed" }, false);
  });
});
