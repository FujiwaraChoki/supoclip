import { describe, expect, it } from "vitest";

import { isSocialPublishingEnabled } from "./use-social-access";

describe("isSocialPublishingEnabled", () => {
  it("stays hidden unless the server explicitly grants access", () => {
    expect(isSocialPublishingEnabled({ enabled: true })).toBe(true);
    expect(isSocialPublishingEnabled({ enabled: false })).toBe(false);
    expect(isSocialPublishingEnabled({})).toBe(false);
    expect(isSocialPublishingEnabled(null)).toBe(false);
    expect(isSocialPublishingEnabled(undefined)).toBe(false);
  });
});
