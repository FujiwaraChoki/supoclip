// @vitest-environment node
import { POST } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));

const post = (country: unknown) =>
  POST(new Request("http://localhost/api/affiliates/country", { method: "POST", body: JSON.stringify({ country }) }));

describe("/api/affiliates/country", () => {
  const updateMany = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({ affiliate: { updateMany } } as never);
  });

  it("sets the country once, only while it is unset", async () => {
    updateMany.mockResolvedValue({ count: 1 });
    expect((await post("GB")).status).toBe(200);
    expect(updateMany).toHaveBeenCalledWith({
      where: { user_id: "user-1", country: null, status: { in: ["pending", "approved"] } },
      data: { country: "GB" },
    });
  });

  it("accepts elsewhere and refuses codes that aren't offered", async () => {
    updateMany.mockResolvedValue({ count: 1 });
    expect((await post("ZZ")).status).toBe(200);
    expect((await post("IN")).status).toBe(400);
  });

  it("won't change a country that is already set", async () => {
    updateMany.mockResolvedValue({ count: 0 });
    expect((await post("US")).status).toBe(409);
  });
});
