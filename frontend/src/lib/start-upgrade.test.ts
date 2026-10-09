import { getStoredCreatorCode, storeCreatorCode } from "./creator-code";
import { startUpgrade } from "./start-upgrade";

vi.mock("@/lib/datafast", () => ({ track: vi.fn() }));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
const bodies = (fetch: { mock: { calls: unknown[][] } }) =>
  fetch.mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));

const storage = new Map<string, string>();

beforeEach(() => {
  storage.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("startUpgrade", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("applies a remembered creator code", async () => {
    storeCreatorCode("MAYA");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { url: "#checkout" }));

    await startUpgrade("pro", "test");

    expect(bodies(fetch)).toEqual([{ plan: "pro", code: "MAYA" }]);
  });

  it("drops a code that stopped being valid and checks out at the regular price", async () => {
    storeCreatorCode("MAYA");
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(400, { error: "That creator code isn't valid" }))
      .mockResolvedValueOnce(json(200, { url: "#checkout" }));

    await startUpgrade("scale", "test");

    expect(bodies(fetch)).toEqual([{ plan: "scale", code: "MAYA" }, { plan: "scale" }]);
    expect(getStoredCreatorCode()).toBeNull();
  });

  it("surfaces checkout errors without a code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(400, { error: "Unknown billing plan" }));

    await expect(startUpgrade("pro", "test")).rejects.toThrow("Unknown billing plan");
  });
});
