import type Stripe from "stripe";
import type { MockInstance } from "vitest";
import { handleMigrationEvent } from "@/lib/stripe-migration-engine";
import {
  createAccountWebhookHandler,
  describeWebhookError,
  type AccountWebhookDependencies,
} from "@/server/stripe-account-webhook";
import type { WebhookClaim } from "@/server/stripe-webhook-inbox";

vi.mock("@/lib/stripe-migration-engine", () => ({
  handleMigrationEvent: vi.fn(),
}));

const SECRET_EMAIL = "customer@example.com";
const event = {
  id: "evt_123",
  type: "customer.subscription.updated",
  livemode: true,
  data: { object: { id: "sub_123", customer: "cus_123", email: SECRET_EMAIL } },
} as unknown as Stripe.Event;
const claimed: WebhookClaim = { kind: "claimed", key: "acct:live:evt_123", token: "lease" };

function setup(overrides: Partial<AccountWebhookDependencies> = {}) {
  const dependencies = {
    store: {},
    client: vi.fn().mockResolvedValue({}),
    signatureClient: () => ({
      webhooks: { constructEventAsync: vi.fn().mockResolvedValue(event) },
    }),
    secret: () => "whsec_test",
    assertMode: () => {},
    claim: vi.fn().mockResolvedValue(claimed),
    finish: vi.fn().mockResolvedValue(undefined),
    dispatch: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as AccountWebhookDependencies;
  const handler = createAccountWebhookHandler(dependencies);
  const send = () =>
    handler(
      new Request("https://example.test/api/billing/webhook/acct_1", {
        method: "POST",
        headers: { "stripe-signature": "t=1,v1=sig" },
        body: JSON.stringify(event),
      }),
      "acct_1",
    );
  return { dependencies, send };
}

/** Everything logged, as one string, to prove no payload or message leaked. */
function logged(...spies: MockInstance<typeof console.error>[]) {
  return JSON.stringify(spies.flatMap((spy) => spy.mock.calls));
}

describe("account webhook diagnostics", () => {
  let error: MockInstance<typeof console.error>;
  let warn: MockInstance<typeof console.warn>;

  beforeEach(() => {
    vi.mocked(handleMigrationEvent).mockResolvedValue(false);
    error = vi.spyOn(console, "error").mockImplementation(() => {});
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("logs nothing for a processed event", async () => {
    const { dependencies, send } = setup();
    const response = await send();
    expect(response.status).toBe(200);
    expect(dependencies.finish).toHaveBeenCalledWith(claimed, true);
    expect(error).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });

  it("tells a busy lease apart from a failed reconciliation, both still 503", async () => {
    const busy = setup({ claim: vi.fn().mockResolvedValue({ kind: "busy" }) });
    const busyResponse = await busy.send();
    expect(busyResponse.status).toBe(503);
    const busyBody = await busyResponse.json();
    expect(busyBody).toMatchObject({ error: "Event is already processing", reason: "busy" });
    expect(busy.dependencies.dispatch).not.toHaveBeenCalled();
    expect(busy.dependencies.finish).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith("Stripe account webhook busy", {
      reference: busyBody.reference,
      accountId: "acct_1",
      eventId: "evt_123",
      eventType: "customer.subscription.updated",
      livemode: true,
      stage: "busy",
    });
    expect(error).not.toHaveBeenCalled();

    const failure = Object.assign(
      new Error(`No such customer for ${SECRET_EMAIL}`),
      { name: "StripeInvalidRequestError", type: "StripeInvalidRequestError", code: "resource_missing", statusCode: 404 },
    );
    const failed = setup({ dispatch: vi.fn().mockRejectedValue(failure) });
    const failedResponse = await failed.send();
    expect(failedResponse.status).toBe(503);
    const failedBody = await failedResponse.json();
    expect(failedBody).toMatchObject({
      error: "Stripe reconciliation failed; retry required",
      reason: "reconcile",
    });
    expect(failedBody.reference).not.toBe(busyBody.reference);
    expect(failed.dependencies.finish).toHaveBeenCalledWith(claimed, false);
    expect(error).toHaveBeenCalledWith("Stripe account webhook failed", {
      reference: failedBody.reference,
      accountId: "acct_1",
      eventId: "evt_123",
      eventType: "customer.subscription.updated",
      livemode: true,
      stage: "reconcile",
      error: {
        name: "StripeInvalidRequestError",
        type: "StripeInvalidRequestError",
        code: "resource_missing",
        statusCode: 404,
      },
    });
    expect(logged(error, warn)).not.toContain(SECRET_EMAIL);
    expect(logged(error, warn)).not.toContain("No such customer");
  });

  it("names a failure to mark the event processed", async () => {
    const finish = vi
      .fn()
      .mockRejectedValueOnce(new Error("Stripe event lease changed during processing"))
      .mockResolvedValueOnce(undefined);
    const { send } = setup({ finish });
    const response = await send();
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ reason: "finish" });
    expect(finish).toHaveBeenNthCalledWith(2, claimed, false);
    expect(error).toHaveBeenCalledWith(
      "Stripe account webhook failed",
      expect.objectContaining({ stage: "finish", error: { name: "Error" } }),
    );
  });

  it("logs and rethrows when the claim or the release fails, as before", async () => {
    const dbError = Object.assign(new Error(`timeout for ${SECRET_EMAIL}`), {
      name: "PrismaClientKnownRequestError",
      code: "P1001",
    });
    const claimFails = setup({ claim: vi.fn().mockRejectedValue(dbError) });
    await expect(claimFails.send()).rejects.toBe(dbError);
    expect(error).toHaveBeenLastCalledWith(
      "Stripe account webhook failed",
      expect.objectContaining({
        stage: "claim",
        error: { name: "PrismaClientKnownRequestError", code: "P1001" },
      }),
    );

    const releaseError = new Error("Stripe event lease changed during processing");
    const releaseFails = setup({
      dispatch: vi.fn().mockRejectedValue(new TypeError("boom")),
      finish: vi.fn().mockRejectedValue(releaseError),
    });
    await expect(releaseFails.send()).rejects.toBe(releaseError);
    const stages = error.mock.calls.map(([, details]: unknown[]) => (details as { stage: string }).stage);
    expect(stages).toEqual(["claim", "reconcile", "release"]);
    expect(logged(error, warn)).not.toContain(SECRET_EMAIL);
  });

  it("keeps rejected signatures and duplicates quiet", async () => {
    const duplicate = setup({ claim: vi.fn().mockResolvedValue({ kind: "duplicate" }) });
    expect((await duplicate.send()).status).toBe(200);
    const invalid = setup({
      signatureClient: () =>
        ({ webhooks: { constructEventAsync: vi.fn().mockRejectedValue(new Error("bad")) } }) as unknown as Stripe,
    });
    expect((await invalid.send()).status).toBe(400);
    expect(error).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });
});

describe("describeWebhookError", () => {
  it("drops messages, stacks and codes that are not identifiers", () => {
    expect(describeWebhookError("raw string")).toEqual({ name: "string" });
    expect(
      describeWebhookError(Object.assign(new Error("secret"), { code: "has spaces and @email" })),
    ).toEqual({ name: "Error" });
  });
});
