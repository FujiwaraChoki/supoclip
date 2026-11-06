import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SharedGenerationPage from "./page";

vi.mock("next/navigation", () => ({ useParams: () => ({ token: "tok" }) }));

function clip(n: number, overrides: Record<string, unknown> = {}) {
  return {
    id: `clip-${n}`, filename: `clip-${n}.mp4`, start_time: "00:00", end_time: "01:00", duration: 60, text: "",
    relevance_score: 1, reasoning: "", clip_order: n, virality_score: 0, hook_title: null,
    workflow_role: null, part_number: null, workflow_meta: {}, ...overrides,
  };
}

function serve(body: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({
    source_title: "Founder interview", source_type: "youtube", status: "completed", clips_count: 0,
    created_at: "2026-10-31T00:00:00Z", workflow: "highlights", workflow_plan: null, ...body,
  })));
}

describe("shared generation", () => {
  it("shows a series in part order without scores", async () => {
    serve({
      workflow: "series",
      workflow_plan: { version: "wf-v1", workflow: "series", title: "How we almost lost it", parts: [{ part_number: 1, title: "A" }, { part_number: 2, title: "B" }] },
      clips: [
        clip(2, { workflow_role: "part", part_number: 2, virality_score: 90, hook_title: "Part 2: B" }),
        clip(1, { workflow_role: "part", part_number: 1, virality_score: 40, hook_title: "Part 1: A" }),
      ],
    });
    render(<SharedGenerationPage />);

    expect(await screen.findByText("How we almost lost it")).toBeInTheDocument();
    expect(screen.getByText("Series · 2 parts")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["A", "B"]);
    expect(screen.getByText("Part 1 of 2")).toBeInTheDocument();
    expect(screen.queryByText(/top virality score/)).not.toBeInTheDocument();
    expect(screen.queryByText("Top pick")).not.toBeInTheDocument();
  });

  it("shows a trailer as one hero video", async () => {
    serve({
      workflow: "trailer",
      workflow_plan: { version: "wf-v1", workflow: "trailer", title: "The night everything changed", beats: [{ role: "cold_open" }] },
      clips: [clip(1, { workflow_role: "trailer", workflow_meta: { beats: [{ role: "cold_open", range_index: 0, output_start: 0 }, { role: "button", range_index: 1, output_start: 12 }] } })],
    });
    render(<SharedGenerationPage />);

    expect(await screen.findByRole("heading", { name: "Beats" })).toBeInTheDocument();
    expect(screen.getAllByText("Trailer").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Hook/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open in editor/i })).not.toBeInTheDocument();
  });

  it("keeps highlights as before", async () => {
    serve({ clips: [clip(1, { virality_score: 50 }), clip(2, { virality_score: 80, hook_title: "Best" })] });
    render(<SharedGenerationPage />);

    expect(await screen.findByText("Top pick")).toBeInTheDocument();
    expect(screen.getByText(/top virality score/)).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["Best", "Clip 1"]);
  });
});
