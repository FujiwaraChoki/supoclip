import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FeatureTourHost } from "./feature-tour";
import { celebrate } from "@/lib/confetti";

vi.mock("@/lib/confetti", () => ({ celebrate: vi.fn() }));

let account: { createdAt: string; seen: string[] };
let posted: string[];
let endings: string[];

function Page({ busy = false, picker = true, closed = [], onWorkflow, onEnd }: { busy?: boolean; picker?: boolean; closed?: string[]; onWorkflow?: (id: string) => void; onEnd?: () => void }) {
  return (
    <>
      {picker && <div data-tour="workflow-picker">picker</div>}
      {picker && ["highlights", "series", "topic", "recap", "trailer"].map((id) => <button key={id} data-tour={`workflow-${id}`} disabled={closed.includes(id)}>{id}</button>)}
      <button data-tour="instructions">instructions</button>
      <button data-tour="composer-settings">settings</button>
      <FeatureTourHost enabled busy={busy} onWorkflow={onWorkflow} onEnd={onEnd} />
    </>
  );
}

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
  });
  posted = [];
  endings = [];
  account = { createdAt: "2026-01-01T00:00:00Z", seen: [] };
  // jsdom lays nothing out, so everything would look hidden.
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as never);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/feature-tours")) {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        if (body.event === "shown") posted.push(body.id); else endings.push(`${body.id}:${body.event}:${body.step}`);
        return Response.json({ ok: true });
      }
      return Response.json(account);
    }
    return Response.json({});
  }));
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.mocked(celebrate).mockClear(); });

const firstTitle = "Introducing workflows";
const wait = () => act(() => new Promise((resolve) => setTimeout(resolve, 1200)));

describe("FeatureTourHost", () => {
  it("asks first, then explains every workflow on the real page and puts the selection back", async () => {
    const onWorkflow = vi.fn();
    const onEnd = vi.fn();
    render(<Page onWorkflow={onWorkflow} onEnd={onEnd} />);
    await screen.findByText(firstTitle, undefined, { timeout: 2500 });
    // The announcement asks first, as a dialog; nothing on the page changes until they say yes.
    expect(screen.getByRole("dialog", { name: firstTitle })).toBeInTheDocument();
    expect(onWorkflow).not.toHaveBeenCalled();
    expect(posted).toEqual(["workflows"]);

    fireEvent.click(screen.getByRole("button", { name: "Show me 👀" }));
    expect(screen.getByText("1 of 7")).toBeInTheDocument();
    expect(onWorkflow).toHaveBeenLastCalledWith("highlights");
    for (const [id, title] of [["series", "Series"], ["topic", "Topic"], ["recap", "Recap"], ["trailer", "Trailer"]]) {
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
      expect(onWorkflow).toHaveBeenLastCalledWith(id);
    }
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onWorkflow).toHaveBeenLastCalledWith("recap");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("7 of 7")).toBeInTheDocument();
    expect(onEnd).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("7 of 7")).not.toBeInTheDocument();
    expect(onEnd).toHaveBeenCalledOnce();
    expect(posted).toEqual(["workflows"]);
    expect(endings).toEqual(["workflows:completed:7"]);
    expect(celebrate).toHaveBeenCalledWith({ x: expect.any(Number), y: expect.any(Number) });
  });

  it("can be skipped from any step", async () => {
    const onEnd = vi.fn();
    render(<Page onEnd={onEnd} />);
    await screen.findByText(firstTitle, undefined, { timeout: 2500 });
    fireEvent.click(screen.getByRole("button", { name: "Show me 👀" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(screen.queryByText("2 of 7")).not.toBeInTheDocument();
    expect(onEnd).toHaveBeenCalledOnce();
    expect(endings).toEqual(["workflows:dismissed:2"]);
    expect(celebrate).not.toHaveBeenCalled();
  });

  it("can be declined or left at any step", async () => {
    const onEnd = vi.fn();
    render(<Page onEnd={onEnd} />);
    await screen.findByText(firstTitle, undefined, { timeout: 2500 });
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByText(firstTitle)).not.toBeInTheDocument();
    expect(onEnd).toHaveBeenCalledOnce();
    expect(endings).toEqual(["workflows:dismissed:0"]);
    expect(celebrate).not.toHaveBeenCalled();
  });

  it("leaves new accounts alone", async () => {
    account.createdAt = "2031-01-01T00:00:00Z";
    render(<Page />);
    await wait();
    expect(screen.queryByText(firstTitle)).not.toBeInTheDocument();
    expect(posted).toEqual([]);
  });

  it("shows at most one tour a day", async () => {
    const { unmount } = render(<Page />);
    await screen.findByText(firstTitle, undefined, { timeout: 2500 });
    unmount();
    render(<Page />);
    await wait();
    expect(screen.queryByText(firstTitle)).not.toBeInTheDocument();
    expect(posted).toEqual(["workflows"]);
  });

  it("only talks about workflows this account can use", async () => {
    render(<Page closed={["series", "recap"]} />);
    await screen.findByText(firstTitle, undefined, { timeout: 2500 });
    fireEvent.click(screen.getByRole("button", { name: "Show me 👀" }));
    expect(screen.getByText("1 of 5")).toBeInTheDocument();
  });

  it("falls back to the settings tip for accounts without workflows", async () => {
    render(<Page picker={false} />);
    await screen.findByText("Everything else lives here", undefined, { timeout: 2500 });
    expect(screen.queryByText(firstTitle)).not.toBeInTheDocument();
    expect(posted).toEqual(["composer-settings"]);
  });

  it("waits while the user is busy, and leaves without undoing their choice when they start", async () => {
    const onEnd = vi.fn();
    const { rerender } = render(<Page busy onEnd={onEnd} />);
    await wait();
    expect(posted).toEqual([]);
    rerender(<Page onEnd={onEnd} />);
    await screen.findByText(firstTitle, undefined, { timeout: 2500 });
    rerender(<Page busy onEnd={onEnd} />);
    await waitFor(() => expect(screen.queryByText(firstTitle)).not.toBeInTheDocument());
    expect(onEnd).not.toHaveBeenCalled();
    expect(endings).toEqual(["workflows:dismissed:0"]);
  });
});
