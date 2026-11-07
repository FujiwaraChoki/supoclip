import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));

const post = (body: unknown) => POST(new Request("http://localhost/api/feature-tours", { method: "POST", body: JSON.stringify(body) }) as never);

describe("/api/feature-tours", () => {
  const tours = { findUnique: vi.fn(), create: vi.fn(), update: vi.fn() };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      userFeatureTour: tours,
      user: { findUnique: vi.fn().mockResolvedValue({ createdAt: new Date("2026-01-01T00:00:00Z"), feature_tours: [{ tour_id: "workflows" }] }) },
    } as never);
  });

  it("needs a signed-in user", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await GET()).status).toBe(401);
    expect((await post({ id: "workflows" })).status).toBe(401);
  });

  it("returns when the account started and the tours it has seen", async () => {
    await expect((await GET()).json()).resolves.toEqual({ createdAt: "2026-01-01T00:00:00.000Z", seen: ["workflows"] });
  });

  it("records a tour once when it is shown", async () => {
    tours.findUnique.mockResolvedValueOnce(null).mockResolvedValueOnce({ completed_at: null, dismissed_at: null });
    expect((await post({ id: "workflows", event: "shown" })).status).toBe(200);
    expect(tours.create).toHaveBeenCalledWith({ data: { user_id: "user-1", tour_id: "workflows" } });
    await post({ id: "workflows" });
    expect(tours.create).toHaveBeenCalledOnce();
    expect(tours.update).not.toHaveBeenCalled();
  });

  it("keeps the first ending and the step it ended on", async () => {
    tours.findUnique.mockResolvedValueOnce({ completed_at: null, dismissed_at: null });
    await post({ id: "workflows", event: "dismissed", step: 3 });
    expect(tours.update).toHaveBeenCalledWith({
      where: { user_id_tour_id: { user_id: "user-1", tour_id: "workflows" } },
      data: { dismissed_at: expect.any(Date), last_step: 3 },
    });
    tours.findUnique.mockResolvedValueOnce({ completed_at: new Date(), dismissed_at: null });
    await post({ id: "workflows", event: "dismissed", step: 7 });
    expect(tours.update).toHaveBeenCalledOnce();
  });

  it("refuses tours and events it doesn't know", async () => {
    for (const body of [{ id: "montage" }, { id: "workflows", event: "liked" }, null]) {
      expect((await post(body)).status).toBe(400);
    }
    expect(tours.create).not.toHaveBeenCalled();
  });
});
