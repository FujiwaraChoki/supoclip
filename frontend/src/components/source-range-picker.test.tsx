import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { SourceRangePicker } from "./source-range-picker";
import type { SourceRange } from "@/lib/source-range";

beforeEach(() => vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => vi.unstubAllGlobals());

const EIGHT_HOURS = 8 * 3600;

function Harness({ initial = { start: 0, end: 7200 }, onChange }: { initial?: SourceRange; onChange?: (range: SourceRange) => void }) {
  const [range, setRange] = useState(initial);
  return (
    <SourceRangePicker
      durationSeconds={EIGHT_HOURS}
      maxSeconds={7200}
      minSeconds={60}
      value={range}
      onChange={(next) => { setRange(next); onChange?.(next); }}
    />
  );
}

it("starts on the first part and says how many minutes it uses", () => {
  render(<Harness />);
  expect(screen.getByRole("textbox", { name: "Start" })).toHaveValue("0:00");
  expect(screen.getByRole("textbox", { name: "End" })).toHaveValue("2:00:00");
  expect(screen.getByText("Clipping 2:00:00. Uses 120 minutes.")).toBeInTheDocument();
});

it("moves a thumb with the keyboard, a minute per press on a long stream", () => {
  const onChange = vi.fn();
  render(<Harness initial={{ start: 600, end: 3600 }} onChange={onChange} />);
  const start = screen.getByRole("slider", { name: "Start" });
  start.focus();
  fireEvent.keyDown(start, { key: "ArrowRight" });
  expect(onChange).toHaveBeenLastCalledWith({ start: 660, end: 3600 });
  expect(start).toHaveAttribute("aria-valuetext", "11:00");
});

it("pulls the other end along instead of going over the limit", () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  const end = screen.getByRole("slider", { name: "End" });
  end.focus();
  fireEvent.keyDown(end, { key: "ArrowRight" });
  expect(onChange).toHaveBeenLastCalledWith({ start: 60, end: 7260 });
});

it("takes typed times and explains a part that is too long", () => {
  render(<Harness />);
  const end = screen.getByRole("textbox", { name: "End" });
  fireEvent.change(end, { target: { value: "3:00:00" } });
  fireEvent.keyDown(end, { key: "Enter" });
  expect(screen.getByRole("alert")).toHaveTextContent("Pick 120 minutes or less.");

  const start = screen.getByRole("textbox", { name: "Start" });
  fireEvent.change(start, { target: { value: "1:00:00" } });
  fireEvent.blur(start);
  expect(screen.getByText("Clipping 2:00:00. Uses 120 minutes.")).toBeInTheDocument();
});

it("says how to write a time it can't read", () => {
  render(<Harness />);
  const start = screen.getByRole("textbox", { name: "Start" });
  fireEvent.change(start, { target: { value: "soon" } });
  fireEvent.blur(start);
  expect(start).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Use h:mm:ss, like 1:30:00")).toBeInTheDocument();
});

it("jumps to the last part of the video", () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "Last 120 min" }));
  expect(onChange).toHaveBeenLastCalledWith({ start: EIGHT_HOURS - 7200, end: EIGHT_HOURS });
});
