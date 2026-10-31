import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TaskPage from "./page";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "task-1" }),
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1" } }, isPending: false }),
}));
// The page's SSE refresh, so a test can deliver the next server state.
const progress = vi.hoisted(() => ({ refresh: null as null | (() => Promise<unknown>) }));
vi.mock("@/hooks/use-task-progress", () => ({
  useTaskProgress: (options: { refresh: () => Promise<unknown> }) => {
    progress.refresh = options.refresh;
    return { reconnecting: false };
  },
}));
vi.mock("@/hooks/use-billing-summary", () => ({ useBillingSummary: () => [null, vi.fn()] }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

type Json = Record<string, unknown>;

function clip(n: number, overrides: Json = {}): Json {
  return {
    id: `clip-${n}`,
    filename: `clip-${n}.mp4`,
    file_path: "",
    start_time: "00:00",
    end_time: "01:00",
    duration: 60,
    text: "",
    relevance_score: 0.9,
    reasoning: "",
    clip_order: n,
    created_at: "2026-10-31T00:00:00Z",
    video_url: `/tasks/task-1/clips/clip-${n}/file`,
    virality_score: 0,
    hook_score: 0,
    engagement_score: 0,
    value_score: 0,
    shareability_score: 0,
    hook_type: null,
    hook_title: null,
    workflow_role: null,
    part_number: null,
    workflow_meta: {},
    ...overrides,
  };
}

function task(overrides: Json = {}): Json {
  return {
    id: "task-1",
    user_id: "user-1",
    source_id: "source-1",
    source_title: "Founder interview",
    source_type: "youtube",
    source_url: "https://www.youtube.com/watch?v=abcdefghijk",
    status: "completed",
    clips_count: 0,
    source_duration_seconds: 1800,
    created_at: "2026-10-31T00:00:00Z",
    updated_at: "2026-10-31T00:00:00Z",
    font_family: null,
    font_size: null,
    font_color: null,
    caption_template: "default",
    processing_mode: "fast",
    output_format: "vertical",
    clip_length: "auto",
    add_subtitles: true,
    include_broll: false,
    cut_long_pauses: false,
    pause_threshold_ms: 1200,
    remove_filler_words: false,
    filtered_words: [],
    workflow: "highlights",
    workflow_options: {},
    workflow_plan: null,
    error_code: null,
    ...overrides,
  };
}

let created: Json | null = null;

function serve(taskBody: Json, clips: Json[]) {
  created = null;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/tasks/create") {
      created = JSON.parse(String(init?.body));
      return Response.json({ task_id: "task-2" });
    }
    if (url === "/api/tasks/task-1") return Response.json(taskBody);
    if (url === "/api/tasks/task-1/clips") return Response.json({ clips });
    return Response.json({});
  }));
}

function renderPage() {
  return render(<TooltipProvider><TaskPage /></TooltipProvider>);
}

const originalLocation = window.location;

beforeEach(() => {
  Object.defineProperty(window, "location", { value: { ...originalLocation, href: "http://localhost/tasks/task-1" }, writable: true, configurable: true });
});

afterEach(() => {
  Object.defineProperty(window, "location", { value: originalLocation, writable: true, configurable: true });
});

describe("task page workflows", () => {
  it("shows a series as numbered parts without sorting or a top pick", async () => {
    const parts = [3, 1, 2].map((n) => clip(n, {
      workflow_role: "part",
      part_number: n,
      virality_score: 80 + n,
      hook_title: `Part ${n}: Chapter ${n}`,
      workflow_meta: { planned: true, title: `Chapter ${n}`, part_count: 3, post_caption: `Watch part ${n + 1}` },
    }));
    serve(task({
      workflow: "series",
      workflow_options: { part_count: 4 },
      workflow_plan: {
        version: "wf-v1",
        workflow: "series",
        title: "How we almost lost the company",
        parts: [1, 2, 3].map((n) => ({ part_number: n, title: `Chapter ${n}` })),
        notes: [{ code: "PART_COUNT_REDUCED", params: { requested: 4, actual: 3 } }],
      },
    }), parts);
    renderPage();

    const series = await screen.findByRole("region", { name: "Series · 3 parts" });
    const titles = within(series).getAllByRole("heading").map((heading) => heading.textContent);
    expect(titles).toEqual(["Chapter 1", "Chapter 2", "Chapter 3"]);
    expect(screen.getAllByText(/^Part \d of 3$/)).toHaveLength(3);
    expect(screen.getByText("Series · 3 parts")).toBeInTheDocument();
    expect(screen.getByText("How we almost lost the company")).toBeInTheDocument();
    expect(screen.getByText("You asked for 4 parts. This video supports 3.")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText("Top pick")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download all parts" })).toBeInTheDocument();
    // The badge already counts the parts; the meta row gives their running time.
    expect(screen.getByText("3:00 total")).toBeInTheDocument();
    expect(screen.queryByText(/3 clips/)).not.toBeInTheDocument();
  });

  it("offers one Resume for a series that was stopped part-way", async () => {
    const parts = [1, 2].map((n) => clip(n, { workflow_role: "part", part_number: n, workflow_meta: { title: `Chapter ${n}`, part_count: 4 } }));
    serve(task({
      status: "cancelled",
      workflow: "series",
      workflow_plan: { version: "wf-v1", workflow: "series", parts: [1, 2, 3, 4].map((n) => ({ part_number: n, title: `Chapter ${n}` })) },
    }), parts);
    renderPage();

    expect(await screen.findByText("Stopped at part 2 of 4. Resume to finish the series.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Resume" })).toHaveLength(1);
  });

  it("drops parts the server deleted when a series fails", async () => {
    const parts = [1, 2].map((n) => clip(n, { workflow_role: "part", part_number: n }));
    serve(task({ status: "processing", workflow: "series" }), parts);
    renderPage();
    expect(await screen.findByText("Part 1 of 2")).toBeInTheDocument();

    serve(task({
      status: "error",
      workflow: "series",
      error_code: "render_incomplete",
      workflow_plan: { version: "wf-v1", workflow: "series", error: { code: "render_incomplete", details: {} } },
    }), []);
    await act(async () => { await progress.refresh?.(); });

    expect(await screen.findByRole("heading", { name: "Part of the video couldn't be rendered" })).toBeInTheDocument();
    expect(screen.queryByText(/clips? ·|total$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Part 1 of/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  });

  it("keeps highlights sorted best first with a top pick", async () => {
    serve(task(), [clip(1, { virality_score: 60 }), clip(2, { virality_score: 90, hook_title: "The best one" })]);
    renderPage();

    expect(await screen.findByRole("tablist", { name: "Sort clips" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Best first" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Top pick")).toBeInTheDocument();
    const headings = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(headings).toEqual(["The best one", "Clip 1"]);
  });

  it("heads topic clips with the topic and their count", async () => {
    serve(task({
      workflow: "topic",
      workflow_options: { topic: "pricing" },
      workflow_plan: { version: "wf-v1", workflow: "topic", topic: { query: "pricing", canonical: "Pricing", coverage: "thin", found: 2 } },
    }), [clip(1, { workflow_role: "topic", virality_score: 70 }), clip(2, { workflow_role: "topic", virality_score: 75 })]);
    renderPage();

    expect(await screen.findByRole("heading", { name: "About pricing · 2 moments" })).toBeInTheDocument();
    expect(screen.getByText("Only 2 moments are really about this topic.")).toBeInTheDocument();
    expect(screen.getByText("Topic: pricing")).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Sort clips" })).toBeInTheDocument();
  });

  it("shows a recap as one hero video", async () => {
    serve(task({
      workflow: "recap",
      workflow_plan: { version: "wf-v1", workflow: "recap", title: "The three pricing rules", points: [{ label: "Signal" }] },
    }), [clip(1, {
      workflow_role: "recap",
      hook_title: "Three rules for pricing",
      workflow_meta: { ranges: [[10, 20], [300, 320]], points: [{ label: "Signal", kind: "opener", range_index: 0, output_start: 0 }, { label: "Raise", kind: "point", range_index: 1, output_start: 10 }] },
    })]);
    renderPage();

    expect(await screen.findByRole("heading", { name: "Covers 2 points" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Three rules for pricing" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("turns a suggested topic into a new generation from the same source", async () => {
    serve(task({
      status: "error",
      workflow: "topic",
      error_code: "topic_not_found",
      clip_length: "short",
      caption_template: "bold",
      workflow_options: { topic: "crypto" },
      workflow_plan: {
        version: "wf-v1",
        workflow: "topic",
        error: { code: "topic_not_found", details: { suggested_topics: ["pricing", "hiring"] } },
      },
    }), []);
    renderPage();

    expect(await screen.findByRole("heading", { name: "“crypto” barely comes up in this video" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "pricing" }));

    await waitFor(() => expect(window.location.href).toBe("/tasks/task-2"));
    expect(created).toMatchObject({
      source: { url: "https://www.youtube.com/watch?v=abcdefghijk", title: null },
      caption_template: "bold",
      clip_length: "short",
      workflow: "topic",
      workflow_options: { topic: "pricing" },
    });
  });
});
