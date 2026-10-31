import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ClipPackageButton, type ClipPackage } from "./clip-package";

const copyText = vi.fn();
vi.mock("@/lib/copy-text", () => ({ useCopyText: () => copyText }));

const pkg: ClipPackage = {
  clip_status: "RESCUE_REQUIRED",
  scoring: { raw_virality_score: 78, projected_score_after_rescue: 91, primary_flaw_fixed: "Slow open trimmed" },
  surgical_edits: {
    new_start: "00:02.500",
    new_end: "00:28.000",
    cold_open_tease_timestamp: "00:12.000 - 00:14.000",
    internal_cuts: [{ cut_start: "00:10.000", cut_end: "00:11.500", reason: "dead air" }],
    total_seconds_shaved: 4,
    first_spoken_words_after_trim: "Here it comes",
    last_spoken_words_after_trim: "And that was it",
  },
  on_screen_text_hooks: { curiosity_gap: "Hook one", polarizing_debate: "Hook two", high_stakes_context: "Hook three" },
  seo_packaging: {
    titles: { search_curiosity_hybrid: "Title A", shock_browse_fyp: "Title B", high_stakes_value: "Title C" },
    description: { engagement_hook_line: "Hook line?", seo_keyword_block: "Keywords.", full_copy_paste_caption: "Full caption" },
    hashtags: { entity_specific: ["#foo"], niche_category: ["#bar"], platform_broad: ["#shorts", "#fyp"] },
    pinned_comment_bait: "Spot the detail?",
    target_search_keywords: ["alpha", "beta"],
  },
};

const clip = { id: "clip-1", clip_order: 1, package: null as ClipPackage | null };
const URL = "/api/tasks/task-1/clips/clip-1/package";

function mockFetch(response: Response) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

function open() {
  fireEvent.click(screen.getByRole("button", { name: /rescue & package/i }));
}

beforeEach(() => {
  vi.resetAllMocks();
  mockFetch(new Response(JSON.stringify({ package: pkg })));
});

it("renders a stored package without fetching", async () => {
  render(<ClipPackageButton taskId="task-1" clip={{ ...clip, package: pkg }} />);
  open();
  expect(await screen.findByTestId("package-status")).toHaveTextContent("Rescue required");
  expect(screen.getByTestId("package-score")).toHaveTextContent("Score 78 → 91");
  expect(screen.getByText("Slow open trimmed")).toBeInTheDocument();
  expect(screen.getByTestId("package-cuts")).toHaveTextContent("00:10.000 - 00:11.500 · dead air");
  expect(screen.getByTestId("package-shaved")).toHaveTextContent("4s shaved");
  expect(screen.getByText("Title B")).toBeInTheDocument();
  expect(screen.getByTestId("package-hashtags")).toHaveTextContent("#foo #bar #shorts #fyp");
  expect(fetch).not.toHaveBeenCalled();
});

it("generates with regenerate false", async () => {
  const onChanged = vi.fn();
  render(<ClipPackageButton taskId="task-1" clip={clip} onChanged={onChanged} />);
  open();
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  expect(await screen.findByText("Hook one")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(URL, expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ regenerate: false }),
  }));
  expect(onChanged).toHaveBeenCalled();
});

it("regenerate sends regenerate true", async () => {
  render(<ClipPackageButton taskId="task-1" clip={{ ...clip, package: pkg }} />);
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(URL, expect.objectContaining({
    body: JSON.stringify({ regenerate: true }),
  })));
});

it("shows the detail message on a 503", async () => {
  mockFetch(new Response(JSON.stringify({ detail: "AI is busy" }), { status: 503 }));
  render(<ClipPackageButton taskId="task-1" clip={clip} />);
  open();
  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  expect(await screen.findByText("AI is busy")).toBeInTheDocument();
});

it("copies a field", async () => {
  render(<ClipPackageButton taskId="task-1" clip={{ ...clip, package: pkg }} />);
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Copy Search + curiosity" }));
  expect(copyText).toHaveBeenCalledWith("Title A");
});

it("rejected clip shows red badge and no packaging copy", async () => {
  const rejected: ClipPackage = {
    ...pkg,
    clip_status: "REJECTED",
    on_screen_text_hooks: { curiosity_gap: "", polarizing_debate: "", high_stakes_context: "" },
    seo_packaging: {
      titles: { search_curiosity_hybrid: "", shock_browse_fyp: "", high_stakes_value: "" },
      description: { engagement_hook_line: "", seo_keyword_block: "", full_copy_paste_caption: "" },
      hashtags: { entity_specific: [], niche_category: [], platform_broad: [] },
      pinned_comment_bait: "",
      target_search_keywords: [],
    },
  };
  render(<ClipPackageButton taskId="task-1" clip={{ ...clip, package: rejected }} />);
  open();
  expect(await screen.findByTestId("package-status")).toHaveClass("bg-red-100");
  expect(screen.getByText(/scored too low/i)).toBeInTheDocument();
  expect(screen.queryByTestId("package-title-shock")).toBeNull();
});

it("links to the studio with the apply flag when trims are usable", async () => {
  render(<ClipPackageButton taskId="task-1" clip={{ ...clip, package: pkg, duration: 30 }} />);
  open();
  const link = await screen.findByRole("link", { name: /apply in studio/i });
  expect(link).toHaveAttribute("href", "/tasks/task-1/edit?clip=clip-1&apply=rescue");
});

it("hides the studio link for a rejected package with no edits", async () => {
  const rejected = { ...pkg, clip_status: "REJECTED" as const, surgical_edits: { ...pkg.surgical_edits, internal_cuts: [], total_seconds_shaved: 0 } };
  render(<ClipPackageButton taskId="task-1" clip={{ ...clip, package: rejected, duration: 30 }} />);
  open();
  await screen.findByTestId("package-status");
  expect(screen.queryByRole("link", { name: /apply in studio/i })).toBeNull();
});
