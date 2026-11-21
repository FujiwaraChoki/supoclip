import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AIPrompt } from "./ai-prompt";
import { editorRequest } from "./use-editor";
import { draft } from "@/lib/editor/document.test-fixture";
import type { AIEditUsage, EditDocument } from "@/lib/editor/document";
import { ApiRequestError } from "@/lib/api-error";

vi.mock("./use-editor", () => ({ editorRequest: vi.fn() }));
const request = vi.mocked(editorRequest);

beforeEach(() => {
  vi.useFakeTimers();
  request.mockReset();
});
afterEach(() => vi.useRealTimers());

function setup(initialUsage?: AIEditUsage) {
  let current = structuredClone(draft);
  const update = vi.fn((change: (doc: EditDocument) => EditDocument) => { current = change(current); });
  const view = render(<AIPrompt endpoint="/editor" basis="clip.mp4" initialUsage={initialUsage} doc={draft} playhead={0} update={update} undo={vi.fn()} />);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "mute it" } });
  const submit = () => fireEvent.submit(input.closest("form")!);
  return { ...view, submit, update, current: () => current, edit: () => { current = { ...current, volume: 0.5 }; } };
}

function completed() {
  return { status: "completed", changed: true, summary: "Muted the clip.", document: { ...draft, muted: true } };
}

function hangUntilAborted(_url: string, _method?: string, _body?: unknown, signal?: AbortSignal): Promise<never> {
  return new Promise((_, reject) => signal?.addEventListener("abort", () => reject(signal.reason), { once: true }));
}

it("submits once even when two form events arrive before React renders", async () => {
  request.mockImplementation(hangUntilAborted);
  const view = setup();
  await act(async () => { view.submit(); view.submit(); });
  expect(request).toHaveBeenCalledTimes(1);
  expect(request.mock.calls[0][3]).toBeInstanceOf(AbortSignal);
  view.unmount();
});

it("aborts a pending POST on unmount", async () => {
  request.mockImplementation(hangUntilAborted);
  const view = setup();
  await act(async () => view.submit());
  const signal = request.mock.calls[0][3]!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  expect(view.update).not.toHaveBeenCalled();
});

it("ignores a polling response that completes after navigating away", async () => {
  let release!: (value: unknown) => void;
  request.mockResolvedValueOnce({ id: "job" }).mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const view = setup();
  await act(async () => view.submit());
  await act(async () => vi.advanceTimersByTimeAsync(900));
  const signal = request.mock.calls[1][3]!;
  view.unmount();
  await act(async () => release(completed()));
  expect(signal.aborted).toBe(true);
  expect(view.update).not.toHaveBeenCalled();
});

it.each(["POST", "GET"])("times out a stalled %s and allows retry", async (method) => {
  if (method === "GET") request.mockResolvedValueOnce({ id: "job" });
  request.mockImplementation(hangUntilAborted);
  const view = setup();
  await act(async () => view.submit());
  await act(async () => vi.advanceTimersByTimeAsync(180_000));
  expect(screen.getByRole("status")).toHaveTextContent(/took too long/i);
  expect(view.update).not.toHaveBeenCalled();
  request.mockResolvedValueOnce({ id: "retry" }).mockResolvedValueOnce(completed());
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "mute it" } });
  await act(async () => view.submit());
  await act(async () => vi.advanceTimersByTimeAsync(900));
  expect(view.current().muted).toBe(true);
});

it("checks the live draft atomically even before changed props render", async () => {
  request.mockResolvedValueOnce({ id: "job" }).mockResolvedValueOnce(completed());
  const view = setup();
  await act(async () => view.submit());
  view.edit(); // History changed, but React has not rendered its new document yet.
  await act(async () => vi.advanceTimersByTimeAsync(900));
  expect(view.current().volume).toBe(0.5);
  expect(view.current().muted).toBe(false);
  expect(screen.getByText(/edited the clip while the AI worked/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(view.current().muted).toBe(true);
});

it("shows a failed job without changing the draft and can run again", async () => {
  request.mockResolvedValueOnce({ id: "job" }).mockResolvedValueOnce({ status: "failed", error: "Local model unavailable." });
  const view = setup();
  await act(async () => view.submit());
  await act(async () => vi.advanceTimersByTimeAsync(900));
  expect(screen.getByRole("status")).toHaveTextContent("Local model unavailable.");
  expect(view.update).not.toHaveBeenCalled();
  request.mockResolvedValueOnce({ id: "retry" }).mockResolvedValueOnce(completed());
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "mute it" } });
  await act(async () => view.submit());
  await act(async () => vi.advanceTimersByTimeAsync(900));
  expect(view.current().muted).toBe(true);
});

const allowance: AIEditUsage = {
  plan: "pro", limit: 10, used: 9, remaining: 1, resetsAt: "2026-12-05T00:00:00Z", allowed: true,
};

it("shows the last monthly edit, disables submission when spent, and restores failed usage", async () => {
  const spent = { ...allowance, used: 10, remaining: 0, allowed: false };
  request.mockResolvedValueOnce({ id: "job", usage: spent }).mockResolvedValueOnce({
    status: "failed", error: "The AI service is unavailable.", usage: allowance,
  });
  const view = setup(allowance);
  expect(screen.getByText(/1 of 10 AI edits left this month/)).toHaveTextContent(/Resets Dec 5/);
  await act(async () => view.submit());
  expect(screen.getByText(/0 of 10 AI edits left this month/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Upgrade to Creator" })).toHaveAttribute("href", "/settings#plan");
  await act(async () => vi.advanceTimersByTimeAsync(900));
  expect(screen.getByText(/1 of 10 AI edits left this month/)).toBeVisible();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "try again" } });
  expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
});

it("uses server allowance on a concurrent limit rejection and keeps the draft", async () => {
  const spent = { ...allowance, used: 10, remaining: 0, allowed: false };
  request.mockRejectedValueOnce(new ApiRequestError({
    message: "Monthly limit reached.", traceId: null, code: "AI_EDIT_LIMIT_REACHED", detail: { usage: spent },
  }));
  const view = setup(allowance);
  await act(async () => view.submit());
  expect(screen.getByText(/0 of 10 AI edits left this month/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  expect(view.update).not.toHaveBeenCalled();
});

it("refreshes an open exhausted editor when its monthly allowance resets", async () => {
  vi.setSystemTime(new Date("2026-12-04T23:59:59Z"));
  const view = setup({ ...allowance, used: 10, remaining: 0, allowed: false });
  request.mockResolvedValueOnce({ ...allowance, used: 0, remaining: 10, resetsAt: "2027-01-05T00:00:00Z" });
  await act(async () => vi.advanceTimersByTimeAsync(2000));
  expect(screen.getByText(/10 of 10 AI edits left this month/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
  view.unmount();
});

const teamAllowance: AIEditUsage = {
  plan: "team", limit: 300, used: 40, remaining: 260, resetsAt: "2026-12-05T00:00:00Z", allowed: true,
  memberLimit: null, memberUsed: null, memberRemaining: null, memberLimitReached: false,
};

it("tells a team member at their own limit to ask an admin, with no plans link", () => {
  const view = setup({ ...teamAllowance, allowed: false, memberLimit: 25, memberUsed: 25, memberRemaining: 0, memberLimitReached: true });
  expect(screen.getByText(/You've used your 25 AI edits for this month\. Ask a team admin to raise your limit\./)).toHaveTextContent(/Resets Dec 5/);
  expect(screen.queryByText(/of 300 AI edits left/)).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  view.unmount();
});

it("says a paused seat is paused instead of counting the team's pool", () => {
  const view = setup({ ...teamAllowance, allowed: false, memberLimit: 0, memberUsed: 0, memberRemaining: 0, memberLimitReached: true });
  expect(screen.getByText("A team admin paused AI edits for your seat.")).toBeVisible();
  expect(screen.queryByText(/Resets/)).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
  view.unmount();
});

it("counts down whichever runs out first: the member's limit or the team's pool", () => {
  const member = setup({ ...teamAllowance, memberLimit: 25, memberUsed: 20, memberRemaining: 5 });
  expect(screen.getByText(/5 of 25 AI edits left this month/)).toBeVisible();
  member.unmount();
  const pool = setup({ ...teamAllowance, used: 298, remaining: 2, memberLimit: 25, memberUsed: 20, memberRemaining: 5 });
  expect(screen.getByText(/2 of 300 AI edits left this month/)).toBeVisible();
  pool.unmount();
});

it("sends a team whose pool ran out to team settings, not to solo plans", () => {
  const view = setup({ ...teamAllowance, used: 300, remaining: 0, allowed: false });
  expect(screen.getByText(/0 of 300 AI edits left this month/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Team settings" })).toHaveAttribute("href", "/settings/team");
  view.unmount();
});
