import { GET } from "./route";

const { findUnique } = vi.hoisted(() => ({ findUnique: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: () => ({ serviceStatus: { findUnique } }) }));

afterEach(() => { vi.restoreAllMocks(); });

it("returns the current video download incident without caching", async () => {
  findUnique.mockResolvedValue({
    status: "outage", headline: "Investigating YouTube import failures",
    description: "Imports may fail.", updatedAt: new Date("2026-10-14T00:08:55Z"),
  });
  const response = await GET();
  expect(findUnique).toHaveBeenCalledWith({
    where: { id: "video-downloads" },
    select: { status: true, headline: true, description: true, updatedAt: true },
  });
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(await response.json()).toMatchObject({ component: { status: "outage" } });
});

it("reports unavailable status without claiming service is operational", async () => {
  findUnique.mockResolvedValue(null);
  const response = await GET();
  expect(response.status).toBe(503);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
});
