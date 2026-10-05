import { render, waitFor } from "@testing-library/react";

import { AttributionTracker } from "./attribution-tracker";
import {
  ATTRIBUTION_STORAGE_KEY,
  getAccountAttribution,
  getAnonymousAttribution,
  getPendingAttribution,
  isAccountAttributionConfirmed,
} from "@/lib/attribution";
import { useSession } from "@/lib/auth-client";

vi.mock("@/lib/auth-client", () => ({
  useSession: vi.fn(),
}));

type SessionState = { data: { user: { id: string } } | null; isPending: boolean };
const signedOut: SessionState = { data: null, isPending: false };
const pending: SessionState = { data: null, isPending: true };
const signedIn = (id: string): SessionState => ({ data: { user: { id } }, isPending: false });

const setSession = (state: SessionState) => vi.mocked(useSession).mockReturnValue(state as never);

/** Mounts the tracker for a fresh page load at `path` with the given session. */
function pageLoad(path: string, state: SessionState) {
  window.history.replaceState(null, "", path);
  setSession(state);
  return render(<AttributionTracker />);
}

/** A fetch whose responses resolve only when the test says so. */
function deferredFetch() {
  const calls: Array<{ url: string; init?: RequestInit; respond: (body: unknown, status?: number) => void }> = [];
  const fn = vi.fn(
    (url: string, init?: RequestInit) =>
      new Promise<Response>((resolve) => {
        calls.push({ url, init, respond: (body, status = 200) => resolve(Response.json(body, { status })) });
      }),
  );
  return { fn, calls };
}

const postedBody = (init?: RequestInit) => JSON.parse(String(init?.body));
/** Lets resolved responses run through the tracker's promise chain. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("AttributionTracker", () => {
  const storage = new Map<string, string>();
  let network: ReturnType<typeof deferredFetch>;

  beforeEach(() => {
    storage.clear();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    });
    network = deferredFetch();
    vi.stubGlobal("fetch", network.fn);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("never captures a campaign visit made while signed in", () => {
    pageLoad("/?utm_source=reddit", signedIn("user-a"));

    expect(network.calls.map((call) => call.url)).toEqual(["/api/attribution"]);
    expect(network.calls[0].init).toBeUndefined();
    expect(storage.has(ATTRIBUTION_STORAGE_KEY)).toBe(false);
  });

  it("waits for the session to resolve before capturing", () => {
    const { rerender } = pageLoad("/?utm_source=reddit", pending);
    expect(storage.has(ATTRIBUTION_STORAGE_KEY)).toBe(false);

    setSession(signedOut);
    rerender(<AttributionTracker />);

    expect(getAnonymousAttribution()).toMatchObject({ utm_source: "reddit" });
  });

  it("claims the visit before the request resolves, so a second account can't take it", () => {
    pageLoad("/?utm_source=reddit", signedOut).unmount();

    pageLoad("/", signedIn("user-a")).unmount();
    // Synchronous: the request for user A is still in flight.
    expect(getAnonymousAttribution()).toBeNull();
    expect(getPendingAttribution("user-a")).toMatchObject({ utm_source: "reddit" });
    expect(postedBody(network.calls[0].init)).toMatchObject({ utm_source: "reddit" });

    pageLoad("/", signedIn("user-b"));

    expect(network.calls[1]).toMatchObject({ url: "/api/attribution", init: undefined });
    expect(getAccountAttribution("user-b")).toBeNull();
  });

  it("keeps exports attributed while the sync is pending", () => {
    pageLoad("/?utm_source=reddit&utm_campaign=launch", signedOut).unmount();
    pageLoad("/", signedIn("user-a"));

    expect(isAccountAttributionConfirmed("user-a")).toBe(false);
    expect(getAccountAttribution("user-a")).toMatchObject({ utm_source: "reddit", utm_campaign: "launch" });
  });

  it("does not let a late response erase a newer anonymous journey", async () => {
    pageLoad("/?utm_source=reddit", signedOut).unmount();
    pageLoad("/", signedIn("user-a")).unmount();
    const sent = postedBody(network.calls[0].init);

    // User A signs out and a new campaign visit is recorded before A's sync answers.
    pageLoad("/?utm_source=newsletter", signedOut).unmount();
    network.calls[0].respond({ attribution: sent, confirmed: true });

    await waitFor(() => expect(isAccountAttributionConfirmed("user-a")).toBe(true));
    expect(getAccountAttribution("user-a")).toMatchObject({ utm_source: "reddit" });
    expect(getPendingAttribution("user-a")).toBeNull();
    expect(getAnonymousAttribution()).toMatchObject({ utm_source: "newsletter" });
  });

  it("retries an unconfirmed empty answer, then keeps the confirmed one", async () => {
    pageLoad("/", signedIn("user-a")).unmount();
    network.calls[0].respond({ attribution: null, confirmed: false });
    await flush();
    expect(isAccountAttributionConfirmed("user-a")).toBe(false);

    pageLoad("/", signedIn("user-a")).unmount();
    expect(network.calls).toHaveLength(2);
    network.calls[1].respond({ attribution: { utm_source: "reddit", captured_at: "2026-10-05T10:00:00.000Z" }, confirmed: true });

    await waitFor(() => expect(getAccountAttribution("user-a")).toMatchObject({ utm_source: "reddit" }));
    pageLoad("/", signedIn("user-a"));
    expect(network.calls).toHaveLength(2);
  });

  it("stops after a confirmed empty answer (an account that predates tracking)", async () => {
    pageLoad("/", signedIn("user-a")).unmount();
    network.calls[0].respond({ attribution: null, confirmed: true });
    await waitFor(() => expect(isAccountAttributionConfirmed("user-a")).toBe(true));

    pageLoad("/", signedIn("user-a"));

    expect(network.calls).toHaveLength(1);
  });

  it("re-sends the same claimed visit after a failed sync", async () => {
    pageLoad("/?utm_source=reddit", signedOut).unmount();
    pageLoad("/", signedIn("user-a")).unmount();
    network.calls[0].respond({ error: "Internal server error" }, 500);
    await flush();
    expect(getPendingAttribution("user-a")).toMatchObject({ utm_source: "reddit" });

    pageLoad("/", signedIn("user-a"));

    expect(network.calls).toHaveLength(2);
    expect(postedBody(network.calls[1].init)).toEqual(postedBody(network.calls[0].init));
    expect(isAccountAttributionConfirmed("user-a")).toBe(false);
  });
});
