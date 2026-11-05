// @vitest-environment node
import { afterAll, expect, it, vi } from "vitest";
import { memoryAdapter } from "better-auth/adapters/memory";

const state = vi.hoisted(() => ({
  database: { user: [], session: [], account: [], verification: [] },
  send: vi.fn(),
}));
vi.mock("better-auth/adapters/prisma", () => ({ prismaAdapter: () => memoryAdapter(state.database) }));
vi.mock("@/lib/prisma", () => ({ default: {} }));
vi.mock("@/server/account-deletion", () => ({ prepareAccountDeletion: vi.fn() }));
vi.mock("@/server/backend-api", () => ({ fetchBackend: state.send }));

process.env.BETTER_AUTH_URL = "http://localhost:3107";
process.env.BETTER_AUTH_SECRET = "test-verification-secret-at-least-32-characters";
const { auth } = await import("./auth");
afterAll(() => vi.useRealTimers());

it("verifies ownership with a signed link while preserving ordinary signup and sign-in", async () => {
  const signup = await auth.api.signUpEmail({ body: {
    name: "Ada", email: "verification@example.com", password: "test-password-123",
  } });
  expect(signup.user.emailVerified).toBe(false);
  expect(state.send).not.toHaveBeenCalled();
  const signin = await auth.api.signInEmail({ body: {
    email: "verification@example.com", password: "test-password-123",
  } });
  expect(signin.token).toBeTruthy();

  state.send.mockResolvedValue(new Response('{}', { status: 200 }));
  await auth.api.sendVerificationEmail({ body: {
    email: "verification@example.com", callbackURL: "/settings/api-keys",
  } });
  expect(state.send).toHaveBeenCalledWith("/account/verification-email", expect.objectContaining({ userId: signup.user.id }));
  const { url } = JSON.parse(state.send.mock.calls[0][1].body);
  expect(url).toContain("/api/auth/verify-email?token=");
  const invalidUrl = new URL(url);
  invalidUrl.searchParams.set("token", "invalid-token");
  const invalid = await auth.handler(new Request(invalidUrl));
  expect(invalid.headers.get("location")).toContain("error=INVALID_TOKEN");
  expect((state.database.user[0] as { emailVerified: boolean }).emailVerified).toBe(false);

  vi.useFakeTimers({ toFake: ["Date"] });
  const now = Date.now();
  vi.setSystemTime(now + 2 * 60 * 60 * 1000);
  const expired = await auth.handler(new Request(url));
  expect(expired.headers.get("location")).toContain("error=TOKEN_EXPIRED");
  expect((state.database.user[0] as { emailVerified: boolean }).emailVerified).toBe(false);
  vi.setSystemTime(now);
  vi.useRealTimers();

  const verified = await auth.handler(new Request(url));
  expect(verified.status).toBe(302);
  expect(verified.headers.get("location")).toContain("/settings/api-keys");
  expect((state.database.user[0] as { emailVerified: boolean }).emailVerified).toBe(true);
});

it("reports delivery failures from the configured sender without exposing tokens", async () => {
  state.send.mockResolvedValue(new Response('{}', { status: 503 }));
  await expect(auth.options.emailVerification!.sendVerificationEmail!({
    user: { id: "user-1", email: "failure@example.com", name: "Grace", emailVerified: false, createdAt: new Date(), updatedAt: new Date() },
    url: "https://example.com/api/auth/verify-email?token=secret-token",
    token: "secret-token",
  })).rejects.toThrow("Verification email delivery failed (503)");
});

it("revokes existing sessions after a password reset and accepts only the new password", async () => {
  const email = "password-reset@example.com";
  const password = "original-password-123";
  const newPassword = "replacement-password-456";
  state.send.mockClear();
  state.send.mockResolvedValue(new Response("{}", { status: 200 }));
  await auth.api.signUpEmail({ body: { name: "Reset user", email, password } });
  const { headers } = await auth.api.signInEmail({
    body: { email, password },
    returnHeaders: true,
  });
  const sessionHeaders = new Headers({
    cookie: headers.getSetCookie().map((cookie) => cookie.split(";")[0]).join("; "),
  });
  expect((await auth.api.getSession({ headers: sessionHeaders }))?.user.email).toBe(email);

  await auth.api.requestPasswordReset({ body: { email, redirectTo: "/sign-in" } });
  const resetCall = state.send.mock.calls.find(([path]) => path === "/account/password-reset-email");
  expect(resetCall).toBeDefined();
  const { url } = JSON.parse(resetCall![1].body);
  const token = new URL(url).pathname.split("/").at(-1)!;
  await auth.api.resetPassword({ body: { token, newPassword } });

  expect(await auth.api.getSession({ headers: sessionHeaders })).toBeNull();
  await expect(auth.api.signInEmail({ body: { email, password } })).rejects.toThrow();
  expect((await auth.api.signInEmail({ body: { email, password: newPassword } })).token).toBeTruthy();
});
