import { act, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import type { AgentStatus } from "@/lib/agent";

import { AgentLimitNote, useAgentLimit } from "./agent-limit";

const status = (extra: Partial<AgentStatus> = {}): AgentStatus => ({
  available: true, reason: null, home_mode: "agent", model: "m", daily_limit: 150, turns_today: 0, daily_retry_after: null, ...extra,
});

function Harness({ agentStatus, error, onReset }: { agentStatus?: AgentStatus | null; error?: unknown; onReset?: () => void }) {
  const limit = useAgentLimit(agentStatus, onReset);
  return (
    <div>
      <button type="button" onClick={() => limit.report(error)}>send</button>
      <button type="button" onClick={() => limit.clearSoft()}>retry</button>
      <span data-testid="blocked">{String(limit.blocksSend)}</span>
      <span data-testid="daily">{String(limit.daily)}</span>
      <AgentLimitNote kind={limit.kind} remaining={limit.remaining} />
    </div>
  );
}

describe("AgentLimitNote", () => {
  it("says when the daily limit resets, or that it does within a day", () => {
    const { rerender } = render(<AgentLimitNote kind="daily" remaining={3 * 3600 + 20} />);
    expect(screen.getByRole("status")).toHaveTextContent("Daily limit reached·Resets in ~3h");
    rerender(<AgentLimitNote kind="daily" remaining={600} />);
    expect(screen.getByRole("status")).toHaveTextContent("Resets in ~10m");
    rerender(<AgentLimitNote kind="daily" remaining={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("Resets within 24 hours");
  });

  it("counts a burst cooldown in seconds and explains the concurrent cap without a timer", () => {
    const { rerender } = render(<AgentLimitNote kind="burst" remaining={12} />);
    expect(screen.getByRole("status")).toHaveTextContent("Too fast·Try again in 12s");
    rerender(<AgentLimitNote kind="concurrent" remaining={8} />);
    expect(screen.getByRole("status")).toHaveTextContent("Finish a reply in another chat before starting a new one");
    expect(screen.getByRole("status")).not.toHaveTextContent("8");
    rerender(<AgentLimitNote kind={null} remaining={null} />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("useAgentLimit", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("blocks Send during a burst cooldown, counts down, then clears", () => {
    render(<Harness agentStatus={status()} error={{ status: 429, code: "AGENT_RATE_LIMITED", retryAfter: 3 }} />);
    expect(screen.getByTestId("blocked")).toHaveTextContent("false");
    act(() => screen.getByText("send").click());
    expect(screen.getByTestId("blocked")).toHaveTextContent("true");
    expect(screen.getByTestId("daily")).toHaveTextContent("false");
    expect(screen.getByRole("status")).toHaveTextContent("Try again in 3s");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(screen.getByRole("status")).toHaveTextContent("Try again in 2s");
    act(() => { vi.advanceTimersByTime(2500); });
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByTestId("blocked")).toHaveTextContent("false");
  });

  it("does not block Send for the concurrent cap and clears when they try again", () => {
    render(<Harness agentStatus={status()} error={{ status: 429, code: "AGENT_CONCURRENT_LIMIT", retryAfter: 5 }} />);
    act(() => screen.getByText("send").click());
    expect(screen.getByRole("status")).toHaveAttribute("data-limit", "concurrent");
    expect(screen.getByTestId("blocked")).toHaveTextContent("false");
    act(() => screen.getByText("retry").click());
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("clears the concurrent note on its own after a short while", () => {
    render(<Harness agentStatus={status()} error={{ status: 429, code: "AGENT_CONCURRENT_LIMIT" }} />);
    act(() => screen.getByText("send").click());
    expect(screen.getByRole("status")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(9000); });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the daily cap from the status at once and asks for a fresh status when it lifts", () => {
    const onReset = vi.fn();
    const { rerender } = render(<Harness agentStatus={status({ turns_today: 150, daily_retry_after: 2 })} onReset={onReset} />);
    expect(screen.getByTestId("daily")).toHaveTextContent("true");
    expect(screen.getByTestId("blocked")).toHaveTextContent("true");
    expect(screen.getByRole("status")).toHaveTextContent("Daily limit reached·Resets in ~2s");
    act(() => { vi.advanceTimersByTime(2500); });
    expect(screen.queryByRole("status")).toBeNull();
    expect(onReset).toHaveBeenCalledTimes(1);

    // A later status at the cap shows it again; one below the cap lifts it.
    rerender(<Harness agentStatus={status({ turns_today: 150, daily_retry_after: 7200 })} onReset={onReset} />);
    expect(screen.getByRole("status")).toHaveTextContent("Resets in ~2h");
    rerender(<Harness agentStatus={status({ turns_today: 10, daily_retry_after: null })} onReset={onReset} />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("leaves plan errors to the upgrade dialog", () => {
    render(<Harness agentStatus={status()} error={{ status: 402, code: "AGENT_TRIAL_USED" }} />);
    act(() => screen.getByText("send").click());
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByTestId("blocked")).toHaveTextContent("false");
  });
});
