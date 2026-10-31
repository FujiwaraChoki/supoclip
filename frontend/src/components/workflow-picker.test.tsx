import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { WORKFLOWS, type Workflow, type WorkflowsInfo } from "@/lib/workflows";
import { availableWorkflows, WorkflowGlyph, WorkflowPicker } from "./workflow-picker";

const ALL: ReadonlySet<Workflow> = new Set(WORKFLOWS);

function Controlled({ initial = "highlights", available = ALL, disabled = false, onChange = () => {} }: {
  initial?: Workflow; available?: ReadonlySet<Workflow>; disabled?: boolean; onChange?: (workflow: Workflow) => void;
}) {
  const [value, setValue] = useState<Workflow>(initial);
  return (
    <>
      <p id="label">What do you want to make?</p>
      <WorkflowPicker value={value} onChange={(next) => { setValue(next); onChange(next); }} available={available} disabled={disabled} labelId="label" />
    </>
  );
}

describe("WorkflowPicker", () => {
  it("is one labelled radio group with a card per workflow", () => {
    render(<Controlled initial="series" />);
    const group = screen.getByRole("radiogroup", { name: "What do you want to make?" });
    const radios = screen.getAllByRole("radio");
    expect(group).toContainElement(radios[0]);
    const cards = [
      ["Highlights", "The best standalone moments"],
      ["Series", "Consecutive parts that tell the whole story"],
      ["Topic", "Every strong moment about one subject"],
      ["Recap", "One short video covering the main points"],
      ["Trailer", "One short preview that makes people watch the full video"],
    ];
    expect(radios).toHaveLength(cards.length);
    cards.forEach(([label, hint], index) => {
      expect(within(radios[index]).getByText(label)).toBeInTheDocument();
      expect(within(radios[index]).getByText(hint)).toBeInTheDocument();
    });
    expect(screen.getByRole("radio", { name: /Series/ })).toHaveAttribute("aria-checked", "true");
    expect(radios.filter((radio) => radio.getAttribute("aria-checked") === "true")).toHaveLength(1);
  });

  it("keeps one tab stop on the selected card and selects with a click", () => {
    const onChange = vi.fn();
    render(<Controlled initial="topic" onChange={onChange} />);
    expect(screen.getAllByRole("radio").map((radio) => radio.tabIndex)).toEqual([-1, -1, 0, -1, -1]);
    fireEvent.click(screen.getByRole("radio", { name: /Recap/ }));
    expect(onChange).toHaveBeenLastCalledWith("recap");
    expect(screen.getByRole("radio", { name: /Recap/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /Recap/ }).tabIndex).toBe(0);
  });

  it("moves the selection and focus with the arrow keys, Home and End, wrapping at the ends", () => {
    render(<Controlled initial="highlights" />);
    const radio = (name: RegExp) => screen.getByRole("radio", { name });
    fireEvent.keyDown(radio(/Highlights/), { key: "ArrowRight" });
    expect(radio(/Series/)).toHaveAttribute("aria-checked", "true");
    expect(radio(/Series/)).toHaveFocus();
    fireEvent.keyDown(radio(/Series/), { key: "ArrowDown" });
    expect(radio(/Topic/)).toHaveFocus();
    fireEvent.keyDown(radio(/Topic/), { key: "ArrowUp" });
    fireEvent.keyDown(radio(/Series/), { key: "ArrowLeft" });
    expect(radio(/Highlights/)).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(radio(/Highlights/), { key: "ArrowLeft" });
    expect(radio(/Trailer/)).toHaveAttribute("aria-checked", "true");
    expect(radio(/Trailer/)).toHaveFocus();
    fireEvent.keyDown(radio(/Trailer/), { key: "Home" });
    expect(radio(/Highlights/)).toHaveFocus();
    fireEvent.keyDown(radio(/Highlights/), { key: "End" });
    expect(radio(/Trailer/)).toHaveAttribute("aria-checked", "true");
  });

  it("skips workflows that are not available", () => {
    render(<Controlled initial="highlights" available={new Set<Workflow>(["highlights", "recap"])} />);
    expect(screen.getByRole("radio", { name: /Series/ })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("radio", { name: /Highlights/ }), { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: /Recap/ })).toHaveAttribute("aria-checked", "true");
  });

  it("disables every card while a generation starts", () => {
    const onChange = vi.fn();
    render(<Controlled disabled onChange={onChange} />);
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Trailer/ }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("draws decorative glyphs with at most one brand accent", () => {
    const { container } = render(<>{WORKFLOWS.map((id) => <WorkflowGlyph key={id} id={id} />)}</>);
    for (const glyph of container.querySelectorAll(":scope > span")) expect(glyph).toHaveAttribute("aria-hidden");
    expect(container.querySelectorAll(".bg-brand")).toHaveLength(1);
  });
});

describe("availableWorkflows", () => {
  const info = (enabled: unknown, workflows: WorkflowsInfo["workflows"]) => ({ enabled, workflows, topic_max_chars: 120, instructions_max_chars: 500 }) as WorkflowsInfo;

  it("offers nothing unless the server explicitly enables workflows", () => {
    const rows = WORKFLOWS.map((id) => ({ id, available: true, min_source_seconds: 0 }));
    expect(availableWorkflows(null).size).toBe(0);
    expect(availableWorkflows(info(false, rows)).size).toBe(0);
    expect(availableWorkflows(info("true", rows)).size).toBe(0);
    expect([...availableWorkflows(info(true, rows))]).toEqual([...WORKFLOWS]);
  });

  it("keeps only known, available workflows", () => {
    const rows = [
      { id: "highlights", available: true, min_source_seconds: 0 },
      { id: "series", available: false, min_source_seconds: 120 },
      { id: "montage", available: true, min_source_seconds: 0 },
    ] as WorkflowsInfo["workflows"];
    expect([...availableWorkflows(info(true, rows))]).toEqual(["highlights"]);
  });
});
