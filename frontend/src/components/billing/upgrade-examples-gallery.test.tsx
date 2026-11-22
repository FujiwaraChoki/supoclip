import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/setup";
import { LOCAL_EXAMPLE_CLIPS, type GalleryClip } from "@/lib/example-gallery";
import { resetUpgradeExamplesGalleryCache, UpgradeExamplesGallery } from "./upgrade-examples-gallery";
import { UpgradeDialog } from "./upgrade-prompt";

vi.mock("@/lib/analytics", () => ({ trackEvent: vi.fn() }));
vi.mock("@/lib/datafast", () => ({ track: vi.fn() }));
vi.mock("@/hooks/use-workspace", () => ({ useTeamRole: () => null }));

const remoteClips: GalleryClip[] = [
  {
    id: "remote-gaming-1",
    title: "Who knew my landlord was my ex",
    group: "gaming",
    posterUrl: "https://media.example.test/examples/v1/gta/clip-1.jpg",
    videoUrl: "https://media.example.test/examples/v1/gta/clip-1.mp4",
    viralityScore: 95,
    durationSeconds: 45,
  },
  {
    id: "remote-interview-1",
    title: "What one smuggled phone actually pays",
    group: "interview",
    posterUrl: "https://media.example.test/examples/v1/doc/clip-1.jpg",
    videoUrl: "https://media.example.test/examples/v1/doc/clip-1.mp4",
    viralityScore: 92,
    durationSeconds: 37.4,
  },
  {
    id: "remote-vlog-1",
    title: "Crossing the bridge at sunrise",
    group: "vlog",
    posterUrl: "https://media.example.test/examples/v1/vlog/clip-1.jpg",
    videoUrl: "https://media.example.test/examples/v1/vlog/clip-1.mp4",
    viralityScore: 88,
    durationSeconds: 41,
  },
];

function mockMatchMedia(options: { hoverFine?: boolean; reducedMotion?: boolean }) {
  const { hoverFine = true, reducedMotion = false } = options;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches:
        query === "(hover: hover) and (pointer: fine)"
          ? hoverFine
          : query === "(prefers-reduced-motion: reduce)"
            ? reducedMotion
            : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  resetUpgradeExamplesGalleryCache();
  mockMatchMedia({ hoverFine: true, reducedMotion: false });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("UpgradeExamplesGallery", () => {
  it("renders bundled local clips immediately, then upgrades asynchronously and caches the remote feed", async () => {
    let requestCount = 0;
    let resolveResponse!: () => void;
    const gate = new Promise<void>((resolve) => {
      resolveResponse = resolve;
    });

    server.use(
      http.get("/api/examples", async () => {
        requestCount += 1;
        await gate;
        return HttpResponse.json({ clips: remoteClips, source: "manifest" });
      }),
    );

    const { unmount } = render(<UpgradeExamplesGallery />);

    // Instant seed from local bundled clips before /api/examples resolves.
    const initialCards = screen.getAllByTestId("upgrade-gallery-card");
    expect(initialCards.length).toBeGreaterThanOrEqual(LOCAL_EXAMPLE_CLIPS.length);
    expect(initialCards.some((card) => card.getAttribute("data-clip-id") === LOCAL_EXAMPLE_CLIPS[0].id)).toBe(true);
    expect(document.querySelectorAll("video")).toHaveLength(0);

    // Link to the full examples catalog.
    const moreLink = screen.getByRole("link", { name: "More examples" });
    expect(moreLink).toHaveAttribute("href", "/examples");

    // Resolve async feed and verify live catalog replaces local seed.
    resolveResponse();
    await waitFor(() => {
      expect(screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1")).toBe(true);
    });
    expect(screen.getAllByText("Gaming").length).toBeGreaterThan(0);
    expect(screen.getAllByText("95").length).toBeGreaterThan(0);
    expect(requestCount).toBe(1);

    // Reopening the modal reuses the module cache without re-fetching.
    unmount();
    render(<UpgradeExamplesGallery />);
    expect(screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1")).toBe(true);
    expect(requestCount).toBe(1);
  });

  it("stays smoothly on bundled local clips when /api/examples fails", async () => {
    server.use(http.get("/api/examples", () => HttpResponse.error()));

    render(<UpgradeExamplesGallery />);
    await waitFor(() => {
      expect(screen.getAllByTestId("upgrade-gallery-card")[0]).toHaveAttribute("data-clip-id", LOCAL_EXAMPLE_CLIPS[0].id);
    });
  });

  it("mounts a single muted looping video on hover and skips preview under reduced motion", async () => {
    server.use(http.get("/api/examples", () => HttpResponse.json({ clips: remoteClips, source: "manifest" })));

    const { unmount } = render(<UpgradeExamplesGallery />);
    await waitFor(() => {
      expect(screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1")).toBe(true);
    });

    const cards = screen.getAllByTestId("upgrade-gallery-card");
    expect(document.querySelectorAll("video")).toHaveLength(0);

    fireEvent.mouseEnter(cards[0]);
    expect(document.querySelectorAll("video")).toHaveLength(1);
    const firstVideo = cards[0].querySelector("video");
    expect(firstVideo).not.toBeNull();
    expect(firstVideo).toHaveAttribute("src", remoteClips[0].videoUrl);
    expect(firstVideo).toHaveAttribute("playsinline");
    expect(firstVideo).toHaveAttribute("loop");

    // Hovering a second card moves the single video element rather than mounting two.
    fireEvent.mouseEnter(cards[1]);
    expect(document.querySelectorAll("video")).toHaveLength(1);
    expect(cards[0].querySelector("video")).toBeNull();
    expect(cards[1].querySelector("video")).not.toBeNull();

    fireEvent.mouseLeave(cards[1]);
    expect(document.querySelectorAll("video")).toHaveLength(0);

    // Under prefers-reduced-motion: reduce, hover never mounts a preview video.
    unmount();
    mockMatchMedia({ hoverFine: true, reducedMotion: true });
    render(<UpgradeExamplesGallery />);
    fireEvent.mouseEnter(screen.getAllByTestId("upgrade-gallery-card")[0]);
    expect(document.querySelectorAll("video")).toHaveLength(0);
  });
});

describe("UpgradeDialog showcase panel", () => {
  const freeBilling = {
    plan: "free",
    subscription_status: "inactive",
    subscription_provider: null,
    usage_count: 0,
    minute_limit: 0,
    remaining_minutes: 0,
    period_end: null,
    upgrade_required: true,
  };

  it("shows the hours headline and moving examples gallery for start/limit/explore", async () => {
    server.use(http.get("/api/examples", () => HttpResponse.json({ clips: remoteClips, source: "manifest" })));

    render(
      <UpgradeDialog
        open
        onOpenChange={() => undefined}
        reason={{ kind: "start" }}
        billing={freeBilling}
        source="test"
      />,
    );

    expect(screen.getByTestId("upgrade-examples-gallery")).toBeInTheDocument();
    expect(screen.getByText(/hours of video a month with Pro/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1")).toBe(true);
    });
  });

  it("keeps LengthIllustration for video_too_long and FontIllustration for custom_fonts", () => {
    const { rerender } = render(
      <UpgradeDialog
        open
        onOpenChange={() => undefined}
        reason={{ kind: "video_too_long", durationSeconds: 7200, maxSeconds: 5400 }}
        billing={{ ...freeBilling, plan: "pro", subscription_status: "active", subscription_provider: "stripe", minute_limit: 600, remaining_minutes: 400, upgrade_required: false }}
        source="test"
      />,
    );

    expect(screen.queryByTestId("upgrade-examples-gallery")).not.toBeInTheDocument();
    expect(screen.getByText("Your video")).toBeInTheDocument();

    rerender(
      <UpgradeDialog
        open
        onOpenChange={() => undefined}
        reason={{ kind: "custom_fonts" }}
        billing={freeBilling}
        source="test"
      />,
    );

    expect(screen.queryByTestId("upgrade-examples-gallery")).not.toBeInTheDocument();
    expect(screen.getByText("or your own .ttf")).toBeInTheDocument();
  });
});
