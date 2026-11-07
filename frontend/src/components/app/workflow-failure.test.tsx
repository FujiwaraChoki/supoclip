import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildRecreateBody, failureAction, isWorkflowErrorCode, WorkflowFailure } from "./workflow-failure";
import type { WorkflowErrorCode, WorkflowPlan } from "@/lib/workflows";

function renderFailure(code: WorkflowErrorCode, props: Partial<Parameters<typeof WorkflowFailure>[0]> = {}) {
  const handlers = { onCreate: vi.fn(), onResume: vi.fn() };
  render(<WorkflowFailure code={code} workflow="series" canCreate readOnly={false} pendingAction={null} {...handlers} {...props} />);
  return handlers;
}

const notFoundPlan: WorkflowPlan = {
  version: "wf-v1",
  workflow: "topic",
  topic: { query: "crypto", canonical: "Crypto", coverage: "thin", found: 0 },
  error: { code: "topic_not_found", details: { suggested_topics: ["pricing", "Crypto", "!!", "hiring"], mentions: ["12:30", "41:02"] } },
};

describe("WorkflowFailure", () => {
  it("offers the topics the video is really about", () => {
    const { onCreate } = renderFailure("topic_not_found", { plan: notFoundPlan, topic: "crypto" });
    expect(screen.getByRole("heading", { name: "“crypto” barely comes up in this video" })).toBeInTheDocument();
    expect(screen.getByText(/Your minutes were refunded/)).toBeInTheDocument();
    expect(screen.getByText("Mentioned in passing at 12:30, 41:02.")).toBeInTheDocument();
    expect(screen.getByText("This video talks most about:")).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Crypto" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "!!" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "hiring" }));
    expect(onCreate).toHaveBeenLastCalledWith("topic", "hiring");
    fireEvent.click(screen.getByRole("button", { name: "Make highlights instead" }));
    expect(onCreate).toHaveBeenLastCalledWith("highlights");
  });

  it("offers each suggestion once, cleaned up the way the API stores it", () => {
    const plan: WorkflowPlan = {
      ...notFoundPlan,
      error: { code: "topic_not_found", details: { suggested_topics: ["  Pricing\u200b tiers ", "pricing tiers", "hiring"] } },
    };
    const { onCreate } = renderFailure("topic_not_found", { plan, topic: "crypto" });
    expect(screen.getAllByRole("button", { name: /^pricing tiers$/i })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Pricing tiers" }));
    expect(onCreate).toHaveBeenLastCalledWith("topic", "Pricing tiers");
  });

  it("names the searched topic from the error when the task lost it", () => {
    const plan: WorkflowPlan = {
      version: "wf-v1",
      workflow: "topic",
      error: { code: "topic_not_found", details: { topic: "crypto", suggested_topics: ["Crypto", "pricing"] } },
    };
    renderFailure("topic_not_found", { plan });
    expect(screen.getByRole("heading", { name: "“crypto” barely comes up in this video" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crypto" })).not.toBeInTheDocument();
  });

  it("suggests a recap when a series finds no story", () => {
    const { onCreate } = renderFailure("no_story", { sourceSeconds: 900 });
    fireEvent.click(screen.getByRole("button", { name: "Make a recap instead" }));
    expect(onCreate).toHaveBeenCalledWith("recap");
  });

  it("suggests highlights when the source is too short for a recap", () => {
    const { onCreate } = renderFailure("no_story", { sourceSeconds: 150 });
    expect(screen.queryByRole("button", { name: "Make a recap instead" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make highlights instead" }));
    expect(onCreate).toHaveBeenCalledWith("highlights");
  });

  it("retries the failures a retry can fix", () => {
    const { onResume, onCreate } = renderFailure("plan_failed");
    expect(screen.getByRole("heading", { name: "The AI editor couldn't finish this plan" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onResume).toHaveBeenCalled();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("sends untimed transcripts back home", () => {
    renderFailure("transcript_untimed");
    expect(screen.getByRole("link", { name: "Back to home" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("offers no new generation for read-only viewers or sources it can't reuse", () => {
    renderFailure("topic_not_found", { plan: notFoundPlan, readOnly: true });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText("This video talks most about:")).not.toBeInTheDocument();
  });

  it("shows the pending action", () => {
    renderFailure("topic_not_found", { plan: notFoundPlan, pendingAction: "create:topic:pricing" });
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
    expect(screen.getByRole("button", { name: "pricing" }).querySelector(".animate-spin")).not.toBeNull();
    expect(screen.getByRole("button", { name: "hiring" }).querySelector(".animate-spin")).toBeNull();
  });

  it.each([
    ["series", "This video is too short for a series", /two parts/],
    ["topic", "This video is too short to search by topic", /over a minute/],
    ["recap", "This video is too short for a recap", /Recaps work on videos over 3 minutes/],
    ["trailer", "This video is too short for a trailer", /Trailers work on videos over 3 minutes/],
  ] as const)("explains a source too short for a %s in its own words", (workflow, title, body) => {
    const { onCreate } = renderFailure("source_too_short", { workflow, sourceSeconds: 175 });
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getByText(body)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make highlights instead" }));
    expect(onCreate).toHaveBeenLastCalledWith("highlights");
  });

  it("draws its icon with theme colours that follow dark mode", () => {
    renderFailure("plan_failed");
    const tile = screen.getByRole("heading").previousElementSibling;
    expect(tile).toHaveClass("bg-destructive/10", "text-destructive");
    expect(tile?.className).not.toMatch(/\b(?:bg|text)-red-\d/);
  });
});

describe("WorkflowFailure for a user with only some workflows", () => {
  it("offers highlights instead of a recap they can't make", () => {
    const { onCreate } = renderFailure("trailer_unbuildable", { workflow: "trailer", offered: new Set(["highlights", "trailer"]) });
    fireEvent.click(screen.getByRole("button", { name: "Make highlights instead" }));
    expect(onCreate).toHaveBeenCalledWith("highlights");
  });

  it("keeps the recap when they can make it", () => {
    const { onCreate } = renderFailure("trailer_unbuildable", { workflow: "trailer", offered: new Set(["recap", "highlights"]) });
    expect(screen.queryByRole("button", { name: "Make highlights instead" })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button")[0]);
    expect(onCreate).toHaveBeenCalledWith("recap");
  });

  it("hides topic suggestions they can't make", () => {
    renderFailure("topic_not_found", { plan: notFoundPlan, topic: "crypto", offered: new Set(["highlights", "trailer"]) });
    expect(screen.queryByRole("button", { name: "pricing" })).not.toBeInTheDocument();
  });

  it("drops instructions and settings the next workflow can't take", () => {
    const task = { source_url: "https://youtu.be/x", workflow: "topic" as const, workflow_options: { topic: "x", instructions: "Funny bits", advanced: { hook_title: false } } };
    const body = buildRecreateBody(task, { workflow: "highlights" }, false);
    expect(body).not.toHaveProperty("instructions");
    expect(body).not.toHaveProperty("workflow_options");
    expect(buildRecreateBody(task, { workflow: "series" }).instructions).toBe("Funny bits");
  });
});

describe("failure helpers", () => {
  it("knows the worker codes", () => {
    expect(isWorkflowErrorCode("topic_not_found")).toBe(true);
    expect(isWorkflowErrorCode("VIDEO_TOO_LONG")).toBe(false);
    expect(isWorkflowErrorCode(null)).toBe(false);
  });

  it("maps each code to its next step", () => {
    expect(failureAction("source_too_short")).toEqual({ kind: "create", workflow: "highlights" });
    expect(failureAction("trailer_unbuildable", 600)).toEqual({ kind: "create", workflow: "recap" });
    expect(failureAction("render_incomplete")).toEqual({ kind: "resume" });
    expect(failureAction("transcript_untimed")).toEqual({ kind: "home" });
  });

  it("rebuilds the create body with the task's look and the new workflow", () => {
    const task = {
      source_url: "upload://abc.mp4",
      source_title: "Episode 12.mp4",
      source_type: "upload",
      font_family: "Inter",
      font_size: null,
      font_color: "#FFFFFF",
      caption_template: "bold",
      processing_mode: "fast",
      output_format: "vertical",
      clip_length: "short",
      add_subtitles: true,
      include_broll: false,
      cut_long_pauses: true,
      pause_threshold_ms: 900,
      remove_filler_words: false,
      filtered_words: ["um"],
      workflow_options: { topic: "crypto", instructions: "Skip the sponsor read" },
    };
    expect(buildRecreateBody(task, { workflow: "topic", topic: "pricing" })).toEqual({
      source: { url: "upload://abc.mp4", title: "Episode 12.mp4" },
      font_options: { font_family: "Inter", font_size: null, font_color: "#FFFFFF" },
      caption_template: "bold",
      processing_mode: "fast",
      output_format: "vertical",
      clip_length: "short",
      add_subtitles: true,
      include_broll: false,
      cut_long_pauses: true,
      pause_threshold_ms: 900,
      remove_filler_words: false,
      filtered_words: ["um"],
      workflow: "topic",
      workflow_options: { topic: "pricing" },
      instructions: "Skip the sponsor read",
    });
    const highlights = buildRecreateBody({ ...task, source_type: "youtube", workflow_options: {} }, { workflow: "highlights" });
    expect(highlights).not.toHaveProperty("workflow");
    expect(highlights).not.toHaveProperty("workflow_options");
    expect(highlights).not.toHaveProperty("instructions");
    expect(highlights.source.title).toBeNull();
  });

  it("keeps a task's advanced settings when they still mean the same thing", () => {
    const task = {
      source_url: "https://youtu.be/x", workflow: "series" as const,
      workflow_options: { advanced: { cold_open: true, hook_title: false } },
    };
    expect(buildRecreateBody(task, { workflow: "series" }).workflow_options).toEqual({ advanced: { cold_open: true, hook_title: false } });
    // Another workflow has no cold open, and highlights have no burned titles to turn off.
    expect(buildRecreateBody(task, { workflow: "recap" }).workflow_options).toEqual({ advanced: { hook_title: false } });
    expect(buildRecreateBody(task, { workflow: "highlights" })).not.toHaveProperty("workflow_options");
    const topic = { ...task, workflow: "topic" as const, workflow_options: { topic: "a", advanced: { match: "strict" } } };
    expect(buildRecreateBody(topic, { workflow: "topic", topic: "b" }).workflow_options).toEqual({ topic: "b", advanced: { match: "strict" } });
  });

  it("keeps the length only when it measures the same thing", () => {
    const task = { source_url: "https://youtu.be/abc", clip_length: "short" };
    // A short recap (about 45 s in total) is not a request for short clips.
    expect(buildRecreateBody({ ...task, workflow: "recap" }, { workflow: "highlights" })).not.toHaveProperty("clip_length");
    expect(buildRecreateBody({ ...task, workflow: "series" }, { workflow: "recap" })).not.toHaveProperty("clip_length");
    expect(buildRecreateBody({ ...task, workflow: "recap" }, { workflow: "trailer" })).toHaveProperty("clip_length", "short");
    expect(buildRecreateBody({ ...task, workflow: "topic" }, { workflow: "highlights" })).toHaveProperty("clip_length", "short");
  });
});
