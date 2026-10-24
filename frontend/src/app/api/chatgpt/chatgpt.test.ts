import { cookies } from "next/headers";
import { getServerSession } from "@/server/session";
import { fetchBackend } from "@/server/backend-api";
import { CHATGPT_STATE_COOKIE } from "@/server/chatgpt";
import { GET as callback } from "./callback/route";
import { POST as authorize } from "./authorize/route";
import { POST as proxy, GET as getProxy } from "./[...path]/route";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/backend-api", async () => ({
  ...await vi.importActual<typeof import("@/server/backend-api")>("@/server/backend-api"),
  fetchBackend: vi.fn(),
}));

const state = "random-state-at-least-thirty-two-characters";
const account = "11111111-1111-4111-8111-111111111111";
const context = (path: string[]) => ({ params: Promise.resolve({ path }) });
function request(path: string, method = "GET", origin = "http://localhost:3107") {
  return new Request(`http://localhost:3107/api/chatgpt/${path}`, {
    method, headers: { origin }, ...(method === "POST" ? { body: "{}" } : {}),
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv("NEXT_PUBLIC_APP_URL", "http://localhost:3107");
  vi.mocked(getServerSession).mockResolvedValue({ user: { id: "alice" } } as never);
  vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: state }) } as never);
});
afterEach(() => vi.unstubAllEnvs());

it("rejects cross-origin authorization before contacting the backend", async () => {
  const response = await authorize(request("authorize", "POST", "https://attacker.example"));
  expect(response.status).toBe(403);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("sets a short-lived HttpOnly callback state cookie without exposing it in JSON", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(Response.json({ authorization_url: "https://auth.openai.com/start", state }));
  const response = await authorize(request("authorize", "POST"));
  expect(await response.json()).toEqual({ authorization_url: "https://auth.openai.com/start" });
  expect(response.cookies.get(CHATGPT_STATE_COOKIE)).toMatchObject({ value: state, httpOnly: true, sameSite: "lax", maxAge: 600, path: "/api/chatgpt" });
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it("requires the signed-in SupoClip session to start authorization", async () => {
  vi.mocked(getServerSession).mockResolvedValue(null);
  expect((await authorize(request("authorize", "POST"))).status).toBe(401);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("rejects callback state mismatches and clears the cookie", async () => {
  const response = await callback(request("callback?code=code&state=attacker"));
  expect(new URL(response.headers.get("location")!).searchParams.get("chatgpt")).toBe("expired");
  expect(response.cookies.get(CHATGPT_STATE_COOKIE)?.maxAge).toBe(0);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("rejects a callback without a current SupoClip session", async () => {
  vi.mocked(getServerSession).mockResolvedValue(null);
  const response = await callback(request(`callback?state=${state}&code=code`));
  expect(new URL(response.headers.get("location")!).searchParams.get("chatgpt")).toBe("expired");
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("forwards a verified callback under the current user and never redirects with codes or tokens", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(Response.json({ connected: true }));
  const response = await callback(request(`callback?state=${state}&code=code&client_id=oaiapp_issued`));
  expect(fetchBackend).toHaveBeenCalledWith("/chatgpt/complete", expect.objectContaining({
    userId: "alice", method: "POST", body: JSON.stringify({ state, code: "code", client_id: "oaiapp_issued", error: null }),
  }));
  expect(response.headers.get("location")).toBe("http://localhost:3107/settings/chatgpt?chatgpt=connected");
  expect(response.headers.get("referrer-policy")).toBe("no-referrer");
});

it("does not reflect a provider error description into the app", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(Response.json({ detail: "private" }, { status: 400 }));
  const response = await callback(request(`callback?state=${state}&error=access_denied&error_description=private`));
  expect(response.headers.get("location")).toBe("http://localhost:3107/settings/chatgpt?chatgpt=failed");
});

it("blocks generic access to token exchange and unknown paths", async () => {
  expect((await proxy(request("complete", "POST"), context(["complete"]))).status).toBe(404);
  expect((await getProxy(request("../admin"), context(["..", "admin"]))).status).toBe(404);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("proxies only an allowed model action with the session user", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(Response.json({ enabled: true }));
  expect((await proxy(request(`accounts/${account}/activate`, "POST"), context(["accounts", account, "activate"]))).status).toBe(200);
  expect(fetchBackend).toHaveBeenCalledWith(`/chatgpt/accounts/${account}/activate`, expect.objectContaining({ userId: "alice", method: "POST" }));
});
