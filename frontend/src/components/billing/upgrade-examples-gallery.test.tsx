import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  UPGRADE_COLLAGE_POSTER_SRC,
  UPGRADE_COLLAGE_VIDEO_SRC,
  UpgradeExamplesGallery,
} from "./upgrade-examples-gallery";
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

beforeEach(() => {
  mockReducedMotion = false;
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("UpgradeExamplesGallery", () => {
  it("renders the looping collage video with poster and /examples link", () => {
    render(<UpgradeExamplesGallery />);

    const region = screen.getByRole("region", { name: "Example clips made with SupoClip" });
    const video = region.querySelector("video");
    expect(video).not.toBeNull();
    expect(video).toHaveAttribute("src", UPGRADE_COLLAGE_VIDEO_SRC);
    expect(video).toHaveAttribute("poster", UPGRADE_COLLAGE_POSTER_SRC);
    expect(video).toHaveAttribute("autoplay");
    expect(video).toHaveAttribute("loop");
    expect(video).toHaveAttribute("playsinline");
    expect(video).toHaveAttribute("preload", "metadata");
    expect(video?.muted).toBe(true);

    const moreLink = screen.getByRole("link", { name: "More examples" });
    expect(moreLink).toHaveAttribute("href", "/examples");
    expect(moreLink).toHaveAttribute("target", "_blank");
  });

  it("renders the static poster image instead of autoplaying video under reduced motion", () => {
    mockReducedMotion = true;
    render(<UpgradeExamplesGallery />);

    const region = screen.getByRole("region", { name: "Example clips made with SupoClip" });
    expect(region.querySelector("video")).toBeNull();
    const img = region.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", UPGRADE_COLLAGE_POSTER_SRC);
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

  it("shows the hours headline and looping video collage for start, limit, and explore", () => {
    const { rerender } = render(
      <UpgradeDialog
        open
        onOpenChange={() => undefined}
        reason={{ kind: "start" }}
        billing={freeBilling}
        source="test"
      />,
    );

    const gallery = screen.getByTestId("upgrade-examples-gallery");
    expect(gallery.querySelector("video")).toHaveAttribute("src", UPGRADE_COLLAGE_VIDEO_SRC);
    expect(screen.getByText(/hours of video a month with Pro/)).toBeInTheDocument();

    rerender(
      <UpgradeDialog
        open
        onOpenChange={() => undefined}
        reason={{ kind: "limit" }}
        billing={{
          ...freeBilling,
          plan: "pro",
          subscription_status: "active",
          subscription_provider: "stripe",
          minute_limit: 600,
          remaining_minutes: 0,
          upgrade_required: true,
        }}
        source="test"
      />,
    );
    expect(screen.getByTestId("upgrade-examples-gallery").querySelector("video")).toHaveAttribute(
      "src",
      UPGRADE_COLLAGE_VIDEO_SRC,
    );

    rerender(
      <UpgradeDialog
        open
        onOpenChange={() => undefined}
        reason={{ kind: "explore" }}
        billing={freeBilling}
        source="test"
      />,
    );
    expect(screen.getByTestId("upgrade-examples-gallery").querySelector("video")).toHaveAttribute(
      "src",
      UPGRADE_COLLAGE_VIDEO_SRC,
    );
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
