import {
  ATTRIBUTION_STORAGE_KEY,
  attributionMetadata,
  captureAttribution,
  getStoredAttribution,
  hasCampaignSignal,
  mergeAttribution,
  parseAttribution,
  sanitizeAttribution,
} from "./attribution";

const now = new Date("2026-10-05T12:00:00.000Z");
const daysLater = (days: number) => new Date(now.getTime() + days * 24 * 60 * 60 * 1000);

describe("parseAttribution", () => {
  it("reads UTM tags, normalizes case, and keeps the landing path", () => {
    const attribution = parseAttribution(
      new URL("https://www.supoclip.com/demo?utm_source=Reddit&utm_medium=social&utm_campaign=Launch_Oct&utm_content=selfhosted"),
      "",
      now,
    );

    expect(attribution).toEqual({
      utm_source: "reddit",
      utm_medium: "social",
      utm_campaign: "launch_oct",
      utm_content: "selfhosted",
      landing_path: "/demo",
      captured_at: now.toISOString(),
    });
  });

  it("uses ?via= when ?ref= is absent", () => {
    expect(parseAttribution(new URL("https://www.supoclip.com/?via=Creator42"), "", now).ref).toBe("creator42");
  });

  it("records external referrers and ignores same-site ones", () => {
    const url = new URL("https://www.supoclip.com/blog");
    expect(parseAttribution(url, "https://news.ycombinator.com/item?id=1", now).referrer_host).toBe("news.ycombinator.com");
    expect(parseAttribution(url, "https://supoclip.com/", now).referrer_host).toBeUndefined();
    expect(parseAttribution(url, "not a url", now).referrer_host).toBeUndefined();
  });
});

describe("mergeAttribution", () => {
  const direct = parseAttribution(new URL("https://www.supoclip.com/"), "", now);
  const reddit = parseAttribution(new URL("https://www.supoclip.com/?utm_source=reddit"), "", now);
  const hn = parseAttribution(new URL("https://www.supoclip.com/?utm_source=hn"), "", daysLater(1));

  it("keeps the first visit with a campaign signal", () => {
    expect(mergeAttribution(reddit, hn, daysLater(1))).toBe(reddit);
  });

  it("lets a real source replace an earlier direct visit", () => {
    expect(hasCampaignSignal(direct)).toBe(false);
    expect(mergeAttribution(direct, hn, daysLater(1))).toBe(hn);
  });

  it("starts over once the stored attribution is older than 90 days", () => {
    expect(mergeAttribution(reddit, hn, daysLater(91))).toBe(hn);
  });
});

describe("sanitizeAttribution", () => {
  it("rejects payloads without a valid capture time", () => {
    expect(sanitizeAttribution(null)).toBeNull();
    expect(sanitizeAttribution({ utm_source: "x" })).toBeNull();
    expect(sanitizeAttribution({ utm_source: "x", captured_at: "nope" })).toBeNull();
  });

  it("drops unknown keys, non-strings, and over-long values", () => {
    const result = sanitizeAttribution({
      captured_at: now.toISOString(),
      utm_source: "x".repeat(500),
      utm_medium: 42,
      email: "someone@example.com",
    });
    expect(result).toEqual({ captured_at: now.toISOString(), utm_source: "x".repeat(100) });
  });
});

describe("attributionMetadata", () => {
  it("returns only populated source fields", () => {
    expect(attributionMetadata({ utm_source: "reddit", utm_campaign: null, landing_path: "/" })).toEqual({ utm_source: "reddit" });
    expect(attributionMetadata(null)).toEqual({});
  });
});

describe("captureAttribution", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    });
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("persists the first touch across later visits", () => {
    window.history.replaceState(null, "", "/demo?utm_source=x&utm_campaign=launch");
    captureAttribution();
    window.history.replaceState(null, "", "/pricing?utm_source=newsletter");
    captureAttribution();

    expect(getStoredAttribution()).toMatchObject({ utm_source: "x", utm_campaign: "launch", landing_path: "/demo" });
  });

  it("ignores corrupt storage", () => {
    window.localStorage.setItem(ATTRIBUTION_STORAGE_KEY, "{not json");
    expect(getStoredAttribution()).toBeNull();
    expect(captureAttribution()).toMatchObject({ landing_path: "/" });
  });
});
