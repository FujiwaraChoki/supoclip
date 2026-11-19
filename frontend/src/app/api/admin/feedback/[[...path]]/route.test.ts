import { GET, PATCH, POST } from "./route";
import { DELETE as deleteWebhook, POST as postWebhook } from "../../feedback-webhooks/[[...path]]/route";
import { getServerSession } from "@/server/session";
import { fetchBackend } from "@/server/backend-api";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/backend-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/server/backend-api")>()),
  fetchBackend: vi.fn(),
}));

const context = (...path: string[]) => ({ params: Promise.resolve({ path }) });
const request = (method: string, url = "https://supoclip.com/api/admin/feedback", body?: unknown, origin?: string) =>
  new Request(url, {
    method,
    headers: { "Content-Type": "application/json", ...(origin ? { origin } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv("NEXT_PUBLIC_APP_URL", "https://www.supoclip.com");
  vi.mocked(getServerSession).mockResolvedValue({ user: { id: "admin-1", is_admin: true } } as never);
  vi.mocked(fetchBackend).mockImplementation(async () => Response.json({ ok: true }));
});

afterEach(() => vi.unstubAllEnvs());

it.each([
  [null, 401],
  [{ user: { id: "user-1", is_admin: false } }, 403],
])("only admins reach the backend", async (session, status) => {
  vi.mocked(getServerSession).mockResolvedValue(session as never);
  expect((await GET(request("GET"), context())).status).toBe(status);
  expect((await PATCH(request("PATCH", undefined, { status: "resolved" }), context("abc"))).status).toBe(status);
  expect((await postWebhook(request("POST", undefined, { name: "x" }), context())).status).toBe(status);
  expect((await deleteWebhook(request("DELETE"), context("abc"))).status).toBe(status);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("forwards the admin's filters to the backend", async () => {
  const response = await GET(request("GET", "https://supoclip.com/api/admin/feedback?status=open&q=ana"), context());
  expect(response.status).toBe(200);
  expect(fetchBackend).toHaveBeenCalledWith("/admin/feedback?status=open&q=ana", expect.objectContaining({ method: "GET", userId: "admin-1" }));
});

it("forwards writes with their body", async () => {
  await POST(request("POST", undefined, { body: "Called them" }), context("sub-1", "notes"));
  expect(fetchBackend).toHaveBeenCalledWith("/admin/feedback/sub-1/notes", expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ body: "Called them" }),
  }));
  await postWebhook(request("POST"), context("hook-1", "test"));
  expect(fetchBackend).toHaveBeenLastCalledWith("/admin/feedback-webhooks/hook-1/test", expect.objectContaining({ method: "POST" }));
});

it("refuses cross-origin writes and paths that leave the prefix", async () => {
  expect((await POST(request("POST", undefined, { body: "x" }, "https://evil.example"), context("sub-1", "notes"))).status).toBe(403);
  expect((await GET(request("GET"), context("..", "runtime-settings"))).status).toBe(404);
  expect((await GET(request("GET"), context("a%2Fb"))).status).toBe(404);
  expect((await GET(request("GET"), context("a", "b", "c", "d"))).status).toBe(404);
  expect(fetchBackend).not.toHaveBeenCalled();
});
