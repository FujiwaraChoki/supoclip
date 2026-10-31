import { fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { expect, it, vi } from "vitest";
import { RescueTrims } from "./rescue-trims";
import { draft } from "@/lib/editor/document.test-fixture";
import type { EditDocument } from "@/lib/editor/document";

const original = { ...draft, segments: [{ id: "s", start: 0, end: 30 }] };
const pkg = {
  clip_status: "RESCUE_REQUIRED",
  surgical_edits: {
    new_start: "00:02.500", new_end: "00:28.000", cold_open_tease_timestamp: "00:12.000 - 00:14.000",
    internal_cuts: [{ cut_start: "00:10.000", cut_end: "00:11.500" }], total_seconds_shaved: 3.4,
  },
};
const setup = (extra = {}) => {
  let current: EditDocument = original;
  const update = vi.fn((change: (d: EditDocument) => EditDocument) => { current = change(current); });
  const view = render(<RescueTrims pkg={pkg} doc={original} original={original} update={update} {...extra} />);
  return { update, current: () => current, ...view };
};

it("shows the shave subtitle and cold-open note, and applies one change on click", () => {
  const { update, current } = setup();
  expect(screen.getByText(/3\.4/)).toBeTruthy();
  expect(screen.getByText(/cold-open tease not applied/i)).toBeTruthy();
  fireEvent.click(screen.getByTestId("apply-rescue"));
  expect(update).toHaveBeenCalledTimes(1);
  expect(current().segments.map((s) => [s.start, s.end])).toEqual([[2.5, 10], [11.5, 28]]);
});

it("auto-applies once under StrictMode and clears the flag", () => {
  const update = vi.fn();
  const done = vi.fn();
  const { rerender } = render(
    <StrictMode><RescueTrims pkg={pkg} doc={original} original={original} update={update} autoApply onAutoApplied={done} /></StrictMode>,
  );
  rerender(<StrictMode><RescueTrims pkg={pkg} doc={original} original={original} update={update} autoApply onAutoApplied={done} /></StrictMode>);
  expect(update).toHaveBeenCalledTimes(1);
  expect(done).toHaveBeenCalledTimes(1);
});

it("renders nothing for a rejected package without edits", () => {
  const rejected = { clip_status: "REJECTED", surgical_edits: { new_start: "0:00", new_end: "0:30", internal_cuts: [], total_seconds_shaved: 0 } };
  const { container } = render(<RescueTrims pkg={rejected} doc={original} original={original} update={vi.fn()} />);
  expect(container).toBeEmptyDOMElement();
});
