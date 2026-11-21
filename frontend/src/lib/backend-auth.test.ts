import crypto from "crypto";
import { afterEach, describe, expect, it } from "vitest";
import { buildBackendAuthHeaders } from "./backend-auth";

describe("buildBackendAuthHeaders", () => {
  const env = process.env;

  afterEach(() => {
    process.env = env;
  });

  it("signs userId without teamId for personal workspace requests", () => {
    process.env = { ...env, BACKEND_AUTH_SECRET: "test-secret" };
    const headers = buildBackendAuthHeaders("user-123");

    expect(headers["x-supoclip-user-id"]).toBe("user-123");
    expect(headers["x-supoclip-team-id"]).toBeUndefined();
    const expected = crypto
      .createHmac("sha256", "test-secret")
      .update(`user-123:${headers["x-supoclip-ts"]}`)
      .digest("hex");
    expect(headers["x-supoclip-signature"]).toBe(expected);
  });

  it("includes and signs teamId when a team workspace is active", () => {
    process.env = { ...env, BACKEND_AUTH_SECRET: "test-secret" };
    const headers = buildBackendAuthHeaders("user-123", "team-456");

    expect(headers["x-supoclip-user-id"]).toBe("user-123");
    expect(headers["x-supoclip-team-id"]).toBe("team-456");
    const expected = crypto
      .createHmac("sha256", "test-secret")
      .update(`user-123:team-456:${headers["x-supoclip-ts"]}`)
      .digest("hex");
    expect(headers["x-supoclip-signature"]).toBe(expected);
  });
});
