import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TaskPage from "./page";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "task-1" }),
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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
    if (String(input).includes("/api/tasks/workflows")) {
      return Response.json({ enabled: true, workflows: ["highlights", "series", "topic", "recap", "trailer"].map((id) => ({ id, available: true, customizable: true })) });
    }
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
        notes: [
          { code: "PART_COUNT_REDUCED", params: { requested: 4, actual: 3 } },
          { code: "CHAPTERS_LEFT_OUT", params: { chapters: "Priest killed by falling cross" } },
        ],
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
    expect(screen.getByText("Left out to fit: Priest killed by falling cross. Add instructions to change what stays.")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText("Top pick")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download all parts" })).toBeInTheDocument();
    // The badge already counts the parts; the meta row gives their running time.
    expect(screen.getByText("3:00 total")).toBeInTheDocument();
    expect(screen.queryByText(/3 clips/)).not.toBeInTheDocument();
  });

  it("downloads a series as one ZIP of its parts", async () => {
    const parts = [2, 1].map((n) => clip(n, { workflow_role: "part", part_number: n, workflow_meta: { title: `Chapter ${n}`, part_count: 2 } }));
    serve(task({ workflow: "series" }), parts);
    const served = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => String(input) === "/api/tasks/task-1/clips/zip/preflight"
      ? Response.json({ filename: "founder-interview-clips.zip", clip_count: 2, total_bytes: 10 })
      : served(input, init));
    const clicked: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.getAttribute("href") ?? "");
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Download all parts" }));

    await waitFor(() => expect(clicked).toEqual(["/api/tasks/task-1/clips/zip"]));
    // One archive request, not one download per part.
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/clips/zip"))).toHaveLength(1);
    click.mockRestore();
  });

  it("keeps sorting next to the ZIP download for highlights", async () => {
    serve(task(), [clip(1, { virality_score: 70 }), clip(2, { virality_score: 90 })]);
    renderPage();

    expect(await screen.findByRole("tablist")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download all" })).toBeInTheDocument();
  });

  it("offers one retry for a series that was stopped part-way", async () => {
    const parts = [1, 2].map((n) => clip(n, { workflow_role: "part", part_number: n, workflow_meta: { title: `Chapter ${n}`, part_count: 4 } }));
    serve(task({
      status: "cancelled",
      workflow: "series",
      workflow_plan: { version: "wf-v1", workflow: "series", parts: [1, 2, 3, 4].map((n) => ({ part_number: n, title: `Chapter ${n}` })) },
    }), parts);
    renderPage();

    expect(await screen.findByText("Stopped at part 2 of 4. Retry to finish the series.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry generation" })).toHaveLength(1);
  });

  it("keeps the finished parts once a resume is accepted", async () => {
    const parts = [1, 2].map((n) => clip(n, { workflow_role: "part", part_number: n, workflow_meta: { title: `Chapter ${n}`, part_count: 4 } }));
    const plan = { version: "wf-v1", workflow: "series", parts: [1, 2, 3, 4].map((n) => ({ part_number: n, title: `Chapter ${n}` })) };
    serve(task({ status: "cancelled", workflow: "series", workflow_plan: plan }), parts);
    const served = vi.mocked(fetch).getMockImplementation()!;
    let resumed = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/tasks/task-1/resume") {
        resumed = true;
        return Response.json({ message: "resumed" });
      }
      if (resumed && url === "/api/tasks/task-1") {
        return Response.json(task({ status: "queued", workflow: "series", workflow_plan: plan }));
      }
      return served(input, init);
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Chapter 1" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry generation" }));

    // The worker renders only the missing parts of the same plan.
    await waitFor(() => expect(resumed).toBe(true));
    expect(await screen.findByRole("heading", { name: "Chapter 2" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Chapter 1" })).toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "Retry generation" })).not.toBeInTheDocument();
  });

  it("explains an age-restricted YouTube video instead of offering a retry", async () => {
    serve(task({ status: "error", error_code: "youtube_age_restricted" }), []);
    renderPage();

    expect(await screen.findByText(/age-restricted, so we can't download it/)).toBeInTheDocument();
    expect(screen.queryByText(/Retry generation to try again/)).not.toBeInTheDocument();
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
      workflow_plan: {
        version: "wf-v1",
        workflow: "topic",
        topic: { query: "pricing", canonical: "Pricing", coverage: "thin", found: 2 },
        notes: [
          { code: "TOPIC_BROADENED", params: { topic: "pricing", interpretation: "how the founder charges customers" } },
          { code: "RECAP_UNDER_WINDOW", params: { seconds: 30, min: 38 } },
        ],
      },
    }), [clip(1, { workflow_role: "topic", virality_score: 70 }), clip(2, { workflow_role: "topic", virality_score: 75 })]);
    renderPage();

    expect(await screen.findByRole("heading", { name: "About pricing · 2 moments" })).toBeInTheDocument();
    expect(screen.getByText("Only 2 moments are really about this topic.")).toBeInTheDocument();
    expect(screen.getByText("\"pricing\" is broad for this video, so we took it to mean: how the founder charges customers")).toBeInTheDocument();
    expect(screen.queryByText(/under its window/i)).not.toBeInTheDocument();
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

    expect(await screen.findByRole("heading", { name: "Covers 1 point" })).toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "Retry generation" })).not.toBeInTheDocument();
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

describe("task page dynamic edits", () => {
  const JOB = "0123456789abcdef0123456789abcdef";
  const ENABLED = {
    enabled: true, intensities: ["off", "subtle", "bold", "max"], defaults: {}, settings: [],
    music_moods: ["hype"], grades: ["none"], templates: ["slam", "behind_head"], reactions_available: false,
  };
  let settingsBodies: Json[];
  let jobStates: Json[];
  let taskFetches: number;
  let templateUrls: string[];

  function serveFx(taskBody: Json, clips: Json[], { enabled = true, settingsStatus = 202 } = {}) {
    settingsBodies = [];
    taskFetches = 0;
    templateUrls = [];
    created = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/tasks/workflows")) {
        return Response.json({ enabled: true, workflows: ["highlights", "topic"].map((id) => ({ id, available: true, customizable: true })) });
      }
      if (url === "/api/tasks/edit-styles") return Response.json(enabled ? ENABLED : { ...ENABLED, enabled: false });
      if (url.startsWith("/api/caption-templates")) {
        templateUrls.push(url);
        const fx = url.includes("include=fx") ? [{ id: "slam", name: "Slam", description: "", fx: true }] : [];
        return Response.json({ templates: [{ id: "default", name: "Default", description: "" }, ...fx] });
      }
      if (url === "/api/tasks/create") {
        created = JSON.parse(String(init?.body));
        return Response.json({ task_id: "task-2" });
      }
      if (url === "/api/tasks/task-1/settings") {
        settingsBodies.push(JSON.parse(String(init?.body)));
        return settingsStatus === 202
          ? Response.json({ status: "queued", job_id: JOB, kind: "settings" }, { status: 202 })
          : Response.json({ task: taskBody, message: "Task settings updated" });
      }
      if (url === `/api/tasks/task-1/clip-jobs/${JOB}`) return Response.json(jobStates.shift() ?? { status: "done", kind: "settings", progress: 1 });
      if (url === "/api/tasks/task-1") { taskFetches += 1; return Response.json(taskBody); }
      if (url === "/api/tasks/task-1/clips") return Response.json({ clips });
      return Response.json({});
    }));
  }

  async function openRestyle() {
    const more = await screen.findByRole("button", { name: "More actions" });
    fireEvent.pointerDown(more, { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Restyle" }));
    return screen.findByRole("dialog");
  }

  beforeEach(() => {
    jobStates = [];
    window.sessionStorage.clear();
  });

  afterEach(() => { vi.unstubAllGlobals(); });

  it("queues a re-render (202), marks the clips and refetches when the job is done", async () => {
    jobStates = [{ status: "running", kind: "settings", progress: 0.4 }, { status: "done", kind: "settings", progress: 1 }];
    serveFx(task({ edit_style: { intensity: "bold" }, caption_template: "slam" }), [clip(1, { fx_meta: { fx_level: "full", timing: "word", intensity: "bold", template: "slam" } }), clip(2)]);
    renderPage();
    await waitFor(() => expect(templateUrls).toContain("/api/caption-templates?include=fx"));
    const sheet = await openRestyle();
    expect(within(sheet).getByRole("combobox", { name: "Edit intensity" })).toHaveTextContent("Bold");
    fireEvent.click(within(sheet).getByRole("switch", { name: "Music" }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Apply to All Clips" }));

    await waitFor(() => expect(settingsBodies).toHaveLength(1));
    expect(settingsBodies[0]).toMatchObject({ caption_template: "slam", apply_to_existing: true, edit_style: { intensity: "bold", music: false } });
    expect(await screen.findAllByText("Rendering edits…")).toHaveLength(3); // the banner and both clips
    const fetchesBefore = taskFetches;
    await waitFor(() => expect(screen.getByText("40%")).toBeInTheDocument(), { timeout: 4000 });
    await waitFor(() => expect(screen.queryByText("Rendering edits…")).not.toBeInTheDocument(), { timeout: 5000 });
    expect(taskFetches).toBeGreaterThan(fetchesBefore);
    expect(window.sessionStorage.getItem("supoclip.clipJob.task-1")).toBeNull();
  }, 15000);

  it("keeps the stored style out of the body when only the font changed", async () => {
    serveFx(task({ edit_style: { intensity: "subtle" } }), [clip(1)], { settingsStatus: 200 });
    renderPage();
    const sheet = await openRestyle();
    fireEvent.click(within(sheet).getByRole("button", { name: "Apply to All Clips" }));
    await waitFor(() => expect(settingsBodies).toHaveLength(1));
    expect(settingsBodies[0]).not.toHaveProperty("edit_style");
  });

  it("shows a failed job with a retry that sends the settings again", async () => {
    jobStates = [{ status: "failed", kind: "settings", progress: 0, error: { code: "CLIP_JOB_FAILED", message: "The renderer ran out of time." } }];
    serveFx(task({ edit_style: { intensity: "bold" } }), [clip(1)]);
    renderPage();
    const sheet = await openRestyle();
    fireEvent.click(within(sheet).getByRole("button", { name: "Apply to All Clips" }));
    const alert = await screen.findByRole("alert", undefined, { timeout: 5000 });
    expect(alert).toHaveTextContent("The edits couldn't be applied: The renderer ran out of time.");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(settingsBodies).toHaveLength(2));
  }, 10000);

  it("follows a job queued before a reload", async () => {
    window.sessionStorage.setItem("supoclip.clipJob.task-1", JSON.stringify({ id: JOB, kind: "settings", clipIds: "all" }));
    jobStates = [{ status: "done", kind: "settings", progress: 1 }];
    serveFx(task(), [clip(1)]);
    renderPage();
    expect(await screen.findAllByText("Rendering edits…")).not.toHaveLength(0);
    await waitFor(() => expect(screen.queryByText("Rendering edits…")).not.toBeInTheDocument(), { timeout: 5000 });
  }, 10000);

  it("explains approximate timing and simplified effects on a clip", async () => {
    serveFx(task(), [clip(1, { hook_title: "The hook", fx_meta: { fx_level: "safe", timing: "approx", intensity: "bold", template: "slam" } })]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Preview The hook" }));
    expect(await screen.findByText(/follow approximate timing/)).toBeInTheDocument();
    expect(screen.getByText("Some effects were simplified for this clip.")).toBeInTheDocument();
  });

  it("offers no edits and no animated templates while the feature is off", async () => {
    serveFx(task(), [clip(1)], { enabled: false });
    renderPage();
    const sheet = await openRestyle();
    expect(within(sheet).queryByRole("combobox", { name: "Edit intensity" })).not.toBeInTheDocument();
    expect(templateUrls.every((url) => !url.includes("include"))).toBe(true);
  });

  it("still lets a task that has edits turn them off without the feature", async () => {
    serveFx(task({ edit_style: '{"intensity": "max"}' }), [clip(1)], { enabled: false });
    renderPage();
    const sheet = await openRestyle();
    expect(within(sheet).getByRole("combobox", { name: "Edit intensity" })).toHaveTextContent("Max");
  });

  it("carries the task's edits into a new generation from the failure screen", async () => {
    serveFx(task({
      status: "error", workflow: "topic", error_code: "topic_not_found", caption_template: "slam",
      edit_style: { intensity: "bold", sfx: false }, workflow_options: { topic: "crypto" },
      workflow_plan: { version: "wf-v1", workflow: "topic", error: { code: "topic_not_found", details: { suggested_topics: ["pricing"] } } },
    }), []);
    renderPage();
    await waitFor(() => expect(templateUrls).toContain("/api/caption-templates?include=fx"));
    fireEvent.click(await screen.findByRole("button", { name: "pricing" }));
    await waitFor(() => expect(created).not.toBeNull());
    expect(created).toMatchObject({ caption_template: "slam", edit_style: { intensity: "bold", sfx: false } });
  });
});
