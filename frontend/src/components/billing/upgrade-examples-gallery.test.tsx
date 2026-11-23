import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/setup";
import { LOCAL_EXAMPLE_CLIPS, type GalleryClip } from "@/lib/example-gallery";
import { UpgradeExamplesGallery } from "./upgrade-examples-gallery";
import { UpgradeDialog } from "./upgrade-prompt";

let mockReducedMotion = false;

vi.mock("motion/react", async () => {
  const actual = await vi.importActual<typeof import("motion/react")>("motion/react");
  return {
    ...actual,
    useReducedMotion: () => mockReducedMotion,
  };
});

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

beforeEach(() => {
  mockReducedMotion = false;
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("UpgradeExamplesGallery", () => {
  it("renders bundled running video cards immediately, then upgrades asynchronously and caches the feed", async () => {
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

    // One app-wide query cache, as in the app, so a reopened dialog can reuse the catalog.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { unmount } = render(<UpgradeExamplesGallery />, { wrapper });

    // Instant seed from local bundled clips before /api/examples resolves.
    const initialCards = screen.getAllByTestId("upgrade-gallery-card");
    expect(initialCards).toHaveLength(6);
    const firstVideo = initialCards[0].querySelector("video");
    expect(firstVideo).not.toBeNull();
    expect(firstVideo).toHaveAttribute("src", LOCAL_EXAMPLE_CLIPS[0].videoUrl);
    expect(firstVideo).toHaveAttribute("poster", LOCAL_EXAMPLE_CLIPS[0].posterUrl);
    expect(firstVideo).toHaveAttribute("autoplay");
    expect(firstVideo).toHaveAttribute("loop");
    expect(firstVideo).toHaveAttribute("playsinline");
    expect(firstVideo?.muted).toBe(true);

    const moreLink = screen.getByRole("link", { name: "More examples" });
    expect(moreLink).toHaveAttribute("href", "/examples");
    expect(moreLink).toHaveAttribute("target", "_blank");

    // Resolve async feed and verify live catalog replaces local seed.
    resolveResponse();
    await waitFor(() => {
      expect(
        screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1"),
      ).toBe(true);
    });
    expect(screen.getAllByTestId("upgrade-gallery-card")[0].querySelector("video")).toHaveAttribute(
      "src",
      remoteClips[0].videoUrl,
    );
    expect(requestCount).toBe(1);

    // Reopening the modal reuses the cached catalog without re-fetching.
    unmount();
    render(<UpgradeExamplesGallery />, { wrapper });
    expect(
      screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1"),
    ).toBe(true);
    expect(requestCount).toBe(1);
  });

  it("stays smoothly on bundled local clips when /api/examples fails", async () => {
    server.use(http.get("/api/examples", () => HttpResponse.error()));

    render(<UpgradeExamplesGallery />);
    await waitFor(() => {
      expect(screen.getAllByTestId("upgrade-gallery-card")[0]).toHaveAttribute("data-clip-id", LOCAL_EXAMPLE_CLIPS[0].id);
    });
  });

  it("asks once per page load, even when the catalog failed", async () => {
    let requestCount = 0;
    server.use(http.get("/api/examples", () => { requestCount += 1; return HttpResponse.error(); }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { unmount } = render(<UpgradeExamplesGallery />, { wrapper });
    await waitFor(() => expect(requestCount).toBe(1));
    await waitFor(() => expect(queryClient.isFetching()).toBe(0));
    unmount();
    render(<UpgradeExamplesGallery />, { wrapper });
    expect(screen.getAllByTestId("upgrade-gallery-card")[0]).toHaveAttribute("data-clip-id", LOCAL_EXAMPLE_CLIPS[0].id);
    expect(requestCount).toBe(1);
  });

  it("renders static poster images instead of autoplaying videos under reduced motion", async () => {
    mockReducedMotion = true;
    server.use(http.get("/api/examples", () => HttpResponse.json({ clips: remoteClips, source: "manifest" })));

    render(<UpgradeExamplesGallery />);
    await waitFor(() => {
      expect(
        screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1"),
      ).toBe(true);
    });

    const region = screen.getByRole("region", { name: "Example clips made with SupoClip" });
    expect(region.querySelectorAll("video")).toHaveLength(0);
    const images = region.querySelectorAll("img");
    expect(images.length).toBeGreaterThanOrEqual(6);
    expect(images[0]).toHaveAttribute("src", remoteClips[0].posterUrl);
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

  it("shows the hours headline and live video gallery for start, limit, and explore", async () => {
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
      expect(
        screen.getAllByTestId("upgrade-gallery-card").some((c) => c.getAttribute("data-clip-id") === "remote-gaming-1"),
      ).toBe(true);
    });
  });

  it("keeps LengthIllustration for video_too_long and FontIllustration for custom_fonts", () => {
    const { rerender } = render(
      <UpgradeDialog
        open
        onOpenChange={() => undefined}
        reason={{ kind: "video_too_long", durationSeconds: 7200, maxSeconds: 5400 }}
        billing={{
          ...freeBilling,
          plan: "pro",
          subscription_status: "active",
          subscription_provider: "stripe",
          minute_limit: 600,
          remaining_minutes: 400,
          upgrade_required: false,
        }}
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
