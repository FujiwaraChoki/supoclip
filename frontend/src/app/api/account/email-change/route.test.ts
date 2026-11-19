// @vitest-environment node
import { APIError } from "better-auth/api";

import { DELETE, GET, POST } from "./route";
import { auth } from "@/lib/auth";
import { fetchBackend } from "@/server/backend-api";
import * as emailChange from "@/server/email-change";
import { EmailChangeError } from "@/server/email-change";

let requestHeaders = new Headers();

vi.mock("next/headers", () => ({ headers: async () => requestHeaders }));
vi.mock("@/lib/auth", () => ({ auth: { api: { getSession: vi.fn(), verifyPassword: vi.fn() } } }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: () => ({ $queryRawUnsafe: vi.fn() }) }));
vi.mock("@/server/backend-api", () => ({ fetchBackend: vi.fn() }));
vi.mock("@/server/email-change", async () => {
  const actual = await vi.importActual<typeof import("@/server/email-change")>("@/server/email-change");
  return {
    ...actual,
    getEmailChangeState: vi.fn(),
    startEmailChange: vi.fn(),
    resendEmailChangeCode: vi.fn(),
    verifyCurrentEmailCode: vi.fn(),
    submitNewEmail: vi.fn(),
    verifyNewEmailCode: vi.fn(),
    finishEmailChange: vi.fn(),
    cancelEmailChange: vi.fn(),
  };
});

const session = {
  user: { id: "user-1", email: "owner@example.com" },
  session: { id: "session-1", createdAt: "2026-11-19T09:00:00.000Z" },
};

function post(body: unknown, headers: Record<string, string> = {}) {
  return new Request("https://app.example.test/api/account/email-change", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

describe("/api/account/email-change", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubEnv("BETTER_AUTH_SECRET", "test-secret");
    requestHeaders = new Headers({ "x-forwarded-for": "198.51.100.1, 203.0.113.7" });
    vi.mocked(auth.api.getSession).mockResolvedValue(session as never);
  });

  afterEach(() => vi.unstubAllEnvs());

  it("needs a signed-in session for every method", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as never);
    expect((await GET()).status).toBe(401);
    expect((await POST(post({ action: "start" }))).status).toBe(401);
    expect((await DELETE(new Request("https://app.example.test/api/account/email-change", { method: "DELETE" }))).status).toBe(401);
    expect(emailChange.startEmailChange).not.toHaveBeenCalled();
  });

  it("refuses cross-site and non-JSON writes", async () => {
    expect((await POST(post({ action: "start" }, { Origin: "https://evil.example" }))).status).toBe(403);
    const form = new Request("https://app.example.test/api/account/email-change", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "action=start",
    });
    expect((await POST(form)).status).toBe(415);
    expect((await POST(post({ action: "explode" }))).status).toBe(400);
    expect(emailChange.startEmailChange).not.toHaveBeenCalled();
  });

  it("acts for the session's user and the proxy's client IP, never the body's", async () => {
    vi.mocked(emailChange.startEmailChange).mockResolvedValue({ email: "owner@example.com" } as never);
    const response = await POST(post({ action: "start", password: "pw", userId: "someone-else" }));
    expect(response.status).toBe(200);
    const [, actor, input] = vi.mocked(emailChange.startEmailChange).mock.calls[0];
    expect(actor).toEqual({
      userId: "user-1",
      sessionId: "session-1",
      sessionCreatedAt: new Date("2026-11-19T09:00:00.000Z"),
      ip: "203.0.113.7",
    });
    expect(input).toEqual({ password: "pw" });
  });

  it("prefers X-Real-IP", async () => {
    requestHeaders = new Headers({ "x-real-ip": "192.0.2.4", "x-forwarded-for": "198.51.100.1" });
    vi.mocked(emailChange.getEmailChangeState).mockResolvedValue({} as never);
    await GET();
    expect(vi.mocked(emailChange.getEmailChangeState).mock.calls[0][1].ip).toBe("192.0.2.4");
  });

  it("checks the password with Better Auth and treats its 400 as wrong", async () => {
    vi.mocked(emailChange.startEmailChange).mockImplementation(async (deps) => {
      expect(await deps.verifyPassword("right")).toBe(true);
      expect(await deps.verifyPassword("wrong")).toBe(false);
      return {} as never;
    });
    vi.mocked(auth.api.verifyPassword).mockImplementation((async ({ body }: { body: { password: string } }) => {
      if (body.password !== "right") throw new APIError("BAD_REQUEST", { message: "Invalid password" });
      return { status: true };
    }) as never);
    expect((await POST(post({ action: "start", password: "right" }))).status).toBe(200);
    expect(auth.api.verifyPassword).toHaveBeenCalledWith({ body: { password: "right" }, headers: requestHeaders });
  });

  it("sends codes through the backend without exposing them on failure", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(new Response("nope", { status: 503 }));
    vi.mocked(emailChange.resendEmailChangeCode).mockImplementation(async (deps) => {
      await expect(deps.sendEmail("user-1", { kind: "current_code", code: "123456" })).rejects.toThrow("(503)");
      return {} as never;
    });
    await POST(post({ action: "resend" }));
    const [path, init] = vi.mocked(fetchBackend).mock.calls[0];
    expect(path).toBe("/account/email-change-email");
    expect(init).toMatchObject({ method: "POST", userId: "user-1" });
    expect(JSON.parse(String(init?.body))).toEqual({ kind: "current_code", code: "123456" });
  });

  it.each([
    ["verify_current", "verifyCurrentEmailCode", { code: "123456" }],
    ["new_email", "submitNewEmail", { email: "new@example.com" }],
    ["verify_new", "verifyNewEmailCode", { code: "123456" }],
    ["finish", "finishEmailChange", {}],
  ] as const)("routes %s", async (action, fn, extra) => {
    vi.mocked(emailChange[fn]).mockResolvedValue({ email: "new@example.com", sessionsRevoked: 0 } as never);
    const response = await POST(post({ action, ...extra }));
    expect(response.status).toBe(200);
    expect(emailChange[fn]).toHaveBeenCalledTimes(1);
  });

  it.each([
    [new EmailChangeError("wrong_code", { attemptsLeft: 3 }), 400, { error: "wrong_code", attemptsLeft: 3 }],
    [new EmailChangeError("email_taken"), 409, { error: "email_taken" }],
    [new EmailChangeError("billing_update_failed"), 502, { error: "billing_update_failed" }],
    [new EmailChangeError("rate_limited", { retryAfterSeconds: 120 }), 429, { error: "rate_limited", retryAfterSeconds: 120 }],
  ])("maps %s to its status", async (error, status, body) => {
    vi.mocked(emailChange.verifyNewEmailCode).mockRejectedValue(error);
    const response = await POST(post({ action: "verify_new", code: "123456" }));
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
    if (status === 429) expect(response.headers.get("Retry-After")).toBe("120");
  });

  it("hides unexpected errors", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(emailChange.finishEmailChange).mockRejectedValue(new Error("connection reset with secrets"));
    const response = await POST(post({ action: "finish" }));
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "server_error" });
  });
});
