import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HeroClipView, heroMoments } from "./hero-clip-view";
import type { WallClip } from "./clip-wall";
import type { WorkflowPlan } from "@/lib/workflows";

function clip(overrides: Partial<WallClip> = {}): WallClip {
  return {
    id: "recap-1",
    filename: "recap.mp4",
    start_time: "02:00",
    end_time: "03:10",
    duration: 74,
    text: "",
    relevance_score: 1,
    clip_order: 1,
    video_url: "/tasks/t/clips/recap-1/file",
    virality_score: 0,
    hook_score: 0,
    engagement_score: 0,
    value_score: 0,
    shareability_score: 0,
    hook_type: null,
    hook_title: "Three rules for pricing",
    workflow_role: "recap",
    part_number: null,
    workflow_meta: {
      planned: true,
      ranges: [[120.5, 140], [600, 630], [3725, 3750]],
      points: [
        { label: "Why price is a signal", kind: "opener", range_index: 0, output_start: 0 },
        { label: "Charge more than feels safe", kind: "point", range_index: 1, output_start: 19.4 },
        { label: "Raise prices every year", kind: "closer", range_index: 2, output_start: 49.8 },
      ],
    },
    ...overrides,
  };
}

describe("HeroClipView", () => {
  it("shows a recap's points with their source times and seeks the player", () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    render(<HeroClipView workflow="recap" clip={clip()} taskId="task-1" editable busy={false} onDownload={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Three rules for pricing" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Covers 3 points" })).toBeInTheDocument();
    expect(screen.getByText("From 2:00 in the original")).toBeInTheDocument();
    expect(screen.getByText("From 1:02:05 in the original")).toBeInTheDocument();
    expect(screen.getByText("Intro")).toBeInTheDocument();
    expect(screen.getByText("Takeaway")).toBeInTheDocument();

    const video = document.querySelector("video")!;
    fireEvent.click(screen.getByRole("button", { name: /Charge more than feels safe/ }));
    expect(video.currentTime).toBeCloseTo(19.4);
    expect(play).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Charge more than feels safe/ })).toHaveAttribute("aria-current", "true");
    play.mockRestore();
  });

  it("lists a trailer's beats by role", () => {
    const trailer = clip({
      id: "trailer-1",
      workflow_role: "trailer",
      hook_title: null,
      workflow_meta: {
        ranges: [[900, 905], [30, 40], [400, 412]],
        beats: [
          { role: "cold_open", range_index: 0, output_start: 0 },
          { role: "context", range_index: 1, output_start: 5.2 },
          { role: "button", range_index: 2, output_start: 15.6 },
        ],
      },
    });
    const plan: WorkflowPlan = { version: "wf-v1", workflow: "trailer", title: "The night everything changed" };
    render(<HeroClipView workflow="trailer" clip={trailer} plan={plan} taskId="task-1" editable={false} busy={false} onDownload={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "The night everything changed" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Beats" })).toBeInTheDocument();
    const beats = within(screen.getByRole("list")).getAllByRole("listitem").map((item) => item.textContent);
    expect(beats[0]).toContain("Hook");
    expect(beats[0]).toContain("15:00");
    expect(beats[2]).toContain("Ending");
    expect(screen.queryByRole("link", { name: /open in editor/i })).not.toBeInTheDocument();
  });

  it("downloads and links to the editor", () => {
    const onDownload = vi.fn();
    render(<HeroClipView workflow="recap" clip={clip()} taskId="task-1" editable busy={false} onDownload={onDownload} />);
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(onDownload).toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /open in editor/i })).toHaveAttribute("href", "/tasks/task-1/edit?clip=recap-1");
  });
});

describe("heroMoments", () => {
  it("falls back to the plan when the clip has no points", () => {
    const plan: WorkflowPlan = {
      version: "wf-v1",
      workflow: "recap",
      points: [{ label: "One", ranges: [[10, 20]], output_start: 0 }, { label: "Two" }],
    };
    expect(heroMoments("recap", clip({ workflow_meta: {} }), plan)).toEqual([
      { key: "p0", label: "One", outputStart: 0, sourceStart: 10 },
      { key: "p1", label: "Two", outputStart: null, sourceStart: null },
    ]);
  });

  it("has no source times on shared pages, where ranges are private", () => {
    const shared = clip({ workflow_meta: { points: [{ label: "One", range_index: 0, output_start: 0 }] } });
    expect(heroMoments("recap", shared)[0].sourceStart).toBeNull();
  });
});
