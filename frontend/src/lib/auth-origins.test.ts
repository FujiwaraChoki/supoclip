import { describe, expect, it } from "vitest";
import { getAuthTrustedOrigins } from "./auth-origins";

describe("authentication browser origins", () => {
  it("accepts both new and legacy self-hosted URLs without trusting arbitrary hosts", () => {
    const origins = getAuthTrustedOrigins({ NEXT_PUBLIC_APP_URL: "http://localhost:3107" });
    expect(origins).toEqual(expect.arrayContaining([
      "http://localhost:3107", "http://localhost:3001", "http://127.0.0.1:3001", "http://127.0.0.1:3107",
    ]));
    expect(origins).not.toContain("http://localhost:3000");
    expect(origins).not.toContain("https://attacker.example");
  });

  it("preserves hosted production's origin list when no extras are configured", () => {
    expect(getAuthTrustedOrigins({
      NEXT_PUBLIC_SELF_HOST: "false", NEXT_PUBLIC_APP_URL: "https://www.supoclip.com",
      BETTER_AUTH_URL: "https://www.supoclip.com",
    })).toEqual([
      "https://www.supoclip.com", "http://localhost:3107", "http://sp.localhost:3107", "http://supoclip.localhost:3107",
    ]);
  });

  it("reads explicit origins, normalizes and deduplicates them, and rejects invalid schemes and credentials", () => {
    const origins = getAuthTrustedOrigins({
      NEXT_PUBLIC_SELF_HOST: "false",
      BETTER_AUTH_TRUSTED_ORIGINS: " https://clips.example:8443/ , https://clips.example:8443, malformed, javascript:alert(1), https://user:secret@example.com, https://*.example.com",
    });
    expect(origins).toContain("https://clips.example:8443");
    expect(origins.filter((origin) => origin === "https://clips.example:8443")).toHaveLength(1);
    expect(origins).not.toContain("https://example.com");
    expect(origins).not.toContain("https://*.example.com");
    expect(origins.every((origin) => origin.startsWith("http"))).toBe(true);
  });
});
