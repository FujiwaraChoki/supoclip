import { GET, PATCH } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

describe("/api/preferences", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 when no session exists", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);

    const response = await GET(new Request("http://localhost/api/preferences") as never);

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
  });

  it("returns user preferences for an authenticated user", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findUnique: vi.fn().mockResolvedValue({
          default_font_family: "Inter",
          default_font_size: 28,
          default_font_color: "#123456",
          notify_on_completion: false,
        }),
      },
    } as never);

    const response = await GET(new Request("http://localhost/api/preferences") as never);

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      fontFamily: "Inter",
      fontSize: 28,
      fontColor: "#123456",
      notifyOnCompletion: false,
    });
  });

  it("validates PATCH payloads", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);

    const response = await PATCH(
      new Request("http://localhost/api/preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fontColor: "red" }),
      }) as never,
    );

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      error: "Invalid fontColor (must be hex format like #FFFFFF)",
    });
  });

  it("validates notifyOnCompletion", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);

    const response = await PATCH(
      new Request("http://localhost/api/preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notifyOnCompletion: "yes" }),
      }) as never,
    );

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      error: "Invalid notifyOnCompletion",
    });
  });

  it("updates stored preferences", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    const update = vi.fn().mockResolvedValue({
      default_font_family: "TikTokSans-Regular",
      default_font_size: 24,
      default_font_color: "#FFFFFF",
      notify_on_completion: true,
    });
    vi.mocked(getPrismaClient).mockReturnValue({
      user: { update },
    } as never);

    const response = await PATCH(
      new Request("http://localhost/api/preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fontFamily: "TikTokSans-Regular",
          fontSize: 24,
          fontColor: "#FFFFFF",
          notifyOnCompletion: true,
        }),
      }) as never,
    );

    expect(update).toHaveBeenCalled();
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      fontFamily: "TikTokSans-Regular",
      fontSize: 24,
      fontColor: "#FFFFFF",
      notifyOnCompletion: true,
    });
  });
});
