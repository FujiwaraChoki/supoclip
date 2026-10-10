import { clearCreatorCode, getStoredCreatorCode, lookupCreatorCode, storeCreatorCode } from "./creator-code";

const respond = (status: number, body: unknown) =>
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status }));

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

describe("creator code", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("returns the normalized code for an approved creator", async () => {
    const fetch = respond(200, { code: "MAYA" });

    await expect(lookupCreatorCode(" maya ")).resolves.toBe("MAYA");
    expect(fetch).toHaveBeenCalledWith("/api/affiliates/offer?code=maya");
  });

  it("returns null for unknown codes and skips blank input", async () => {
    const fetch = respond(404, { error: "Unknown creator code" });

    await expect(lookupCreatorCode("nope")).resolves.toBeNull();
    await expect(lookupCreatorCode("   ")).resolves.toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("remembers a code for 90 days", () => {
    vi.useFakeTimers({ now: new Date("2026-10-09T00:00:00Z") });
    storeCreatorCode("MAYA");
    expect(getStoredCreatorCode()).toBe("MAYA");

    vi.setSystemTime(new Date("2027-01-08T00:00:00Z"));
    expect(getStoredCreatorCode()).toBeNull();
  });

  it("forgets a removed code", () => {
    storeCreatorCode("MAYA");
    clearCreatorCode();
    expect(getStoredCreatorCode()).toBeNull();
  });

  it("captures a valid ?ref= once per page load", async () => {
    vi.resetModules();
    window.history.replaceState(null, "", "/?ref=maya");
    const fetch = respond(200, { code: "MAYA" });
    const { captureCreatorCodeFromUrl } = await import("./creator-code");

    await expect(captureCreatorCodeFromUrl()).resolves.toBe("MAYA");
    await expect(captureCreatorCodeFromUrl()).resolves.toBe("MAYA");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(getStoredCreatorCode()).toBe("MAYA");
    window.history.replaceState(null, "", "/");
  });
});
