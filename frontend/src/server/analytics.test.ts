import { getPostHogServer } from "@/server/posthog";

import { captureServerEvent, deterministicUuid, flushServerEvents, intervalFromPeriod, WebhookAnalytics } from "./analytics";

vi.mock("@/server/posthog", () => ({ getPostHogServer: vi.fn() }));

const started = {
  event: "subscription_started",
  distinctId: "user-1",
  properties: { plan: "pro", interval: "month", provider: "stripe", trial: false },
  set: { plan: "pro", subscription_status: "active" },
} as const;

describe("server analytics", () => {
  const capture = vi.fn();

  beforeEach(() => {
    capture.mockReset();
    vi.mocked(getPostHogServer).mockReturnValue({ capture } as never);
  });

  it("is a no-op without a PostHog key", async () => {
    vi.mocked(getPostHogServer).mockReturnValue(null);
    captureServerEvent(started);
    const analytics = new WebhookAnalytics("evt_1");
    analytics.add(started);
    const claim = vi.fn();
    await analytics.flush(claim);
    expect(capture).not.toHaveBeenCalled();
    // Self-hosted installs never write claim rows.
    expect(claim).not.toHaveBeenCalled();
  });

  it("sends person properties with $set", () => {
    captureServerEvent(started);
    expect(capture).toHaveBeenCalledWith({
      distinctId: "user-1",
      event: "subscription_started",
      properties: {
        plan: "pro",
        interval: "month",
        provider: "stripe",
        trial: false,
        platform: "web-server",
        $set: { plan: "pro", subscription_status: "active" },
      },
    });
  });

  it("never throws when the client fails", () => {
    capture.mockImplementation(() => {
      throw new Error("down");
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(() => captureServerEvent(started)).not.toThrow();
    warn.mockRestore();
  });

  it("makes stable, valid UUIDs", () => {
    const uuid = deterministicUuid("evt_1", "subscription_started", "user-1");
    expect(uuid).toBe(deterministicUuid("evt_1", "subscription_started", "user-1"));
    expect(uuid).not.toBe(deterministicUuid("evt_2", "subscription_started", "user-1"));
    expect(uuid).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("holds webhook events until flushed and gives each a deterministic id", async () => {
    const at = new Date("2026-11-01T00:00:00Z");
    const analytics = new WebhookAnalytics("evt_1", at);
    analytics.add(started);
    expect(capture).not.toHaveBeenCalled();

    await analytics.flush();
    expect(capture).toHaveBeenCalledTimes(1);
    expect(capture.mock.calls[0][0]).toMatchObject({
      uuid: deterministicUuid("evt_1", "subscription_started", "user-1"),
      timestamp: at,
    });

    // Flushing again sends nothing new.
    await analytics.flush();
    expect(capture).toHaveBeenCalledTimes(1);
  });

  it("sends a claimed fact once across deliveries", async () => {
    const claimed = new Set<string>();
    const claim = vi.fn(async (key: string) => {
      if (claimed.has(key)) return false;
      claimed.add(key);
      return true;
    });
    const renewed = {
      event: "subscription_renewed",
      distinctId: "user-1",
      properties: { plan: "pro", interval: "month", provider: "stripe" },
    } as const;

    for (const eventId of ["evt_paid", "evt_payment_succeeded"]) {
      const analytics = new WebhookAnalytics(eventId);
      analytics.add(renewed, { claimKey: "subscription_renewed:in_1" });
      await analytics.flush(claim);
    }

    expect(claim).toHaveBeenCalledTimes(2);
    expect(capture).toHaveBeenCalledTimes(1);
    // The id follows the invoice, not the webhook event, so PostHog dedupes too.
    expect(capture.mock.calls[0][0].uuid).toBe(
      deterministicUuid("subscription_renewed:in_1", "subscription_renewed", "user-1"),
    );
  });

  it("skips a claimed fact when the claim fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const analytics = new WebhookAnalytics("evt_1");
    analytics.add(started, { claimKey: "k" });
    await expect(analytics.flush(() => Promise.reject(new Error("db down")))).resolves.toBeUndefined();
    expect(capture).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("waits for queued events to be sent before the webhook responds", async () => {
    const flush = vi.fn().mockResolvedValue(undefined);
    vi.mocked(getPostHogServer).mockReturnValue({ capture, flush } as never);
    const analytics = new WebhookAnalytics("evt_flush");
    analytics.add(started);
    await analytics.flush();
    expect(capture).toHaveBeenCalledTimes(1);
    expect(flush).toHaveBeenCalledTimes(1);
  });

  it("does not hold the webhook when PostHog is slow", async () => {
    vi.useFakeTimers();
    try {
      const flush = vi.fn(() => new Promise<void>(() => {}));
      vi.mocked(getPostHogServer).mockReturnValue({ capture, flush } as never);
      const done = flushServerEvents(50);
      await vi.advanceTimersByTimeAsync(60);
      await expect(done).resolves.toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads the interval from the period length", () => {
    expect(intervalFromPeriod(0, 31 * 86400)).toBe("month");
    expect(intervalFromPeriod(0, 365 * 86400)).toBe("year");
    expect(intervalFromPeriod(null, 10)).toBeNull();
    expect(intervalFromPeriod(10, 10)).toBeNull();
  });
});
