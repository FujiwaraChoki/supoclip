import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ClipFocus, ClipTile, ProcessingPanel, clipProgressFromMessage, type WallClip } from "./clip-wall";
import { GenerationCard } from "./generation-card";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { GenerationSummary } from "@/lib/generations";

function clip(overrides: Partial<WallClip> = {}): WallClip {
  return {
    id: "clip-1",
    filename: "clip.mp4",
    start_time: "12:04",
    end_time: "13:20",
    duration: 76,
    text: "",
    relevance_score: 1,
    clip_order: 2,
    video_url: "/tasks/t/clips/clip-1/file",
    virality_score: 0,
    hook_score: 0,
    engagement_score: 0,
    value_score: 0,
    shareability_score: 0,
    hook_type: null,
    hook_title: null,
    ...overrides,
  };
}

const tileProps = { taskId: "t", busy: false, best: false, editable: false, onOpen: vi.fn(), onDownload: vi.fn() };

describe("workflow progress", () => {
  it("reads clip, part, recap and trailer counters", () => {
    expect(clipProgressFromMessage("Creating clip 3/5...")).toEqual({ current: 3, total: 5 });
    expect(clipProgressFromMessage("Creating part 2 / 4")).toEqual({ current: 2, total: 4 });
    expect(clipProgressFromMessage("Creating recap 1/1")).toEqual({ current: 1, total: 1 });
    expect(clipProgressFromMessage("Mapping the story")).toBeNull();
  });

  it("names the steps for what the generation makes", () => {
    render(<ProcessingPanel status="processing" progress={50} message="Mapping the story" workflow="series" />);
    const current = screen.getByText("Mapping the story", { selector: "li span" });
    expect(current.closest("li")).toHaveClass("font-medium");
    expect(screen.getByText("Cutting parts")).toBeInTheDocument();
  });

  it("counts parts while a series renders", () => {
    render(<ProcessingPanel status="processing" progress={80} message="Creating part 2/4" workflow="series" />);
    expect(screen.getByText("Part 2 of 4")).toBeInTheDocument();
    expect(screen.getByText("Cutting parts").closest("li")).toHaveClass("font-medium");
  });

  it("stays on the planning step whatever the searched topic says", () => {
    const message = "Finding where 'render farm clip 3/4' comes up...";
    render(<ProcessingPanel status="processing" progress={54} message={message} workflow="topic" />);
    expect(screen.getByText("Finding moments about your topic").closest("li")).toHaveClass("font-medium");
    expect(clipProgressFromMessage(message)).toBeNull();
    expect(screen.queryByText(/Clip 3 of 4/)).not.toBeInTheDocument();
  });

  it("keeps the highlights labels", () => {
    render(<ProcessingPanel status="processing" progress={80} message="Creating clip 1/3" />);
    expect(screen.getByText("Clip 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("Finding moments")).toBeInTheDocument();
  });
});

describe("ClipTile", () => {
  it("badges parts with their number instead of a score", () => {
    render(<ClipTile {...tileProps} partTotal={5} clip={clip({ workflow_role: "part", part_number: 2, virality_score: 70 })} />);
    expect(screen.getByText("Part 2 of 5")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Part 2" })).toBeInTheDocument();
    expect(screen.queryByText("70")).not.toBeInTheDocument();
  });

  it("titles a part without repeating the number its badge shows", () => {
    render(<ClipTile {...tileProps} partTotal={5} clip={clip({ workflow_role: "part", part_number: 2, hook_title: "Part 2: The bank calls back" })} />);
    expect(screen.getByText("Part 2 of 5")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The bank calls back" })).toBeInTheDocument();
  });

  it("keeps the score ring for topic clips", () => {
    render(<TooltipProvider><ClipTile {...tileProps} clip={clip({ workflow_role: "topic", virality_score: 70, workflow_meta: { why: "Explains pricing tiers" } })} /></TooltipProvider>);
    expect(screen.getByText("70")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Clip 2" })).toBeInTheDocument();
  });
});

describe("ClipFocus", () => {
  it("lists the source moments of a stitched clip and what a part delivers", () => {
    render(
      <ClipFocus
        clip={clip({
          workflow_role: "part",
          part_number: 2,
          hook_title: "Part 2: The bank calls back",
          workflow_meta: { ranges: [[724.31, 801.92], [815.1, 840.55]], delivers: "Why the loan fell through", ends_on: "The call nobody expected" },
        })}
        taskId="t"
        index={1}
        total={4}
        busy={false}
        editable={false}
        onNavigate={vi.fn()}
        onClose={vi.fn()}
        onDownload={vi.fn()}
        hookTypeLabel={() => ""}
      />,
    );
    expect(screen.getByText("Source moments")).toBeInTheDocument();
    expect(screen.getByText("12:04 – 13:21")).toBeInTheDocument();
    expect(screen.getByText("13:35 – 14:00")).toBeInTheDocument();
    expect(screen.getByText("Why the loan fell through")).toBeInTheDocument();
    expect(screen.getByText("Ends on: The call nobody expected")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The bank calls back" })).toBeInTheDocument();
    expect(screen.getByText("Part 2")).toBeInTheDocument();
  });

  it("numbers a part against the series it belongs to", () => {
    render(
      <ClipFocus
        clip={clip({ workflow_role: "part", part_number: 3, hook_title: "Teil 3 – Die Bank ruft an", workflow_meta: { part_count: 4 } })}
        taskId="t"
        index={2}
        total={4}
        busy={false}
        editable={false}
        onNavigate={vi.fn()}
        onClose={vi.fn()}
        onDownload={vi.fn()}
        hookTypeLabel={() => ""}
      />,
    );
    expect(screen.getByText("Part 3 of 4")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Die Bank ruft an" })).toBeInTheDocument();
  });
});

describe("GenerationCard", () => {
  const generation: GenerationSummary = {
    id: "g1",
    user_id: "u",
    source_id: "s",
    source_title: "Founder interview",
    source_type: "youtube",
    status: "completed",
    clips_count: 4,
    top_virality_score: 0,
    created_at: "2026-10-31T00:00:00Z",
    updated_at: "2026-10-31T00:00:00Z",
  };

  it("counts parts for a series and moments for a topic", () => {
    const { rerender } = render(<GenerationCard generation={{ ...generation, workflow: "series", part_count: 5 }} />);
    expect(screen.getByText("Series · 5 parts")).toBeInTheDocument();
    rerender(<GenerationCard generation={{ ...generation, workflow: "topic" }} />);
    expect(screen.getByText("Topic · 4 moments")).toBeInTheDocument();
    rerender(<GenerationCard generation={{ ...generation, workflow: "trailer", clips_count: 1 }} />);
    expect(screen.getByText("Trailer")).toBeInTheDocument();
    rerender(<GenerationCard generation={generation} />);
    expect(screen.getByText("4 clips")).toBeInTheDocument();
  });

  it("says what an active workflow is doing", () => {
    render(<GenerationCard generation={{ ...generation, status: "processing", workflow: "recap", clips_count: 0 }} />);
    expect(screen.getByText("Picking the main points…")).toBeInTheDocument();
  });
});
