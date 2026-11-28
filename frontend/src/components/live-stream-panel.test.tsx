import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { defaultLiveChoice, type LiveChoice, type LiveOffer } from "@/lib/live-clipping";
import { LiveStreamPanel, streamClock } from "./live-stream-panel";

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
  it("says who is live and for how long, and offers the newest parts", () => {
    render(<Harness />);
    const panel = screen.getByTestId("live-stream-panel");
    expect(within(panel).getByRole("heading")).toHaveTextContent("SomeStreamer is live1:32:00");
    const windows = within(panel).getByRole("radiogroup", { name: "How much to clip" });
    expect(within(windows).getAllByRole("radio").map((pick) => pick.textContent)).toEqual(["15 min", "30 min", "1 h", "All"]);
    expect(within(windows).getByRole("radio", { name: "30 min" })).toHaveAttribute("aria-checked", "true");
    expect(within(panel).getByRole("switch", { name: "Keep clipping every 30 min" })).not.toBeChecked();
    expect(within(panel).getByRole("button", { name: "Clip last 30 min" })).toBeEnabled();
  });

  it("keeps clipping when the switch is on", () => {
    const onSubmit = vi.fn();
    const onChange = vi.fn();
    render(<Harness onSubmit={onSubmit} onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: "15 min" }));
    fireEvent.click(screen.getByRole("switch", { name: "Keep clipping every 30 min" }));
    expect(onChange).toHaveBeenLastCalledWith({ windowSeconds: 900, follow: true });
    fireEvent.click(screen.getByRole("button", { name: "Start live clipping" }));
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("clips everything so far when the stream is shorter than a pick", () => {
    render(<Harness offer={{ ...OFFER, elapsedSeconds: 40 * 60 }} />);
    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    expect(screen.getByRole("button", { name: "Clip it all" })).toBeEnabled();
  });

  it("shows other plans one line about Creator instead of the controls", () => {
    const onUpgrade = vi.fn();
    render(<Harness offer={{ ...OFFER, canClipLive: false }} onUpgrade={onUpgrade} />);
    const panel = screen.getByTestId("live-stream-panel");
    expect(panel).toHaveTextContent("Clipping live streams is part of Creator.");
    expect(within(panel).queryByRole("radio")).not.toBeInTheDocument();
    expect(within(panel).queryByRole("switch")).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "See Creator" }));
    expect(onUpgrade).toHaveBeenCalledOnce();
  });

  it("asks to wait when the stream is younger than a minute", () => {
    render(<Harness offer={{ ...OFFER, elapsedSeconds: 20 }} />);
    expect(screen.getByTestId("live-stream-panel")).toHaveTextContent("This stream just started.");
    expect(screen.queryByRole("button", { name: /Clip/ })).not.toBeInTheDocument();
  });

  it("formats the time on air", () => {
    expect(streamClock(59)).toBe("0:59");
    expect(streamClock(24 * 60 + 5)).toBe("24:05");
    expect(streamClock(3600 + 36 * 60 + 12)).toBe("1:36:12");
  });
});
