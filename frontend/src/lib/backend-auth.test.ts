import { buildBackendAuthHeaders } from "./backend-auth";

describe("buildBackendAuthHeaders", () => {
  const originalSecret = process.env.BACKEND_AUTH_SECRET;

  afterEach(() => {
    if (originalSecret === undefined) {
      delete process.env.BACKEND_AUTH_SECRET;
    } else {
      process.env.BACKEND_AUTH_SECRET = originalSecret;
    }
    vi.restoreAllMocks();
  });

  it("falls back to unsigned headers when no secret is configured", () => {
    delete process.env.BACKEND_AUTH_SECRET;

    expect(buildBackendAuthHeaders("user-1")).toEqual({
      "x-supoclip-user-id": "user-1",
    });
  });

  it("builds signed headers when a secret is configured", () => {
    process.env.BACKEND_AUTH_SECRET = "secret";
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);

    expect(buildBackendAuthHeaders("user-1")).toEqual({
      "x-supoclip-user-id": "user-1",
      "x-supoclip-ts": "1700000000",
      "x-supoclip-signature":
        "cceb65b01012f5596122712d023d1def6663579f457362796a52133b3875c545",
    });
  });
});
