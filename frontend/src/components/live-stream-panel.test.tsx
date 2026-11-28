import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { defaultLiveChoice, type LiveChoice, type LiveOffer } from "@/lib/live-clipping";
import { LiveStreamPanel } from "./live-stream-panel";

const OFFER: LiveOffer = {
  platform: "twitch",
  channel: "SomeStreamer",
  title: "Sunday stream",
  elapsedSeconds: 92 * 60,
  canClipLive: true,
  maxWindowSeconds: 10800,
  defaultWindowSeconds: 1800,
  followIntervalSeconds: 1800,
};

function Harness({ offer = OFFER, onSubmit = vi.fn(), onUpgrade = vi.fn(), onChange }: {
  offer?: LiveOffer;
  onSubmit?: () => void;
  onUpgrade?: () => void;
  onChange?: (choice: LiveChoice) => void;
}) {
  const [choice, setChoice] = useState(defaultLiveChoice(offer));
  return (
    <LiveStreamPanel
      offer={offer}
      choice={choice}
      onChoiceChange={(next) => { setChoice(next); onChange?.(next); }}
      onSubmit={onSubmit}
      onCancel={vi.fn()}
      onUpgrade={onUpgrade}
      busy={false}
    />
  );
}

describe("LiveStreamPanel", () => {
  it("says the stream is live and offers the newest parts, 30 minutes by default", () => {
    render(<Harness />);
    const panel = screen.getByTestId("live-stream-panel");
    expect(within(panel).getByRole("heading")).toHaveTextContent("SomeStreamer is live");
    expect(panel).toHaveTextContent("Live for 1 h 32 min");
    const picks = within(panel).getAllByRole("radio");
    expect(picks.map((pick) => pick.textContent)).toEqual(["Last 15 min", "Last 30 min", "Last 60 min", "Everything so far"]);
    expect(within(panel).getByRole("radio", { name: "Last 30 min" })).toHaveAttribute("aria-checked", "true");
    expect(panel).toHaveTextContent("Uses about 30 min of video now.");
  });

  it("starts continuous clipping when the switch is on", () => {
    const onSubmit = vi.fn();
    const onChange = vi.fn();
    render(<Harness onSubmit={onSubmit} onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: "Last 15 min" }));
    fireEvent.click(screen.getByRole("switch", { name: "Keep clipping while live" }));
    expect(onChange).toHaveBeenLastCalledWith({ windowSeconds: 900, follow: true });
    expect(screen.getByTestId("live-stream-panel")).toHaveTextContent("Each new batch uses only its new minutes.");
    fireEvent.click(screen.getByRole("button", { name: "Start live clipping" }));
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("shows other plans what Creator adds instead of working controls", () => {
    const onUpgrade = vi.fn();
    render(<Harness offer={{ ...OFFER, canClipLive: false }} onUpgrade={onUpgrade} />);
    const panel = screen.getByTestId("live-stream-panel");
    expect(panel).toHaveTextContent("Clipping live streams is part of the Creator plan.");
    for (const pick of within(panel).getAllByRole("radio")) expect(pick).toBeDisabled();
    expect(within(panel).getByRole("switch")).toBeDisabled();
    expect(within(panel).queryByRole("button", { name: "Clip it now" })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "See Creator" }));
    expect(onUpgrade).toHaveBeenCalledOnce();
  });

  it("asks to wait when the stream is younger than a minute", () => {
    render(<Harness offer={{ ...OFFER, elapsedSeconds: 20 }} />);
    expect(screen.getByTestId("live-stream-panel")).toHaveTextContent("This stream just started.");
    expect(screen.getByRole("button", { name: "Clip it now" })).toBeDisabled();
  });
});
