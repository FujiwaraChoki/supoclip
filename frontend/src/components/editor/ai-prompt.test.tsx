import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AIPrompt } from "./ai-prompt";
import { editorRequest } from "./use-editor";
import { draft } from "@/lib/editor/document.test-fixture";
import type { EditDocument } from "@/lib/editor/document";

vi.mock("./use-editor", () => ({ editorRequest: vi.fn() }));
const request = vi.mocked(editorRequest);

beforeEach(() => {
  vi.useFakeTimers();
  request.mockReset();
});
afterEach(() => vi.useRealTimers());

function setup() {
  let current = structuredClone(draft);
  const update = vi.fn((change: (doc: EditDocument) => EditDocument) => { current = change(current); });
  const view = render(<AIPrompt endpoint="/editor" basis="clip.mp4" doc={draft} playhead={0} update={update} undo={vi.fn()} />);
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
