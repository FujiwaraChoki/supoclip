// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { memoryAdapter } from "better-auth/adapters/memory";

const state = vi.hoisted(() => ({
  database: { user: [], session: [], account: [], verification: [] },
  send: vi.fn(),
}));
vi.mock("better-auth/adapters/prisma", () => ({ prismaAdapter: () => memoryAdapter(state.database) }));
vi.mock("@/lib/prisma", () => ({ default: {} }));
vi.mock("@/server/account-deletion", () => ({ prepareAccountDeletion: vi.fn() }));
vi.mock("@/server/backend-api", () => ({ fetchBackend: state.send }));

const CUTOFF = "2026-01-01T00:00:00Z";
process.env.BETTER_AUTH_URL = "http://localhost:3107";
process.env.BETTER_AUTH_SECRET = "test-verification-secret-at-least-32-characters";
process.env.EMAIL_VERIFICATION_REQUIRED_SINCE = CUTOFF;
const { auth, parseVerificationCutoff } = await import("./auth");

afterEach(() => {
  vi.useRealTimers();
  state.send.mockReset();
});

const password = "test-password-123";
const origin = { "content-type": "application/json", origin: "http://localhost:3107" };

function post(path: string, body: object) {
  return auth.handler(new Request(`http://localhost:3107/api/auth/${path}`, {
    method: "POST", headers: origin, body: JSON.stringify(body),
  }));
}

function sentLinks(): string[] {
  return state.send.mock.calls
    .filter(([path]) => path === "/account/verification-email")
    .map(([, init]) => JSON.parse(init.body).url);
}

it("lets accounts from before the cutoff keep signing in unverified", async () => {
  state.send.mockResolvedValue(new Response("{}", { status: 200 }));
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2025-06-01T00:00:00Z"));
  await auth.api.signUpEmail({ body: { name: "Old", email: "old@example.com", password } });
  vi.useRealTimers();
  state.send.mockClear();

  const signin = await post("sign-in/email", { email: "old@example.com", password });
  expect(signin.status).toBe(200);
  expect(signin.headers.get("set-cookie")).toContain("session_token");
  expect(sentLinks()).toHaveLength(0);
});

it("gives a new account its session only through the emailed link", async () => {
  state.send.mockResolvedValue(new Response("{}", { status: 200 }));
  const email = "new@example.com";

  const signup = await post("sign-up/email", { name: "New", email, password, callbackURL: "/invite/abc" });
  expect(signup.status).toBe(200);
  expect((await signup.json()).token).toBeNull();
  expect(signup.headers.get("set-cookie") ?? "").not.toContain("session_token");
  expect(sentLinks()).toHaveLength(1);
  expect(sentLinks()[0]).toContain("callbackURL=%2Finvite%2Fabc");

  // A wrong password says nothing about verification and sends nothing.
  const wrong = await post("sign-in/email", { email, password: "not-the-password" });
  expect(wrong.status).toBe(401);
  expect(sentLinks()).toHaveLength(1);

  const blocked = await post("sign-in/email", { email, password });
  expect(blocked.status).toBe(403);
  expect((await blocked.json()).code).toBe("EMAIL_NOT_VERIFIED");
  expect(blocked.headers.get("set-cookie") ?? "").not.toContain("session_token");
  expect(sentLinks()).toHaveLength(2);
  expect(state.database.session.filter((s: { userId: string }) => s.userId === (state.database.user.find(
    (u: { email: string }) => u.email === email,
  ) as unknown as { id: string }).id)).toHaveLength(0);

  const verified = await auth.handler(new Request(sentLinks()[0]));
  expect(verified.status).toBe(302);
  expect(verified.headers.get("location")).toBe("/invite/abc");
  expect(verified.headers.get("set-cookie")).toContain("session_token");

  const signin = await post("sign-in/email", { email, password });
  expect(signin.status).toBe(200);
  expect(sentLinks()).toHaveLength(2);
});

it("answers a sign-up for a taken address like a new one", async () => {
  state.send.mockResolvedValue(new Response("{}", { status: 200 }));
  await post("sign-up/email", { name: "First", email: "taken@example.com", password });
  const again = await post("sign-up/email", { name: "Second", email: "taken@example.com", password: "other-password-456" });
  expect(again.status).toBe(200);
  expect((await again.json()).token).toBeNull();
  expect(state.database.user.filter((u: { email: string }) => u.email === "taken@example.com")).toHaveLength(1);
});

it("still blocks sign-in when the resent email can't be delivered", async () => {
  state.send.mockResolvedValue(new Response("{}", { status: 200 }));
  await post("sign-up/email", { name: "Down", email: "ses-down@example.com", password });
  state.send.mockResolvedValue(new Response("{}", { status: 503 }));
  const blocked = await post("sign-in/email", { email: "ses-down@example.com", password });
  expect(blocked.status).toBe(403);
  expect((await blocked.json()).code).toBe("EMAIL_NOT_VERIFIED");
});

it("reads the cutoff strictly", () => {
  expect(parseVerificationCutoff(undefined)).toBeNull();
  expect(parseVerificationCutoff("  ")).toBeNull();
  expect(parseVerificationCutoff(CUTOFF)?.toISOString()).toBe("2026-01-01T00:00:00.000Z");
  expect(() => parseVerificationCutoff("soon")).toThrow("EMAIL_VERIFICATION_REQUIRED_SINCE");
});
