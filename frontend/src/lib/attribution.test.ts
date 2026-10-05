import {
  ATTRIBUTION_STORAGE_KEY,
  attributionMetadata,
  captureAnonymousAttribution,
  attributionVisitKey,
  claimAnonymousAttribution,
  confirmAccountAttribution,
  getAccountAttribution,
  getAnonymousAttribution,
  getPendingAttribution,
  isAccountAttributionConfirmed,
  hasCampaignSignal,
  mergeAttribution,
  normalizeLandingPath,
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

describe("normalizeLandingPath", () => {
  // token_urlsafe(32), the format the backend uses for share links.
  const shareToken = "kQ3v_Zr8-Xb1yTq0pLmN4sHcW7aEoU2fDgJiVnRtYxM";

  it.each([
    [`/share/${shareToken}`, "/share/:token"],
    ["/share/abcdefabcdef", "/share/:token"],
    ["/tasks/3f2a9c1e-8b4d-4e6f-9a0b-1c2d3e4f5a6b", "/tasks/:id"],
    ["/tasks/3f2a9c1e-8b4d-4e6f-9a0b-1c2d3e4f5a6b/edit", "/tasks/:id/edit"],
    ["/settings/api-keys/12345", "/settings/api-keys/:id"],
    ["/anything/9f86d081884c7d659a2feaa0c55ad015", "/anything/:token"],
    [`/x/${shareToken}?utm_source=y#frag`, "/x/:token"],
  ])("masks secrets and IDs in %s", (path, expected) => {
    expect(normalizeLandingPath(path)).toBe(expected);
  });

  it.each([
    "/",
    "/demo",
    "/blog/self-host-supoclip-docker",
    "/blog/top-10-clipping-tips-2026",
    "/open-source-video-clipper",
    "/tasks",
  ])("keeps marketing and static paths like %s", (path) => {
    expect(normalizeLandingPath(path)).toBe(path);
  });

  it("is applied to untrusted input, including previously stored raw paths", () => {
    expect(sanitizeAttribution({ captured_at: now.toISOString(), landing_path: `/share/${shareToken}` })?.landing_path).toBe(
      "/share/:token",
    );
    expect(sanitizeAttribution({ captured_at: now.toISOString(), landing_path: "https://evil.example/x" })?.landing_path).toBeUndefined();
  });

  it("never records a share token from a captured visit", () => {
    const attribution = parseAttribution(new URL(`https://www.supoclip.com/share/${shareToken}?utm_source=x`), "", now);
    expect(JSON.stringify(attribution)).not.toContain(shareToken);
    expect(attribution.landing_path).toBe("/share/:token");
  });
});

describe("browser attribution storage", () => {
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

  it("persists the first anonymous touch across later visits", () => {
    captureAnonymousAttribution("https://www.supoclip.com/demo?utm_source=x&utm_campaign=launch", "");
    captureAnonymousAttribution("https://www.supoclip.com/pricing?utm_source=newsletter", "");

    expect(getAnonymousAttribution()).toMatchObject({ utm_source: "x", utm_campaign: "launch", landing_path: "/demo" });
  });

  it("ignores corrupt storage", () => {
    storage.set(ATTRIBUTION_STORAGE_KEY, "{not json");
    expect(getAnonymousAttribution()).toBeNull();
    expect(captureAnonymousAttribution("https://www.supoclip.com/", "")).toMatchObject({ landing_path: "/" });
  });

  const visitAt = (href: string) => captureAnonymousAttribution(href, "")!;

  it("claims the anonymous visit synchronously into one account's pending slot", () => {
    const visit = visitAt("https://www.supoclip.com/?utm_source=reddit");

    expect(claimAnonymousAttribution("user-a")).toEqual(visit);

    expect(getAnonymousAttribution()).toBeNull();
    expect(getPendingAttribution("user-a")).toEqual(visit);
    expect(claimAnonymousAttribution("user-b")).toBeNull();
    expect(getAccountAttribution("user-b")).toBeNull();
  });

  it("keeps exports attributed while the claim is pending", () => {
    visitAt("https://www.supoclip.com/?utm_source=reddit");
    claimAnonymousAttribution("user-a");

    expect(isAccountAttributionConfirmed("user-a")).toBe(false);
    expect(getAccountAttribution("user-a")).toMatchObject({ utm_source: "reddit" });
  });

  it("re-sends an earlier unconfirmed claim instead of claiming a newer visit", () => {
    const first = visitAt("https://www.supoclip.com/?utm_source=reddit");
    claimAnonymousAttribution("user-a");
    visitAt("https://www.supoclip.com/?utm_source=newsletter");

    expect(claimAnonymousAttribution("user-a")).toEqual(first);
  });

  it("consumes only the exact claimed visit when the server confirms", () => {
    const sent = visitAt("https://www.supoclip.com/?utm_source=reddit");
    claimAnonymousAttribution("user-a");
    // User A signs out and a new anonymous journey starts before the response arrives.
    const newer = visitAt("https://www.supoclip.com/?utm_source=newsletter");

    confirmAccountAttribution("user-a", sent, sent);

    expect(getPendingAttribution("user-a")).toBeNull();
    expect(getAccountAttribution("user-a")).toMatchObject({ utm_source: "reddit" });
    expect(getAnonymousAttribution()).toEqual(newer);
  });

  it("does not let a stale response consume a different pending visit", () => {
    const stale = sanitizeAttribution({ captured_at: "2026-10-01T00:00:00.000Z", utm_source: "old" })!;
    const current = visitAt("https://www.supoclip.com/?utm_source=reddit");
    claimAnonymousAttribution("user-a");

    confirmAccountAttribution("user-a", stale, stale);

    expect(getPendingAttribution("user-a")).toEqual(current);
    expect(attributionVisitKey(stale)).not.toBe(attributionVisitKey(current));
  });

  it("has a confirmed account discard anonymous visits so later accounts can't inherit them", () => {
    confirmAccountAttribution("user-a", null, null);
    visitAt("https://www.supoclip.com/?utm_source=reddit");

    expect(claimAnonymousAttribution("user-a")).toBeNull();
    expect(getAnonymousAttribution()).toBeNull();
    expect(claimAnonymousAttribution("user-b")).toBeNull();
  });

  it("records a confirmed unattributed account as done", () => {
    confirmAccountAttribution("user-a", null, null);

    expect(isAccountAttributionConfirmed("user-a")).toBe(true);
    expect(getAccountAttribution("user-a")).toBeNull();
  });
});
