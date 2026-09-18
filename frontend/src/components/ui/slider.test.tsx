import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Slider } from "./slider";

afterEach(() => vi.unstubAllGlobals());

it("exposes both range endpoints and lets the keyboard adjust the end", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const onChange = vi.fn();
  render(<Slider aria-label="Trim range" min={0} max={10} step={1} defaultValue={[2, 8]} onValueChange={onChange} />);
  expect(screen.getByRole("slider", { name: "Trim range start" })).toHaveAttribute("aria-valuenow", "2");
  const end = screen.getByRole("slider", { name: "Trim range end" });
  end.focus();
  fireEvent.keyDown(end, { key: "ArrowRight" });
  expect(onChange).toHaveBeenLastCalledWith([2, 9]);
});
