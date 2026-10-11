import { GET, POST } from "./route";
import { PATCH } from "./[id]/route";
import { GET as publicFeed } from "../../announcements/route";
import { getServerSession } from "@/server/session";
import { getPrismaClient } from "@/server/prisma";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));

const body = { title: "New update", description: "An update", href: "/affiliate", action: "Learn more", isPublished: false, publishedAt: "2026-10-11T00:00:00Z" };
const request = (value: unknown, origin?: string) => new Request("https://supoclip.com/api/admin/announcements", {
  method: "POST", headers: { "Content-Type": "application/json", ...(origin ? { origin } : {}) }, body: JSON.stringify(value),
});
const params = { params: Promise.resolve({ id: "stored-id" }) };
const announcement = { findMany: vi.fn(), create: vi.fn(), updateMany: vi.fn() };

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv("NEXT_PUBLIC_APP_URL", "https://www.supoclip.com");
  vi.mocked(getServerSession).mockResolvedValue({ user: { id: "admin", is_admin: true } } as never);
  vi.mocked(getPrismaClient).mockReturnValue({ announcement } as never);
  announcement.findMany.mockResolvedValue([]);
  announcement.create.mockResolvedValue({ id: "stored-id", ...body });
  announcement.updateMany.mockResolvedValue({ count: 1 });
});

afterEach(() => vi.unstubAllEnvs());

it.each([null, { user: { id: "user", is_admin: false } }])("restricts management to admins", async session => {
  vi.mocked(getServerSession).mockResolvedValue(session as never);
  const status = session ? 403 : 401;
  expect((await GET()).status).toBe(status);
  expect((await POST(request(body))).status).toBe(status);
  expect((await PATCH(request(body), params)).status).toBe(status);
  expect(announcement.findMany).not.toHaveBeenCalled();
  expect(announcement.create).not.toHaveBeenCalled();
  expect(announcement.updateMany).not.toHaveBeenCalled();
});

it("rejects cross-origin writes even for an admin", async () => {
  expect((await POST(request(body, "https://other.example"))).status).toBe(403);
  expect(announcement.create).not.toHaveBeenCalled();
});

it("allows the configured public origin behind a reverse proxy", async () => {
  const proxied = new Request("https://0.0.0.0:3107/api/admin/announcements", {
    method: "POST", headers: { "Content-Type": "application/json", origin: "https://www.supoclip.com" }, body: JSON.stringify(body),
  });
  expect((await POST(proxied)).status).toBe(201);
});

it("does not trust a spoofed forwarded host to allow a cross-origin write", async () => {
  const proxied = new Request("https://0.0.0.0:3107/api/admin/announcements", {
    method: "POST", headers: { "Content-Type": "application/json", origin: "https://other.example", "x-forwarded-host": "other.example" }, body: JSON.stringify(body),
  });
  expect((await POST(proxied)).status).toBe(403);
  expect(announcement.create).not.toHaveBeenCalled();
});

it.each([
  { ...body, href: "javascript:alert(1)" }, { ...body, href: "//other.example" },
  { ...body, title: " " }, { ...body, title: "x".repeat(121) }, { ...body, publishedAt: "invalid" },
])("rejects invalid announcement content", async value => {
  expect((await POST(request(value))).status).toBe(400);
  expect(announcement.create).not.toHaveBeenCalled();
});

it("creates a database announcement and updates visibility without changing its ID", async () => {
  const created = await POST(request(body));
  expect(created.status).toBe(201);
  expect((await created.json()).announcement.id).toBe("stored-id");
  expect((await PATCH(request({ ...body, isPublished: true }), params)).status).toBe(200);
  expect(announcement.updateMany).toHaveBeenCalledWith({ where: { id: "stored-id" }, data: { ...body, isPublished: true, publishedAt: new Date(body.publishedAt) } });
});

it("reports missing announcements", async () => {
  announcement.updateMany.mockResolvedValue({ count: 0 });
  expect((await PATCH(request(body), params)).status).toBe(404);
});

it("publishes only visible announcements whose publication date has arrived", async () => {
  const response = await publicFeed();
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(announcement.findMany).toHaveBeenCalledWith(expect.objectContaining({
    where: { isPublished: true, publishedAt: { lte: expect.any(Date) } },
  }));
  expect(getServerSession).not.toHaveBeenCalled();
});
