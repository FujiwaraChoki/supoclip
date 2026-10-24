import { render, screen } from "@testing-library/react";
import { beforeAll, expect, it, vi } from "vitest";
import { Inspector } from "./inspector";
import { draft } from "@/lib/editor/document.test-fixture";
import type { EditorState } from "@/lib/editor/document";

beforeAll(() => {
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserver);
});

function asset(captionTiming?: EditorState["captionTiming"]): EditorState {
  return {
    status: "ready",
    basis: "clip.mp4",
    duration: 4,
    width: 1080,
    height: 1920,
    fps: 30,
    hasAudio: true,
    waveform: [],
    draft: { revision: 0, document: draft },
    original: draft,
    jobs: [],
    captionTiming,
  };
}

function renderCaptions(captionTiming?: EditorState["captionTiming"]) {
  render(
    <Inspector
      doc={draft}
      asset={asset(captionTiming)}
      tab="captions"
      setTab={() => {}}
      time={0}
      seek={() => {}}
      update={() => {}}
      applyAll={async () => {}}
    />,
  );
}

it("warns when caption timings are estimated", () => {
  renderCaptions("estimated");
  expect(
    screen.getByText(/spaced evenly across the clip/i),
  ).toBeInTheDocument();
});

it("hides the timing warning when captions follow speech", () => {
  renderCaptions("speech");
  expect(screen.queryByText(/spaced evenly across the clip/i)).toBeNull();
});
