import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import type { EditDocument, EditorState } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Timeline } from "./timeline";

function renderTimeline(doc: EditDocument) {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const asset = { status: "ready", basis: "clip", duration: 30, width: 1080, height: 1920, fps: 25, hasAudio: false,
    waveform: [], draft: { revision: 1, document: doc }, original: doc, jobs: [] } as EditorState;
  let current = doc;
  const update = vi.fn((change: (doc: EditDocument) => EditDocument) => { current = change(current); });
  render(
    <TooltipProvider>
    <Timeline doc={doc} asset={asset} endpoint="/editor" currentTime={doc.segments[0].start} onTextDraft={() => {}}
      index={0} select={() => {}} pause={() => {}} seek={() => {}} update={update} brollId={null} selectBroll={() => {}} />
    </TooltipProvider>,
  );
  return () => current;
}

it("keeps a segment's start at or after zero when its out point is cleared", () => {
  const latest = renderTimeline({ ...draft, segments: [{ id: "s", start: 12.5, end: 20 }] });
  fireEvent.change(screen.getByLabelText(/out point/i), { target: { value: "" } });
  expect(latest().segments[0]).toEqual({ id: "s", start: 12.5, end: 20 });
  fireEvent.change(screen.getByLabelText(/out point/i), { target: { value: "0" } });
  expect(latest().segments[0].start).toBe(12.5);
  expect(latest().segments[0].end).toBeCloseTo(12.54);
});
