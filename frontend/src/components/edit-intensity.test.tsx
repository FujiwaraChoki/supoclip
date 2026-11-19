import { fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_EDIT_STYLE, type EditStyle } from "@/lib/fx/core/types";
import { EDIT_STYLES_UNAVAILABLE, MOODS, parseEditStylesInfo } from "@/lib/fx/edit-style-request";
import { EditIntensity, useEditStylesInfo } from "./edit-intensity";

const INFO = parseEditStylesInfo({
  enabled: true, intensities: ["off", "subtle", "bold", "max"], music_moods: ["hype", "dark"], grades: [],
  templates: [], reactions_available: false,
});

function Harness({ initial = DEFAULT_EDIT_STYLE, info = INFO, onChange }: { initial?: EditStyle; info?: typeof INFO; onChange?: (style: EditStyle) => void }) {
  const [style, setStyle] = useState(initial);
  return <EditIntensity value={style} info={info} onChange={(next) => { setStyle(next); onChange?.(next); }} />;
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("EditIntensity", () => {
  it("is four radio cards the tour can point at, off by default with nothing else to set", () => {
    render(<Harness />);
    const group = screen.getByRole("radiogroup", { name: "Edit intensity" });
    expect(group).toHaveAttribute("data-tour", "edit-intensity");
    expect(within(group).getAllByRole("radio").map((radio) => radio.textContent)).toEqual([
      expect.stringContaining("Off"), expect.stringContaining("Subtle"), expect.stringContaining("Bold"), expect.stringContaining("Max"),
    ]);
    expect(within(group).getByRole("radio", { name: /Off/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.getByText(/Turn edits on/)).toBeInTheDocument();
  });

  it("shows what edits use once they're on, and changes one thing at a time", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /Bold/ }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_EDIT_STYLE, intensity: "bold" });
    // The switches wait behind "Fine-tune", which the tour points at.
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    const fineTune = screen.getByRole("button", { name: "Fine-tune" });
    expect(fineTune.parentElement).toHaveAttribute("data-tour", "edit-options");
    fireEvent.click(fineTune);
    for (const name of ["Sound effects", "Music", "Zooms", "Transitions", "Progress bar", "Loop ending"]) {
      expect(screen.getByRole("switch", { name })).toBeInTheDocument();
    }
    // GIF reactions only when the server has a provider.
    expect(screen.queryByRole("switch", { name: "GIF reactions" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Progress bar" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_EDIT_STYLE, intensity: "bold", progress_bar: true });
    fireEvent.click(screen.getByRole("switch", { name: "Music" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_EDIT_STYLE, intensity: "bold", progress_bar: true, music: false });
    // Closed again, the row still says two things are changed.
    fireEvent.click(screen.getByRole("button", { name: /Fine-tune/ }));
    expect(screen.getByRole("button", { name: /Fine-tune/ })).toHaveTextContent("2");
    fireEvent.click(screen.getByRole("button", { name: /Fine-tune/ }));
    // The mood picker belongs to music.
    expect(screen.queryByRole("combobox", { name: "Music mood" })).not.toBeInTheDocument();
  });

  it("offers GIF reactions when the server can serve them", () => {
    render(<Harness initial={{ ...DEFAULT_EDIT_STYLE, intensity: "subtle" }} info={{ ...INFO, reactions_available: true }} />);
    fireEvent.click(screen.getByRole("button", { name: /Fine-tune/ }));
    expect(screen.getByRole("switch", { name: "GIF reactions" })).not.toBeChecked();
    expect(screen.getByRole("combobox", { name: "Music mood" })).toHaveTextContent("Match the video");
    expect(screen.getByRole("combobox", { name: "Swearing" })).toHaveTextContent("Bleep and cover mouth");
  });

  it("offers the aggressive mood and shows it when picked", () => {
    const full = parseEditStylesInfo({ ...INFO, music_moods: [...MOODS] });
    expect(full.music_moods).toContain("aggressive");
    render(<Harness initial={{ ...DEFAULT_EDIT_STYLE, intensity: "bold", music_mood: "aggressive" }} info={full} />);
    fireEvent.click(screen.getByRole("button", { name: /Fine-tune/ }));
    expect(screen.getByRole("combobox", { name: "Music mood" })).toHaveTextContent("Aggressive");
  });

  it("moves between intensities with the arrow keys", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const off = screen.getByRole("radio", { name: /Off/ });
    expect(off).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: /Subtle/ })).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(off, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_EDIT_STYLE, intensity: "subtle" });
    expect(screen.getByRole("radio", { name: /Subtle/ })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("radio", { name: /Subtle/ }), { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("radio", { name: /Off/ }), { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_EDIT_STYLE, intensity: "max" });
  });

  it("moves its glyphs only for intensities that have motion", () => {
    render(<Harness />);
    const meters = [...document.querySelectorAll(".fx-meter")];
    expect(meters.map((meter) => meter.getAttribute("data-animated"))).toEqual(["false", "true", "true", "true"]);
    expect(meters.map((meter) => meter.getAttribute("data-jitter"))).toEqual(["false", "false", "false", "true"]);
    // The animation CSS sits behind prefers-reduced-motion: no-preference.
    expect(document.querySelector("style")?.textContent).toContain("@media (prefers-reduced-motion: no-preference)");
  });
});

describe("useEditStylesInfo", () => {
  it("asks once and reads anything but enabled: true as off", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ enabled: true, intensities: ["off", "bold"] })));
    const { result } = renderHook(() => useEditStylesInfo(true));
    expect(result.current).toBeNull();
    await waitFor(() => expect(result.current?.enabled).toBe(true));
    expect(result.current?.intensities).toEqual(["off", "bold"]);
    expect(fetch).toHaveBeenCalledWith("/api/tasks/edit-styles", { cache: "no-store" });
  });

  it("stays quiet until asked", () => {
    vi.stubGlobal("fetch", vi.fn());
    const { result } = renderHook(() => useEditStylesInfo(false));
    expect(result.current).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("falls back to unavailable on a 404", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Not found", { status: 404 })));
    const { result } = renderHook(() => useEditStylesInfo(true));
    await waitFor(() => expect(result.current).toBe(EDIT_STYLES_UNAVAILABLE));
  });
});
