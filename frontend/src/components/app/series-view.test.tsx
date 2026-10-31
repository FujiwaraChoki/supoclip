import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { orderedParts, partTitle, seriesPartTotal, SeriesView, unfinishedSeries } from "./series-view";
import type { WallClip } from "./clip-wall";
import type { WorkflowPlan } from "@/lib/workflows";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function part(n: number, overrides: Partial<WallClip> = {}): WallClip {
  return {
    id: `clip-${n}`,
    filename: `part-${n}.mp4`,
    start_time: "00:00",
    end_time: "01:00",
    duration: 61 + n,
    text: "",
    relevance_score: 1,
    clip_order: n,
    video_url: `/tasks/t/clips/clip-${n}/file`,
    virality_score: 0,
    hook_score: 0,
    engagement_score: 0,
    value_score: 0,
    shareability_score: 0,
    hook_type: null,
    hook_title: `Part ${n}: Title ${n}`,
    workflow_role: "part",
    part_number: n,
    workflow_meta: { planned: true, title: `Title ${n}`, ends_on: `Cliffhanger ${n}`, post_caption: `Caption ${n}`, part_count: 3 },
    ...overrides,
  };
}

const plan: WorkflowPlan = {
  version: "wf-v1",
  workflow: "series",
  title: "How we almost lost the company",
  parts: [1, 2, 3].map((n) => ({ part_number: n, title: `Title ${n}` })),
};

function renderSeries(props: Partial<Parameters<typeof SeriesView>[0]> = {}) {
  const handlers = { onOpen: vi.fn(), onDownload: vi.fn(), onDownloadAll: vi.fn(), onResume: vi.fn() };
  render(
    <SeriesView
      clips={[part(3), part(1), part(2)]}
      plan={plan}
      taskId="task-1"
      status="completed"
      editable
      busyId={null}
      downloadingAll={false}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SeriesView", () => {
  it("lists parts in story order with their number, title, ending and caption", () => {
    renderSeries();
    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => within(row).getByRole("heading").textContent)).toEqual(["Title 1", "Title 2", "Title 3"]);
    expect(within(rows[0]).getByText("Part 1 of 3")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Ends on: Cliffhanger 1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Caption 1")).toBeInTheDocument();
    expect(within(rows[2]).getByRole("link", { name: /edit/i })).toHaveAttribute("href", "/tasks/task-1/edit?clip=clip-3");
  });

  it("downloads every part and single parts", () => {
    const handlers = renderSeries();
    fireEvent.click(screen.getByRole("button", { name: "Download all parts" }));
    expect(handlers.onDownloadAll).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Download Part 2 of 3" }));
    expect(handlers.onDownload).toHaveBeenCalledWith(expect.objectContaining({ id: "clip-2" }));
  });

  it("copies a part's post caption", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderSeries();
    fireEvent.click(screen.getAllByRole("button", { name: "Copy caption" })[1]);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Caption 2"));
    expect(toast.success).toHaveBeenCalledWith("Caption copied");
  });

  it("offers to resume a cancelled series that is missing parts", () => {
    const handlers = renderSeries({ clips: [part(1), part(2)], status: "cancelled" });
    expect(screen.getByText("Stopped at part 2 of 3. Resume to finish the series.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(handlers.onResume).toHaveBeenCalledTimes(1);
  });

  it("says nothing about resuming a finished series", () => {
    renderSeries({ status: "completed" });
    expect(screen.queryByText(/Stopped at part/)).not.toBeInTheDocument();
  });

  it("hides editing for read-only viewers", () => {
    renderSeries({ editable: false });
    expect(screen.queryByRole("link", { name: /edit/i })).not.toBeInTheDocument();
  });
});

describe("series helpers", () => {
  it("keeps the halves of a split part together", () => {
    const halves = [part(2, { id: "b", clip_order: 4 }), part(1), part(2, { id: "a", clip_order: 2 })];
    expect(orderedParts(halves).map((clip) => clip.id)).toEqual(["clip-1", "a", "b"]);
  });

  it("counts the planned parts, then what the clips say", () => {
    expect(seriesPartTotal(plan, [part(1)])).toBe(3);
    expect(seriesPartTotal(null, [part(1), part(2)])).toBe(3);
    expect(seriesPartTotal(null, [part(1, { workflow_meta: {} })])).toBe(1);
  });

  it("knows when a cancelled series stopped early", () => {
    expect(unfinishedSeries("cancelled", plan, [part(1), part(2)])).toEqual({ done: 2, total: 3 });
    // Both halves of a split part are still one part.
    expect(unfinishedSeries("cancelled", plan, [part(1), part(2, { id: "a" }), part(2, { id: "b" })])).toEqual({ done: 2, total: 3 });
    expect(unfinishedSeries("cancelled", plan, [part(1), part(2), part(3)])).toBeNull();
    expect(unfinishedSeries("completed", plan, [part(1)])).toBeNull();
    expect(unfinishedSeries("cancelled", plan, [])).toBeNull();
  });

  it("strips the localized part label from a hook title", () => {
    const bare = (hook: string, n = 3) => partTitle({ hook_title: hook, part_number: n, workflow_meta: {} });
    expect(bare("Part 3: The bank calls back")).toBe("The bank calls back");
    expect(bare("Teil 3 – Die Bank ruft an")).toBe("Die Bank ruft an");
    expect(bare("第3集：银行回电")).toBe("银行回电");
    expect(bare("第3部分：银行回电")).toBe("银行回电");
    expect(bare("Parte 3 · El banco llama")).toBe("El banco llama");
    expect(bare("Top 10: Mistakes")).toBe("Top 10: Mistakes");
    expect(partTitle({ hook_title: "Part 3: x", part_number: 3, workflow_meta: { title: "Planned" } })).toBe("Planned");
  });
});
