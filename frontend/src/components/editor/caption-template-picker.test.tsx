import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { animatedTemplates, CaptionTemplatePicker, classicTemplates } from "./caption-template-picker";

// The live tiles draw with fx-core on a canvas; the picker's job is which ids it offers.
vi.mock("@/components/caption-style-tiles", () => ({
  CaptionStyleTiles: ({ options, value, onChange }: { options: { id: string }[]; value: string; onChange: (id: string) => void }) => (
    <div role="radiogroup" aria-label="Animated">
      {options.map((option) => (
        <button key={option.id} role="radio" aria-checked={option.id === value} aria-label={option.id} onClick={() => onChange(option.id)} />
      ))}
    </div>
  ),
}));

it("offers animated templates only when the edit-styles answer does", () => {
  const { rerender } = render(<CaptionTemplatePicker value="hormozi" offered={[]} onChange={vi.fn()} />);
  expect(screen.getAllByRole("radiogroup")).toHaveLength(1);
  expect(screen.getByRole("radio", { name: "Hormozi" })).toHaveAttribute("aria-checked", "true");
  expect(screen.queryByRole("radio", { name: "slam" })).toBeNull();
  rerender(<CaptionTemplatePicker value="hormozi" offered={["slam", "emoji_drop"]} onChange={vi.fn()} />);
  expect(screen.getByRole("radio", { name: "slam" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "emoji_drop" })).toBeInTheDocument();
});

it("keeps a draft's own animated template even when it is no longer offered", () => {
  expect(animatedTemplates([], "behind_head")).toEqual(["behind_head"]);
  expect(animatedTemplates(["slam"], "default")).toEqual(["slam"]);
  render(<CaptionTemplatePicker value="behind_head" offered={[]} onChange={vi.fn()} />);
  expect(screen.getByRole("radio", { name: "behind_head" })).toHaveAttribute("aria-checked", "true");
});

it("treats a missing template as Default and reports picks", () => {
  const onChange = vi.fn();
  render(<CaptionTemplatePicker value={undefined} offered={["slam"]} onChange={onChange} />);
  expect(screen.getByRole("radio", { name: "Default" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(screen.getByRole("radio", { name: "Default" }));
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("radio", { name: "Neon" }));
  fireEvent.click(screen.getByRole("radio", { name: "slam" }));
  expect(onChange.mock.calls).toEqual([["neon"], ["slam"]]);
});

it("lists the session's classic looks and never an animated one among them", () => {
  const look = { animation: "karaoke", wordPop: true, wordBox: false, wordBoxColor: null, highlightColor: "#ff0", emphasisColor: null, strokeColor: null, strokeWidth: 0, shadow: false, glow: false, backgroundColor: null } as const;
  expect(classicTemplates({ podcast: look, default: look, slam: { ...look, fx: true, fxStyle: "slam" } })).toEqual(["default", "podcast"]);
  expect(classicTemplates(undefined)).toHaveLength(7);
});
