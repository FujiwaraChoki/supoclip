// @vitest-environment node
import { betterAuth } from "better-auth";
import { memoryAdapter } from "better-auth/adapters/memory";
import { expect, it } from "vitest";
import { getAuthTrustedOrigins } from "./auth-origins";

it("allows legacy self-hosted signup while rejecting an untrusted request origin", async () => {
  const auth = betterAuth({
    baseURL: "http://localhost:3107",
    secret: "test-origin-validation-secret-at-least-32-chars",
    database: memoryAdapter({ user: [], session: [], account: [], verification: [] }),
    trustedOrigins: getAuthTrustedOrigins({ NEXT_PUBLIC_APP_URL: "http://localhost:3107" }),
    emailAndPassword: { enabled: true },
    advanced: { disableOriginCheck: false, disableCSRFCheck: false },
  });
  const request = (origin: string) => new Request("http://localhost:3001/api/auth/sign-up/email", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: origin, Cookie: "browser-preference=test" },
    body: JSON.stringify({ name: "Local user", email: "origin-test@example.com", password: "test-password-123" }),
  });
  const rejected = await auth.handler(request("https://untrusted.example"));
  expect(rejected.status).toBe(403);
  expect((await rejected.json()).code).toBe("INVALID_ORIGIN");
  const accepted = await auth.handler(request("http://localhost:3001"));
  expect(accepted.status).toBe(200);
  expect((await accepted.json()).user.email).toBe("origin-test@example.com");
});
