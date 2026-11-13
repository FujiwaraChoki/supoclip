import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

async function loadWrapper() {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_DATAFAST_WEBSITE_ID", "site");
  vi.stubEnv("NEXT_PUBLIC_DATAFAST_DOMAIN", "example.com");
  return import("./datafast");
}

const securityError = () => {
  throw new DOMException("localStorage is blocked", "SecurityError");
};

describe("datafast wrapper", () => {
  beforeEach(() => {
    window.datafast = undefined;
    window.tilly = undefined;
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("still reaches Tilly and does not throw when DataFast throws", async () => {
    const { track } = await loadWrapper();
    const tilly = vi.fn();
    window.tilly = tilly as never;
    window.datafast = vi.fn(securityError) as never;

    expect(() => track("signup_completed", { auth_method: "email" })).not.toThrow();
    expect(tilly).toHaveBeenCalledWith("signup_completed", { auth_method: "email" });
    expect(window.datafast).toHaveBeenCalledTimes(1);
  });

  it("still reaches DataFast and does not throw when Tilly throws", async () => {
    const { track } = await loadWrapper();
    window.tilly = vi.fn(() => {
      throw new Error("tilly broke");
    }) as never;
    const datafast = vi.fn();
    window.datafast = datafast as never;

    expect(() => track("signup_completed")).not.toThrow();
    expect(datafast).toHaveBeenCalledWith("signup_completed");
  });

  it("does not throw from identify when DataFast throws", async () => {
    const { identify } = await loadWrapper();
    window.datafast = vi.fn(securityError) as never;

    expect(() => identify({ user_id: "u1" })).not.toThrow();
    expect(window.datafast).toHaveBeenCalledWith("identify", { user_id: "u1" });
  });
});
