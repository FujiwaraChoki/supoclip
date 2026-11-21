import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FeatureTour } from "@/lib/feature-tours";
import { FeatureTourHost } from "./feature-tour";

vi.mock("@/lib/confetti", () => ({ celebrate: vi.fn() }));

// Tours that use what WP17 adds (flag gating, steps behind a settings section or an inspector tab). They reuse the
// composer-settings copy so the host has real strings to show.
const tours = vi.hoisted(() => ({ list: [] as FeatureTour[] }));
vi.mock("@/lib/feature-tours", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/feature-tours")>();
  return {
    ...actual,
    get FEATURE_TOURS() { return tours.list; },
    dueTours: (createdAt: string | null | undefined, seen: readonly string[]) =>
      createdAt ? tours.list.filter((tour) => Date.parse(createdAt) < Date.parse(tour.releasedAt) && !seen.includes(tour.id)) : [],
  };
});

let flags: Record<string, boolean> | null;

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => { storage.set(key, value); } });
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as never);
  flags = { dynamic_edits: true };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/feature-flags")) return flags ? Response.json({ platform: "web", flags }) : new Response("no", { status: 500 });
    if (url.includes("/api/feature-tours")) return init?.method === "POST" ? Response.json({ ok: true }) : Response.json({ createdAt: "2026-01-01T00:00:00Z", seen: [] });
    return Response.json({});
  }));
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const STEP_TITLE = "Everything else lives here";

function tour(overrides: Partial<FeatureTour>): FeatureTour {
  return { id: "composer-settings", releasedAt: "2026-12-01T00:00:00Z", steps: [{ target: "composer-settings", message: "settings" }], ...overrides };
}

/** A page whose "edits" section only exists once something opens it, like the composer's settings popover. */
function Page({ onSection, onTab, closeRef }: { onSection?: (id: string) => void; onTab?: (id: string) => void; closeRef?: { current: (() => void) | null } }) {
  const [section, setSection] = useState<string | null>(null);
  if (closeRef) closeRef.current = () => setSection(null);
  const [tab, setTab] = useState<string | null>(null);
  return (
    <>
      <button data-tour="composer-settings">settings</button>
      {section === "edits" && <div data-tour="edit-intensity">intensity</div>}
      {tab === "edits" && <div data-tour="studio-edits-tab">edits tab</div>}
      <FeatureTourHost
        enabled
        pathname="/"
        navigate={stay}
        canStart
        busy={false}
        onSettingsSection={onSection ? (id) => { onSection(id); setSection(id); } : undefined}
        onInspectorTab={onTab ? (id) => { onTab(id); setTab(id); } : undefined}
      />
    </>
  );
}

const wait = () => act(() => new Promise((resolve) => setTimeout(resolve, 1200)));

/** The page never changes in these tests; one function, as a router's `push` is. */
const stay = () => {};

describe("FeatureTourHost: flags and hidden targets", () => {
  it("keeps a flagged tour hidden while /api/feature-flags says false", async () => {
    tours.list = [tour({ requiresFlag: "dynamic_edits" })];
    flags = { dynamic_edits: false };
    render(<Page />);
    await wait();
    expect(screen.queryByText(STEP_TITLE)).not.toBeInTheDocument();
  });

  it("keeps a flagged tour hidden when the flags can't be read", async () => {
    tours.list = [tour({ requiresFlag: "dynamic_edits" })];
    flags = null;
    render(<Page />);
    await wait();
    expect(screen.queryByText(STEP_TITLE)).not.toBeInTheDocument();
  });

  it("shows a flagged tour once the flag is on", async () => {
    tours.list = [tour({ requiresFlag: "dynamic_edits" })];
    render(<Page />);
    expect(await screen.findByText(STEP_TITLE, undefined, { timeout: 2500 })).toBeInTheDocument();
  });

  it("opens the settings section a step needs before pointing at it", async () => {
    const onSection = vi.fn();
    tours.list = [tour({
      steps: [
        { target: "composer-settings", message: "settings", side: "top" },
        { target: "edit-intensity", message: "settings", side: "top", settingsSection: "edits" },
      ],
    })];
    render(<Page onSection={onSection} />);
    await screen.findByText("1 of 2", undefined, { timeout: 2500 });
    expect(onSection).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onSection).toHaveBeenCalledWith("edits");
    expect(await screen.findByText("2 of 2")).toBeInTheDocument();
  });

  it("follows a menu that closes under a step: hides, opens it again and points at the new target", async () => {
    const onSection = vi.fn();
    const close: { current: (() => void) | null } = { current: null };
    tours.list = [tour({
      steps: [
        { target: "composer-settings", message: "settings", side: "top" },
        { target: "edit-intensity", message: "settings", side: "top", settingsSection: "edits" },
      ],
    })];
    render(<Page onSection={onSection} closeRef={close} />);
    await screen.findByText("1 of 2", undefined, { timeout: 2500 });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("2 of 2");
    expect(document.body).toHaveAttribute("data-feature-tour", "composer-settings");
    onSection.mockClear();
    // Something closed the menu (focus moving out of it, say): the old target is gone from the page.
    act(() => close.current?.());
    expect(screen.queryByText("intensity")).not.toBeInTheDocument();
    await waitFor(() => expect(onSection).toHaveBeenCalledWith("edits"));
    expect(await screen.findByText("2 of 2")).toBeInTheDocument();
    expect(screen.getByText("intensity")).toBeInTheDocument();
  });

  it("opens the inspector tab a step needs", async () => {
    const onTab = vi.fn();
    tours.list = [tour({
      steps: [
        { target: "composer-settings", message: "settings" },
        { target: "studio-edits-tab", message: "settings", inspectorTab: "edits" },
      ],
    })];
    render(<Page onTab={onTab} />);
    await screen.findByText("1 of 2", undefined, { timeout: 2500 });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onTab).toHaveBeenCalledWith("edits");
    expect(await screen.findByText("2 of 2")).toBeInTheDocument();
  });

  it("doesn't start (or record) a tour whose first target isn't on the page, opener or not", async () => {
    const onTab = vi.fn();
    tours.list = [tour({ steps: [{ target: "studio-edits-tab", message: "settings", inspectorTab: "edits" }] })];
    render(<Page onTab={onTab} />);
    await wait();
    expect(screen.queryByText(STEP_TITLE)).not.toBeInTheDocument();
    expect(onTab).not.toHaveBeenCalled();
    expect(document.body).not.toHaveAttribute("data-feature-tour");
    const posted = vi.mocked(fetch).mock.calls.filter(([input, init]) => String(input).includes("/api/feature-tours") && init?.method === "POST");
    expect(posted).toEqual([]);
  });

  it("marks the page only while a step points into the settings menu", async () => {
    tours.list = [tour({
      steps: [
        { target: "composer-settings", message: "settings", side: "top" },
        { target: "edit-intensity", message: "settings", side: "top", settingsSection: "edits" },
      ],
    })];
    render(<Page onSection={() => {}} />);
    await screen.findByText("1 of 2", undefined, { timeout: 2500 });
    expect(document.body).not.toHaveAttribute("data-feature-tour-settings");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("2 of 2");
    expect(document.body).toHaveAttribute("data-feature-tour-settings");
  });

  it("skips a step the page can't open, and leaves out steps nothing can reach", async () => {
    tours.list = [tour({
      steps: [
        { target: "composer-settings", message: "settings" },
        // No opener for inspector tabs on this page: unreachable from the start.
        { target: "studio-edits-tab", message: "settings", inspectorTab: "edits" },
        // An opener, but the section never shows this target.
        { target: "never-there", message: "settings", settingsSection: "edits" },
      ],
    })];
    render(<Page onSection={() => {}} />);
    await screen.findByText("1 of 2", undefined, { timeout: 2500 });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    // The hidden target never appears: the tour ends instead of hanging on an empty step.
    await waitFor(() => expect(screen.queryByText(STEP_TITLE)).not.toBeInTheDocument(), { timeout: 2500 });
  });
});
