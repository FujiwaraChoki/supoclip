import { claimAnonymousAttribution, captureAnonymousAttribution, confirmAccountAttribution } from "./attribution";
import { trackClipExport } from "./clip-export-tracking";
import { track } from "./datafast";

vi.mock("./datafast", () => ({
  track: vi.fn(),
}));

describe("trackClipExport", () => {
  const storage = new Map<string, string>();
  const fetchMock = vi.fn();

  beforeEach(() => {
    storage.clear();
    vi.mocked(track).mockReset();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    });
    fetchMock.mockReset().mockResolvedValue(Response.json({ first_export: true, attribution: null, hours_since_signup: 1 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attributes exports to the claimed visit while the sync is pending", async () => {
    captureAnonymousAttribution("https://www.supoclip.com/?utm_source=reddit&utm_campaign=launch", "");
    claimAnonymousAttribution("user-a");

    await trackClipExport({ userId: "user-a", surface: "task", format: "tiktok" });

    expect(track).toHaveBeenCalledWith("clip_exported", expect.objectContaining({ utm_source: "reddit", utm_campaign: "launch" }));
    expect(track).toHaveBeenCalledWith("first_clip_exported", expect.objectContaining({ utm_source: "reddit", hours_since_signup: 1 }));
  });

  it("never attaches another account's attribution", async () => {
    captureAnonymousAttribution("https://www.supoclip.com/?utm_source=reddit", "");
    claimAnonymousAttribution("user-a");
    confirmAccountAttribution("user-c", { utm_source: "newsletter", captured_at: "2026-10-05T10:00:00.000Z" }, null);

    await trackClipExport({ userId: "user-b", surface: "editor", format: "reels" });

    expect(track).toHaveBeenCalledWith("clip_exported", { surface: "editor", format: "reels" });
  });
});
