import { POST } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/backend-api", async () => {
  const actual = await vi.importActual<typeof import("@/server/backend-api")>("@/server/backend-api");
  return { ...actual, fetchBackend: vi.fn() };
});

const params = { params: Promise.resolve({ id: "req/1" }) };

function post(body: string, contentType = "application/json") {
  return new Request("https://www.supoclip.test/api/oauth/requests/req/decision", {
    method: "POST",
    headers: { "Content-Type": contentType },
    body,
  });
}

describe("/api/oauth/requests/[id]/decision", () => {
  beforeEach(() => vi.resetAllMocks());

  it("needs a session", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await POST(post('{"approve":true}'), params)).status).toBe(401);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("refuses form posts so another site can't approve with the cookie", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    expect((await POST(post("approve=true", "application/x-www-form-urlencoded"), params)).status).toBe(415);
    expect((await POST(post('{"approve":"yes"}'), params)).status).toBe(400);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("forwards only the decision for the signed-in user", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1" } } as never);
    vi.mocked(fetchBackend).mockResolvedValue(new Response(JSON.stringify({ redirect_url: "https://claude.ai/cb?code=c" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    const response = await POST(post('{"approve":true,"user_id":"someone-else"}'), params);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ redirect_url: "https://claude.ai/cb?code=c" });
    expect(fetchBackend).toHaveBeenCalledWith("/oauth/requests/req%2F1/decision", expect.objectContaining({
      method: "POST",
      userId: "user-1",
      body: JSON.stringify({ approve: true }),
    }));
  });
});
