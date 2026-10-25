// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchBackend } from "@/server/backend-api";
import { GET, POST } from "./route";

vi.mock("@/server/backend-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/server/backend-api")>()),
  fetchBackend: vi.fn(),
}));

// The headers the backend's unsubscribe page sets.
const page = (body: string, status = 200) => new Response(body, {
  status,
  headers: {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
  },
});

beforeEach(() => {
  vi.mocked(fetchBackend).mockResolvedValue(page("<h1>Confirm unsubscribe</h1>"));
});
afterEach(() => vi.resetAllMocks());

it.each([["GET", GET], ["POST", POST]] as const)("forwards an anonymous %s without session headers or cookies", async (method, handler) => {
  const response = await handler(new Request("https://app.example.com/email/product-updates/unsubscribe?token=v1.signed", { method }));
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(response.headers.get("referrer-policy")).toBe("no-referrer");
  expect(response.headers.get("content-security-policy")).toBe("default-src 'none'");
  expect(fetchBackend).toHaveBeenCalledWith("/email/product-updates/unsubscribe?token=v1.signed", {
    method, cache: "no-store", signal: expect.any(AbortSignal),
  });
});

it("forwards a mailbox one-click POST without requiring its body or cookies", async () => {
  await POST(new Request("https://app.example.com/email/product-updates/unsubscribe?token=v1.signed", {
    method: "POST", body: "List-Unsubscribe=One-Click", headers: { Cookie: "session=not-forwarded" },
  }));
  expect(fetchBackend).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "POST" }));
  expect(vi.mocked(fetchBackend).mock.calls[0][1]).not.toHaveProperty("userId");
  expect(vi.mocked(fetchBackend).mock.calls[0][1]).not.toHaveProperty("body");
});

it("keeps invalid-token responses and handles backend outages without caching", async () => {
  vi.mocked(fetchBackend).mockResolvedValueOnce(page("Invalid link", 400));
  const request = new Request("https://app.example.com/email/product-updates/unsubscribe?token=wrong");
  expect((await GET(request)).status).toBe(400);
  vi.mocked(fetchBackend).mockRejectedValueOnce(new Error("offline"));
  const unavailable = await GET(request);
  expect(unavailable.status).toBe(503);
  expect(unavailable.headers.get("cache-control")).toBe("no-store");
});

it("rejects oversized tokens before contacting the backend", async () => {
  const response = await GET(new Request(`https://app.example.com/email/product-updates/unsubscribe?token=${"x".repeat(257)}`));
  expect(response.status).toBe(400);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("sends link-scanner HEAD requests to the backend as GET", async () => {
  await GET(new Request("https://app.example.com/email/product-updates/unsubscribe?token=v1.signed", { method: "HEAD" }));
  expect(fetchBackend).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "GET" }));
});

it("shows the outage message instead of relabelling a non-HTML backend error", async () => {
  vi.mocked(fetchBackend).mockResolvedValueOnce(Response.json({ detail: "Internal Server Error" }, { status: 500 }));
  const response = await POST(new Request("https://app.example.com/email/product-updates/unsubscribe?token=v1.signed", { method: "POST" }));
  expect(response.status).toBe(503);
  expect(response.headers.get("content-type")).toContain("text/plain");
  expect(await response.text()).not.toContain("detail");
});
